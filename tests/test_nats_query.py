"""Mid-stream query & approval round-trip for the NATS adapter.

Covers:

* :meth:`NatsAdapter.request_interaction` — resolves the active
  ``PromptStream`` via the contextvar-first lookup, forwards the prompt
  to ``stream.ask(timeout=…)``, and maps ``QueryTimeout`` / no-stream /
  arbitrary exceptions to ``None``.
* Module-level helpers in the plugin's ``_approval.py``:
  ``_format_approval_prompt`` and ``_parse_approval_reply`` — the pure
  transport-agnostic helpers ``send_exec_approval`` uses.
* :meth:`NatsAdapter.send_exec_approval` — the duck-typed entry point
  stock Hermes prefers over the ``/approve`` fallback. Drives
  ``request_interaction`` then calls ``resolve_gateway_approval`` with
  the two-arg form (no ``entry_id`` on stock v0.14.0).

The SDK is mocked via ``tests/conftest.py::_ensure_synadia_agents_mock``
(short-circuits when real synadia-ai-agents is installed).
No real NATS broker is touched.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig

from tests._nats_sdk_mock import _ensure_synadia_agents_mock  # noqa: F401
from tests._helpers import load_adapter, load_approval

_nats_adapter = load_adapter()
_approval = load_approval()

_format_approval_prompt = _approval._format_approval_prompt
_parse_approval_reply = _approval._parse_approval_reply

NatsAdapter = _nats_adapter.NatsAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_extra(**overrides) -> dict:
    base = {
        "servers": ["nats://127.0.0.1:4222"],
        "owner": "rene",
        "session_name": "default",
        "ack_keepalive_interval_s": 1,
    }
    base.update(overrides)
    return base


def _build_adapter(**extra_overrides) -> NatsAdapter:
    return NatsAdapter(PlatformConfig(enabled=True, extra=_valid_extra(**extra_overrides)))


def _fake_stream(reply_text: str | None = None, *, raises=None) -> MagicMock:
    """Build a PromptStream-shaped MagicMock with an async ``ask``."""
    stream = MagicMock()
    stream.send = AsyncMock()
    if raises is not None:
        stream.ask = AsyncMock(side_effect=raises)
    else:
        reply = MagicMock()
        reply.prompt = reply_text
        stream.ask = AsyncMock(return_value=reply)
    return stream


# ---------------------------------------------------------------------------
# _parse_approval_reply
# ---------------------------------------------------------------------------


class TestParseApprovalReply:
    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("once", "once"),
            ("ONCE", "once"),
            ("yes", "once"),
            ("y", "once"),
            ("ok", "once"),
            ("approve", "once"),
            ("o", "once"),
            ("session", "session"),
            ("S", "session"),
            ("always", "always"),
            ("A", "always"),
            ("permanent", "always"),
            ("deny", "deny"),
            ("no", "deny"),
            ("cancel", "deny"),
            ("reject", "deny"),
        ],
    )
    def test_canonical_mappings(self, reply, expected):
        assert _parse_approval_reply(reply) == expected

    def test_first_token_wins(self):
        assert _parse_approval_reply("yes please") == "once"
        assert _parse_approval_reply("approve this one") == "once"
        assert _parse_approval_reply("session thanks") == "session"
        assert _parse_approval_reply("deny immediately") == "deny"

    def test_none_defaults_to_deny(self):
        assert _parse_approval_reply(None) == "deny"

    def test_empty_string_defaults_to_deny(self):
        assert _parse_approval_reply("") == "deny"
        assert _parse_approval_reply("    ") == "deny"

    def test_unknown_token_defaults_to_deny(self):
        assert _parse_approval_reply("maybe") == "deny"
        assert _parse_approval_reply("hmm") == "deny"
        assert _parse_approval_reply("whatever") == "deny"

    def test_non_string_defaults_to_deny(self):
        assert _parse_approval_reply(42) == "deny"  # type: ignore[arg-type]
        assert _parse_approval_reply(True) == "deny"  # type: ignore[arg-type]
        assert _parse_approval_reply([]) == "deny"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _format_approval_prompt
# ---------------------------------------------------------------------------


class TestFormatApprovalPrompt:
    def test_includes_command_and_description(self):
        out = _format_approval_prompt({
            "command": "rm -rf /tmp/foo",
            "description": "recursive delete",
        })
        assert "rm -rf /tmp/foo" in out
        assert "recursive delete" in out
        assert "once" in out and "session" in out and "always" in out and "deny" in out

    def test_truncates_long_commands(self):
        big = "x" * 2000
        out = _format_approval_prompt({"command": big, "description": "desc"})
        assert len(out) < 1000
        assert "…" in out

    def test_missing_fields_use_safe_defaults(self):
        out = _format_approval_prompt({})
        assert "dangerous command" in out


# ---------------------------------------------------------------------------
# NatsAdapter.request_interaction
# ---------------------------------------------------------------------------


class TestRequestInteraction:
    @pytest.mark.asyncio
    async def test_resolves_stream_and_returns_reply_prompt(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="yes please")
        adapter._active_streams[("alice", id(stream))] = stream

        reply = await adapter.request_interaction(
            chat_id="alice",
            prompt="approve?",
            kind="approval",
            timeout=10.0,
        )

        assert reply == "yes please"
        stream.ask.assert_awaited_once()
        call = stream.ask.await_args
        assert call.args[0] == "approve?"
        assert call.kwargs["timeout"] == 10.0

    @pytest.mark.asyncio
    async def test_prefers_contextvar_over_dict(self):
        adapter = _build_adapter()
        dict_stream = _fake_stream(reply_text="dict")
        ctx_stream = _fake_stream(reply_text="ctx")
        adapter._active_streams[("alice", id(dict_stream))] = dict_stream

        nats_mod = _nats_adapter
        token = nats_mod._current_stream.set(ctx_stream)
        try:
            reply = await adapter.request_interaction(
                chat_id="alice",
                prompt="approve?",
                kind="approval",
                timeout=1.0,
            )
        finally:
            nats_mod._current_stream.reset(token)

        assert reply == "ctx"
        ctx_stream.ask.assert_awaited_once()
        dict_stream.ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stream_returns_none(self):
        adapter = _build_adapter()
        reply = await adapter.request_interaction(
            chat_id="ghost",
            prompt="approve?",
            kind="approval",
            timeout=1.0,
        )
        assert reply is None

    @pytest.mark.asyncio
    async def test_query_timeout_maps_to_none(self):
        adapter = _build_adapter()
        query_timeout_cls = sys.modules["synadia_ai.agents"].QueryTimeout
        stream = _fake_stream(raises=query_timeout_cls("no reply"))
        adapter._active_streams[("alice", id(stream))] = stream

        reply = await adapter.request_interaction(
            chat_id="alice",
            prompt="approve?",
            kind="approval",
            timeout=1.0,
        )
        assert reply is None

    @pytest.mark.asyncio
    async def test_generic_exception_maps_to_none(self):
        adapter = _build_adapter()
        stream = _fake_stream(raises=RuntimeError("broken pipe"))
        adapter._active_streams[("alice", id(stream))] = stream

        reply = await adapter.request_interaction(
            chat_id="alice",
            prompt="approve?",
            kind="approval",
            timeout=1.0,
        )
        assert reply is None


# ---------------------------------------------------------------------------
# NatsAdapter.send_exec_approval (post-Stage-B duck-typed path)
# ---------------------------------------------------------------------------


class TestSendExecApproval:
    @pytest.mark.asyncio
    async def test_schedules_request_interaction_and_resolves_approval(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="session")
        adapter._active_streams[("alice", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        result = await adapter.send_exec_approval(
            chat_id="alice",
            command="rm -rf /tmp/foo",
            session_key="agent:main:nats:dm:alice",
            description="recursive delete",
        )

        # Dispatch returns immediately; reply resolution happens in
        # the scheduled background task. Yield until it lands.
        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert result.success is True
        assert resolved == [("agent:main:nats:dm:alice", "session")]
        stream.ask.assert_awaited_once()
        ask_prompt = stream.ask.await_args.args[0]
        assert "rm -rf /tmp/foo" in ask_prompt
        assert "recursive delete" in ask_prompt

    @pytest.mark.asyncio
    async def test_timeout_reply_resolves_as_deny(self, monkeypatch):
        adapter = _build_adapter()
        query_timeout_cls = sys.modules["synadia_ai.agents"].QueryTimeout
        stream = _fake_stream(raises=query_timeout_cls("no reply"))
        adapter._active_streams[("bob", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        await adapter.send_exec_approval(
            chat_id="bob",
            command="kill -9 1",
            session_key="agent:main:nats:dm:bob",
            description="kill init",
        )

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:bob", "deny")]

    @pytest.mark.asyncio
    async def test_unknown_reply_resolves_as_deny(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="maybe later")
        adapter._active_streams[("carol", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        await adapter.send_exec_approval(
            chat_id="carol",
            command="dd if=/dev/zero",
            session_key="agent:main:nats:dm:carol",
            description="disk copy",
        )

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:carol", "deny")]


# ---------------------------------------------------------------------------
# Sanity: plugin exports the helpers send_exec_approval relies on
# ---------------------------------------------------------------------------


class TestModuleSurface:
    def test_plugin_approval_exports_helpers(self):
        # Guards against a rename silently breaking the plugin's vendored
        # approval helpers.
        assert hasattr(_approval, "_parse_approval_reply")
        assert hasattr(_approval, "_format_approval_prompt")
        # The adapter exposes the duck-typed approval entry point.
        assert hasattr(NatsAdapter, "send_exec_approval")
