"""Phase 2 (T2.2): config parsing for the NATS gateway adapter.

Covers :class:`NatsAdapterSettings.from_extra` happy/bad paths, the
adapter's fatal-error behaviour on bad config, and the env-variable →
``config.extra`` round-trip in :func:`_apply_env_overrides`. The
``_ensure_synadia_agents_mock`` autouse in ``conftest.py`` installs a mock
``synadia_ai.agents`` module so these tests run without the real SDK.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    _apply_env_overrides,
)
from tests._nats_sdk_mock import _ensure_synadia_agents_mock  # noqa: F401
from tests._helpers import load_adapter

_nats_mod = load_adapter()
DEFAULT_ACK_KEEPALIVE_INTERVAL_S = _nats_mod.DEFAULT_ACK_KEEPALIVE_INTERVAL_S
DEFAULT_AGENT = _nats_mod.DEFAULT_AGENT
DEFAULT_ATTACHMENTS_OK = _nats_mod.DEFAULT_ATTACHMENTS_OK
DEFAULT_HEARTBEAT_INTERVAL_S = _nats_mod.DEFAULT_HEARTBEAT_INTERVAL_S
MAX_ACK_KEEPALIVE_INTERVAL_S = _nats_mod.MAX_ACK_KEEPALIVE_INTERVAL_S
NatsAdapter = _nats_mod.NatsAdapter
NatsAdapterSettings = _nats_mod.NatsAdapterSettings
NatsConfigError = _nats_mod.NatsConfigError
check_nats_requirements = _nats_mod.check_nats_requirements

# TestNatsConnectedGate exercises GatewayConfig.get_connected_platforms(),
# which routes "nats" through gateway.platform_registry. Under xdist the
# registry may be empty in a worker that hasn't yet touched any code that
# triggers plugin discovery (e.g. _apply_env_overrides). Register the
# plugin directly so these tests are order-independent across workers.
from gateway.platform_registry import PlatformEntry, platform_registry  # noqa: E402

if not platform_registry.is_registered("nats"):
    class _RegistrationCtx:
        def register_platform(self, **kwargs):
            platform_registry.register(PlatformEntry(**kwargs))

    _ctx = _RegistrationCtx()
    try:
        _nats_mod.register(_ctx)
    except TypeError:
        # Older upstream PlatformEntry rejects ``transport_authed=True`` —
        # the plugin's register() already retries without it, but guard
        # the outer call anyway.
        pass


@pytest.fixture(autouse=True)
def _isolate_nats_env(monkeypatch):
    """Blank NATS_* env vars per-test.

    The plugin's ``validate_config`` / ``is_connected`` read NATS_URL,
    NATS_CONTEXT, HERMES_NATS_OWNER, and HERMES_NATS_SESSION_NAME from
    ``os.environ`` as a config fallback. Without this fixture, developer
    machines with a populated NATS context (the common case for anyone
    using ``hermes setup nats``) see XOR validation fail against the
    test's in-extra ``servers`` configuration. tests/conftest.py's
    ``_hermetic_environment`` blanks credential-shaped vars but not
    NATS_*, and scripts/run_tests.sh's unset list also omits them.
    """
    for name in ("NATS_URL", "NATS_CONTEXT", "HERMES_NATS_AGENT",
                 "HERMES_NATS_OWNER", "HERMES_NATS_SESSION_NAME"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestSettingsFromExtraHappy:
    def test_minimal_servers_config_applies_defaults(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://127.0.0.1:4222"],
                "owner": "rene",
                "session_name": "default",
            }
        )

        assert settings.servers == ["nats://127.0.0.1:4222"]
        assert settings.context is None
        assert settings.agent == DEFAULT_AGENT
        assert settings.owner == "rene"
        assert settings.session_name == "default"
        assert settings.heartbeat_interval_s == DEFAULT_HEARTBEAT_INTERVAL_S
        # max_payload is intentionally None when not set — _on_connect
        # derives it from the broker's negotiated INFO at connect time
        # (PR #41). Hardcoding "1MB" here would have capped a 64MB
        # broker.
        assert settings.max_payload is None
        assert settings.attachments_ok is DEFAULT_ATTACHMENTS_OK
        assert settings.ack_keepalive_interval_s == DEFAULT_ACK_KEEPALIVE_INTERVAL_S

    def test_minimal_context_config_works(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "context": "local-nats",
                "owner": "rene",
                "session_name": "default",
            }
        )

        assert settings.context == "local-nats"
        assert settings.servers is None

    def test_full_config_preserves_all_overrides(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://a:4222", "nats://b:4222"],
                "agent": "hermes",
                "owner": "acme_corp",
                "session_name": "prod-1",
                "heartbeat_interval_s": 15,
                "max_payload": "2MB",
                "attachments_ok": False,
                "ack_keepalive_interval_s": 30,
            }
        )

        assert settings.servers == ["nats://a:4222", "nats://b:4222"]
        assert settings.agent == "hermes"
        assert settings.owner == "acme_corp"
        assert settings.session_name == "prod-1"
        assert settings.heartbeat_interval_s == 15
        assert settings.max_payload == "2MB"
        assert settings.attachments_ok is False
        assert settings.ack_keepalive_interval_s == 30

    def test_servers_string_is_coerced_to_single_url_list(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": "nats://127.0.0.1:4222",
                "owner": "rene",
                "session_name": "default",
            }
        )
        assert settings.servers == ["nats://127.0.0.1:4222"]

    def test_servers_whitespace_is_stripped_and_empty_entries_dropped(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["  nats://a:4222  ", "", "nats://b:4222"],
                "owner": "rene",
                "session_name": "default",
            }
        )
        assert settings.servers == ["nats://a:4222", "nats://b:4222"]

    def test_identity_property_formats_agent_owner_session_name(self):
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://x:4222"],
                "agent": "hermes",
                "owner": "rene",
                "session_name": "default",
            }
        )
        assert settings.identity == "hermes:rene:default"

    def test_case_insensitive_max_payload_accepted(self):
        # The §2.1 size grammar is case-insensitive; callers shouldn't have
        # to match an exact spelling for such a common misconfig.
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://x:4222"],
                "owner": "rene",
                "session_name": "default",
                "max_payload": "4gb",
            }
        )
        assert settings.max_payload == "4gb"

    def test_unset_max_payload_stays_none_for_broker_derivation(self):
        # PR #41: when the user omits max_payload, leave the field unset
        # so _on_connect can derive it from nc.max_payload at connect
        # time. Hardcoding "1MB" here was the bug — it capped 64MB
        # brokers regardless of negotiated capacity.
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://x:4222"],
                "owner": "rene",
                "session_name": "default",
            }
        )
        assert settings.max_payload is None

    def test_blank_max_payload_string_treated_as_unset(self):
        # Empty/whitespace-only is the YAML "I didn't set this" shape.
        settings = NatsAdapterSettings.from_extra(
            {
                "servers": ["nats://x:4222"],
                "owner": "rene",
                "session_name": "default",
                "max_payload": "   ",
            }
        )
        assert settings.max_payload is None


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


class TestSettingsFromExtraBad:
    def test_missing_transport_raises(self):
        with pytest.raises(NatsConfigError, match="exactly one of 'servers'"):
            NatsAdapterSettings.from_extra(
                {"owner": "rene", "session_name": "default"}
            )

    def test_both_servers_and_context_raises(self):
        with pytest.raises(NatsConfigError, match="not both"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "context": "local-nats",
                    "owner": "rene",
                    "session_name": "default",
                }
            )

    def test_empty_servers_list_raises(self):
        with pytest.raises(NatsConfigError, match="at least one non-empty URL"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["   ", ""],
                    "owner": "rene",
                    "session_name": "default",
                }
            )

    def test_servers_wrong_type_raises(self):
        with pytest.raises(NatsConfigError, match="'servers' must be a string or list"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": 42,
                    "owner": "rene",
                    "session_name": "default",
                }
            )

    def test_context_wrong_type_raises(self):
        with pytest.raises(NatsConfigError, match="'context' must be a string"):
            NatsAdapterSettings.from_extra(
                {
                    "context": 123,
                    "owner": "rene",
                    "session_name": "default",
                }
            )

    def test_missing_owner_raises(self):
        with pytest.raises(NatsConfigError, match="'owner' is required"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "session_name": "default",
                }
            )

    def test_missing_session_name_raises(self):
        with pytest.raises(NatsConfigError, match="'session_name' is required"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                }
            )

    def test_owner_non_string_raises(self):
        with pytest.raises(NatsConfigError, match="'owner' must be a string"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": 9000,
                    "session_name": "default",
                }
            )

    def test_invalid_agent_token_raises(self):
        # §2.2 restricts agent to lowercase alphanumeric + hyphens; catch
        # mixed-case or underscores before the SDK's AgentSubject.new().
        with pytest.raises(NatsConfigError, match="'agent'"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "agent": "Hermes_Bot",
                    "owner": "rene",
                    "session_name": "default",
                }
            )

    def test_invalid_max_payload_raises(self):
        with pytest.raises(NatsConfigError, match="'max_payload'"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "max_payload": "one megabyte",
                }
            )

    def test_non_bool_attachments_ok_raises(self):
        with pytest.raises(NatsConfigError, match="'attachments_ok' must be a boolean"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "attachments_ok": "yes",
                }
            )

    def test_non_positive_heartbeat_raises(self):
        with pytest.raises(NatsConfigError, match="'heartbeat_interval_s' must be positive"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "heartbeat_interval_s": 0,
                }
            )

    def test_bool_heartbeat_is_rejected(self):
        # Without an explicit bool guard, Python's int(True) == 1 would
        # silently pass validation.
        with pytest.raises(NatsConfigError, match="'heartbeat_interval_s' must be an integer"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "heartbeat_interval_s": True,
                }
            )

    def test_non_integer_heartbeat_raises(self):
        with pytest.raises(NatsConfigError, match="'heartbeat_interval_s' must be an integer"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "heartbeat_interval_s": "often",
                }
            )

    def test_ack_keepalive_at_protocol_limit_raises(self):
        with pytest.raises(NatsConfigError, match="ack_keepalive_interval_s"):
            NatsAdapterSettings.from_extra(
                {
                    "servers": ["nats://x:4222"],
                    "owner": "rene",
                    "session_name": "default",
                    "ack_keepalive_interval_s": MAX_ACK_KEEPALIVE_INTERVAL_S,
                }
            )


# ---------------------------------------------------------------------------
# Adapter instantiation
# ---------------------------------------------------------------------------


class TestNatsAdapterInit:
    def _pconfig(self, **extra) -> PlatformConfig:
        return PlatformConfig(enabled=True, extra=extra)

    def test_valid_config_no_fatal_error(self):
        adapter = NatsAdapter(
            self._pconfig(
                servers=["nats://127.0.0.1:4222"],
                owner="rene",
                session_name="default",
            )
        )
        assert adapter.has_fatal_error is False
        assert adapter._settings is not None
        assert adapter._settings.owner == "rene"

    def test_invalid_config_sets_fatal_error_nonretryable(self):
        adapter = NatsAdapter(self._pconfig(owner="rene"))
        # No servers / context / session_name — must fail fast.
        assert adapter.has_fatal_error is True
        assert adapter.fatal_error_code == "nats_config_error"
        assert adapter.fatal_error_retryable is False
        assert adapter._settings is None

    def test_active_streams_and_service_fields_are_initialised(self):
        # Phases (3, 4) assume these attributes exist regardless of
        # whether init succeeded — guard against regressions.
        adapter = NatsAdapter(
            self._pconfig(
                servers=["nats://x:4222"],
                owner="rene",
                session_name="default",
            )
        )
        assert adapter._active_streams == {}
        assert adapter._nc is None
        assert adapter._service is None

    @pytest.mark.asyncio
    async def test_get_chat_info_shape(self):
        adapter = NatsAdapter(
            self._pconfig(
                servers=["nats://x:4222"],
                owner="rene",
                session_name="default",
            )
        )
        info = await adapter.get_chat_info("any-session-id")
        assert info == {"name": "any-session-id", "type": "dm"}

    @pytest.mark.asyncio
    async def test_disconnect_before_connect_is_idempotent_no_op(self):
        # ``gateway._safe_adapter_disconnect`` calls ``disconnect()``
        # defensively after failed ``connect()`` attempts and during
        # shutdown — must tolerate being called on a never-connected
        # adapter without raising.
        adapter = NatsAdapter(
            self._pconfig(
                servers=["nats://x:4222"],
                owner="rene",
                session_name="default",
            )
        )
        await adapter.disconnect()
        await adapter.disconnect()  # idempotent


# ---------------------------------------------------------------------------
# Env-variable → config.extra round-trip (complements Phase 1 T1.2)
# ---------------------------------------------------------------------------


class TestNatsEnvOverrides:
    def _nats_env(self, **overrides: str) -> dict[str, str]:
        """Build a clean env dict with only NATS_* / HERMES_NATS_* variables."""
        base = {
            "NATS_URL": "",
            "NATS_CONTEXT": "",
            "HERMES_NATS_AGENT": "",
            "HERMES_NATS_OWNER": "",
            "HERMES_NATS_SESSION_NAME": "",
        }
        base.update(overrides)
        return {k: v for k, v in base.items() if v}

    def test_nats_url_enables_and_populates_servers(self):
        config = GatewayConfig()
        with patch.dict(os.environ, self._nats_env(NATS_URL="nats://127.0.0.1:4222"), clear=True):
            _apply_env_overrides(config)

        assert Platform("nats") in config.platforms
        platform_cfg = config.platforms[Platform("nats")]
        assert platform_cfg.enabled is True
        assert platform_cfg.extra["servers"] == ["nats://127.0.0.1:4222"]

    def test_nats_context_enables_and_populates_context(self):
        config = GatewayConfig()
        with patch.dict(os.environ, self._nats_env(NATS_CONTEXT="local-nats"), clear=True):
            _apply_env_overrides(config)

        platform_cfg = config.platforms[Platform("nats")]
        assert platform_cfg.enabled is True
        assert platform_cfg.extra["context"] == "local-nats"
        assert "servers" not in platform_cfg.extra

    def test_identity_env_vars_populate_extra(self):
        config = GatewayConfig()
        env = self._nats_env(
            NATS_URL="nats://127.0.0.1:4222",
            HERMES_NATS_AGENT="hermes",
            HERMES_NATS_OWNER="rene",
            HERMES_NATS_SESSION_NAME="default",
        )
        with patch.dict(os.environ, env, clear=True):
            _apply_env_overrides(config)

        extra = config.platforms[Platform("nats")].extra
        assert extra["agent"] == "hermes"
        assert extra["owner"] == "rene"
        assert extra["session_name"] == "default"

    def test_identity_only_enables_but_stays_disconnected(self):
        # Decision log 2026-04-21: HERMES_NATS_OWNER alone marks the
        # platform enabled but lacks transport, so get_connected_platforms
        # must still filter it out.
        config = GatewayConfig()
        with patch.dict(os.environ, self._nats_env(HERMES_NATS_OWNER="rene"), clear=True):
            _apply_env_overrides(config)

        assert config.platforms[Platform("nats")].enabled is True
        assert Platform("nats") not in config.get_connected_platforms()

    def test_no_env_vars_leaves_platform_absent(self):
        config = GatewayConfig()
        with patch.dict(os.environ, {}, clear=True):
            _apply_env_overrides(config)
        assert Platform("nats") not in config.platforms


# ---------------------------------------------------------------------------
# get_connected_platforms()
# ---------------------------------------------------------------------------


class TestNatsConnectedGate:
    def test_enabled_with_servers_is_connected(self):
        config = GatewayConfig(
            platforms={
                Platform("nats"): PlatformConfig(
                    enabled=True,
                    extra={"servers": ["nats://x:4222"], "owner": "rene", "session_name": "default"},
                )
            }
        )
        assert Platform("nats") in config.get_connected_platforms()

    def test_enabled_with_context_is_connected(self):
        config = GatewayConfig(
            platforms={
                Platform("nats"): PlatformConfig(
                    enabled=True,
                    extra={"context": "local-nats", "owner": "rene", "session_name": "default"},
                )
            }
        )
        assert Platform("nats") in config.get_connected_platforms()

    def test_enabled_without_transport_is_not_connected(self):
        config = GatewayConfig(
            platforms={
                Platform("nats"): PlatformConfig(
                    enabled=True,
                    extra={"owner": "rene", "session_name": "default"},
                )
            }
        )
        assert Platform("nats") not in config.get_connected_platforms()

    def test_disabled_with_servers_is_not_connected(self):
        config = GatewayConfig(
            platforms={
                Platform("nats"): PlatformConfig(
                    enabled=False,
                    extra={"servers": ["nats://x:4222"], "owner": "rene", "session_name": "default"},
                )
            }
        )
        assert Platform("nats") not in config.get_connected_platforms()


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------


def test_check_nats_requirements_reports_sdk_availability():
    # conftest installs the synadia_ai.agents mock module before any test
    # runs, so the requirements check must report True both in CI (no real
    # SDK) and locally (real SDK installed).
    assert check_nats_requirements() is True


# ---------------------------------------------------------------------------
# Transport-gap diagnostic
# ---------------------------------------------------------------------------


class TestNatsTransportDiagnostic:
    """``validate_config`` emits a precise, once-per-process warning when a
    profile has identity but no (or an ambiguous) transport — the half-config
    a ``required_env``-only setup leaves behind. The registry only logs a
    generic "config validation failed", so this guards the actionable message.
    """

    def _reset(self):
        _nats_mod._transport_diagnostic_emitted = False

    def test_identity_without_transport_warns_about_transport(self, caplog):
        self._reset()
        cfg = PlatformConfig(
            enabled=True, extra={"owner": "rene", "session_name": "one"}
        )
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("WARNING"):
                assert _nats_mod.validate_config(cfg) is False
        msg = caplog.text.lower()
        assert "transport" in msg
        assert "nats_url" in msg and "nats_context" in msg
        # Explicitly distinguishes a config gap from a missing dependency —
        # the misread that sent users chasing the SDK install.
        assert "not a missing dependency" in msg

    def test_both_transports_warns_about_xor(self, caplog):
        self._reset()
        cfg = PlatformConfig(
            enabled=True,
            extra={
                "servers": ["nats://x:4222"],
                "context": "c",
                "owner": "rene",
                "session_name": "one",
            },
        )
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("WARNING"):
                assert _nats_mod.validate_config(cfg) is False
        assert "exactly one" in caplog.text.lower()

    def test_diagnostic_emitted_once_per_process(self, caplog):
        self._reset()
        cfg = PlatformConfig(
            enabled=True, extra={"owner": "rene", "session_name": "one"}
        )
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("WARNING"):
                _nats_mod.validate_config(cfg)
                _nats_mod.validate_config(cfg)
        # The flag suppresses the second emission within a process.
        assert caplog.text.lower().count("no transport") == 1

    def test_unconfigured_profile_stays_silent(self, caplog):
        self._reset()
        cfg = PlatformConfig(enabled=True, extra={})
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("WARNING"):
                assert _nats_mod.validate_config(cfg) is False
        assert caplog.text.strip() == ""
