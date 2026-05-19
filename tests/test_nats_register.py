"""Stage 4: pins the NATS plugin's ``register()`` contract.

Two invariants:

1. ``register()`` calls ``ctx.register_platform`` with the kwargs the
   gateway needs to wire the NATS plugin (name, label, env-name pair,
   setup_fn callable, install_hint mentioning both SDKs + nkeys, emoji).

2. ``register()`` feature-detects ``transport_authed=True`` — on stock
   upstream where ``PlatformEntry`` lacks that field, it retries without
   the kwarg. This is the master plan's Dependency Point B and must not
   regress (the Core PR / Stage 6 will add the field; until then the
   plugin has to survive on plain upstream).
"""

from __future__ import annotations

from types import SimpleNamespace

from tests._nats_sdk_mock import _ensure_synadia_agents_mock  # noqa: F401
from tests._helpers import load_adapter

_nats_mod = load_adapter()


def test_register_shape():
    """``register()`` passes the expected kwargs to ``register_platform``."""
    calls: list[dict] = []

    def fake_register_platform(**kwargs):
        calls.append(kwargs)

    ctx = SimpleNamespace(register_platform=fake_register_platform)
    _nats_mod.register(ctx)

    assert len(calls) == 1, "register() must call register_platform exactly once"
    kwargs = calls[0]

    assert kwargs["name"] == "nats"
    assert kwargs["label"] == "NATS"
    assert kwargs["allow_all_env"] == "NATS_ALLOW_ALL_USERS"
    assert kwargs["allowed_users_env"] == "NATS_ALLOWED_USERS"
    assert callable(kwargs["setup_fn"])
    assert kwargs["setup_fn"] is _nats_mod.interactive_setup
    assert kwargs["emoji"] == "🛰️"

    # install_hint must mention both Synadia SDKs and nkeys (per
    # adapter.py:2191) so a fresh operator gets a single copy-pasteable
    # pip command rather than a partial install.
    install_hint = kwargs["install_hint"]
    assert "synadia-ai-agents" in install_hint
    assert "synadia-ai-agent-service" in install_hint
    assert "nkeys" in install_hint


def test_register_transport_authed_is_feature_detected():
    """Pins Dependency Point B — register() must retry without
    ``transport_authed`` when the upstream ``PlatformEntry`` rejects it.
    """
    calls: list[dict] = []

    def fake_register_platform(**kwargs):
        if "transport_authed" in kwargs:
            raise TypeError(
                "PlatformEntry() got an unexpected keyword argument 'transport_authed'"
            )
        calls.append(kwargs)

    ctx = SimpleNamespace(register_platform=fake_register_platform)
    _nats_mod.register(ctx)

    # Exactly one successful call landed — the retry.
    assert len(calls) == 1
    assert "transport_authed" not in calls[0]
    assert calls[0]["name"] == "nats"
