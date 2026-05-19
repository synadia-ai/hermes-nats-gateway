"""Approval-flow contract tests for the NATS plugin.

Plugin-focused subset of the in-tree hermes-agent /approve and /deny suite:

* ``TestBlockingGatewayApproval`` — verifies the stock ``tools.approval``
  contract the plugin's ``send_exec_approval`` depends on. Only the
  two-arg ``resolve_gateway_approval(session_key, choice)`` shape is
  asserted; the ``entry_id=`` kwarg landed in a later Core PR and is
  not part of stock v0.14.0 (the pivot's locked surface).
* ``TestSendExecApprovalPaths`` — five end-to-end paths through
  ``NatsAdapter.send_exec_approval`` driven by a mocked PromptStream.
  Models the same shape as Stage B's Layer-3 smoke.

Upstream tests for the ``GatewayRunner`` ``/approve`` / ``/deny`` slash
commands, the ``check_all_command_guards`` worker-thread flow, and the
no-callback fallback path are not ported — they exercise stock Hermes,
not the plugin, and remain green inside hermes-agent's own test suite.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig

from tests._nats_sdk_mock import _ensure_synadia_agents_mock  # noqa: F401
from tests._helpers import load_adapter

_nats_adapter = load_adapter()
NatsAdapter = _nats_adapter.NatsAdapter


def _clear_approval_state():
    """Reset all module-level approval state between tests."""
    from tools import approval as mod
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    mod._session_approved.clear()
    mod._permanent_approved.clear()
    mod._pending.clear()


# ------------------------------------------------------------------
# Blocking gateway approval infrastructure (tools/approval.py contract)
# ------------------------------------------------------------------


class TestBlockingGatewayApproval:
    """Pin the two-arg ``resolve_gateway_approval`` contract on stock v0.14.0."""

    def setup_method(self):
        _clear_approval_state()

    def test_register_and_resolve_unblocks_entry(self):
        """resolve_gateway_approval signals the entry's event."""
        from tools.approval import (
            register_gateway_notify, unregister_gateway_notify,
            resolve_gateway_approval, has_blocking_approval,
            _ApprovalEntry, _gateway_queues,
        )
        session_key = "test-session"
        register_gateway_notify(session_key, lambda d: None)

        entry = _ApprovalEntry({"command": "rm -rf /"})
        _gateway_queues.setdefault(session_key, []).append(entry)

        assert has_blocking_approval(session_key) is True

        def resolve():
            time.sleep(0.1)
            resolve_gateway_approval(session_key, "once")

        t = threading.Thread(target=resolve)
        t.start()
        resolved = entry.event.wait(timeout=5)
        t.join()

        assert resolved is True
        assert entry.result == "once"
        unregister_gateway_notify(session_key)

    def test_resolve_returns_zero_when_no_pending(self):
        from tools.approval import resolve_gateway_approval
        assert resolve_gateway_approval("nonexistent", "once") == 0

    def test_resolve_all_unblocks_multiple_entries(self):
        """resolve_gateway_approval with resolve_all=True signals all entries."""
        from tools.approval import (
            resolve_gateway_approval, _ApprovalEntry, _gateway_queues,
        )
        session_key = "test-all"
        e1 = _ApprovalEntry({"command": "cmd1"})
        e2 = _ApprovalEntry({"command": "cmd2"})
        e3 = _ApprovalEntry({"command": "cmd3"})
        _gateway_queues[session_key] = [e1, e2, e3]

        count = resolve_gateway_approval(session_key, "session", resolve_all=True)
        assert count == 3
        assert all(e.event.is_set() for e in [e1, e2, e3])
        assert all(e.result == "session" for e in [e1, e2, e3])

    def test_resolve_single_pops_oldest_fifo(self):
        """resolve_gateway_approval without resolve_all resolves oldest first."""
        from tools.approval import (
            resolve_gateway_approval,
            _ApprovalEntry, _gateway_queues,
        )
        session_key = "test-fifo"
        e1 = _ApprovalEntry({"command": "first"})
        e2 = _ApprovalEntry({"command": "second"})
        _gateway_queues[session_key] = [e1, e2]

        count = resolve_gateway_approval(session_key, "once")
        assert count == 1
        assert e1.event.is_set()
        assert e1.result == "once"
        assert not e2.event.is_set()
        assert len(_gateway_queues[session_key]) == 1

    def test_unregister_signals_all_entries(self):
        """unregister_gateway_notify signals all waiting entries to prevent hangs."""
        from tools.approval import (
            register_gateway_notify, unregister_gateway_notify,
            _ApprovalEntry, _gateway_queues,
        )
        session_key = "test-cleanup"
        register_gateway_notify(session_key, lambda d: None)

        e1 = _ApprovalEntry({"command": "cmd1"})
        e2 = _ApprovalEntry({"command": "cmd2"})
        _gateway_queues[session_key] = [e1, e2]

        unregister_gateway_notify(session_key)
        assert e1.event.is_set()
        assert e2.event.is_set()

    def test_clear_session_denies_and_signals_all_entries(self):
        """clear_session must wake blocked entries during boundary cleanup."""
        from tools.approval import clear_session, _ApprovalEntry, _gateway_queues

        session_key = "test-boundary-cleanup"
        e1 = _ApprovalEntry({"command": "cmd1"})
        e2 = _ApprovalEntry({"command": "cmd2"})
        _gateway_queues[session_key] = [e1, e2]

        clear_session(session_key)

        assert e1.event.is_set()
        assert e2.event.is_set()
        assert e1.result == "deny"
        assert e2.result == "deny"
        assert session_key not in _gateway_queues


# ------------------------------------------------------------------
# NatsAdapter.send_exec_approval — plugin-side coverage
# ------------------------------------------------------------------


def _valid_extra(**overrides) -> dict:
    base = {
        "servers": ["nats://127.0.0.1:4222"],
        "owner": "rene",
        "session_name": "default",
        "ack_keepalive_interval_s": 1,
    }
    base.update(overrides)
    return base


def _build_adapter() -> NatsAdapter:
    return NatsAdapter(PlatformConfig(enabled=True, extra=_valid_extra()))


def _fake_stream(reply_text: str | None = None, *, raises=None) -> MagicMock:
    stream = MagicMock()
    stream.send = AsyncMock()
    if raises is not None:
        stream.ask = AsyncMock(side_effect=raises)
    else:
        reply = MagicMock()
        reply.prompt = reply_text
        stream.ask = AsyncMock(return_value=reply)
    return stream


class TestSendExecApprovalPaths:
    """End-to-end paths through ``send_exec_approval`` against a mocked stream.

    Each test installs a fake ``resolve_gateway_approval`` on the canonical
    ``tools.approval`` module — that is the import path the adapter resolves
    inside ``_drive_approval`` (``from tools.approval import …`` is local
    to the coroutine, so the live attribute lookup wins).
    """

    @pytest.mark.asyncio
    async def test_happy_path_once(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="once")
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

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert result.success is True
        assert resolved == [("agent:main:nats:dm:alice", "once")]
        stream.ask.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deny_path(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="deny")
        adapter._active_streams[("alice", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        await adapter.send_exec_approval(
            chat_id="alice",
            command="rm -rf /etc",
            session_key="agent:main:nats:dm:alice",
            description="wipe etc",
        )

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:alice", "deny")]

    @pytest.mark.asyncio
    async def test_timeout_falls_back_to_deny(self, monkeypatch):
        adapter = _build_adapter()
        query_timeout_cls = sys.modules["synadia_ai.agents"].QueryTimeout
        stream = _fake_stream(raises=query_timeout_cls("no reply"))
        adapter._active_streams[("alice", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        await adapter.send_exec_approval(
            chat_id="alice",
            command="kill -9 1",
            session_key="agent:main:nats:dm:alice",
            description="kill init",
        )

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:alice", "deny")]

    @pytest.mark.asyncio
    async def test_unknown_reply_falls_back_to_deny(self, monkeypatch):
        adapter = _build_adapter()
        stream = _fake_stream(reply_text="huh?")
        adapter._active_streams[("alice", id(stream))] = stream

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        await adapter.send_exec_approval(
            chat_id="alice",
            command="dd if=/dev/zero",
            session_key="agent:main:nats:dm:alice",
            description="disk wipe",
        )

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:alice", "deny")]

    @pytest.mark.asyncio
    async def test_no_stream_returns_success_immediately_and_denies(self, monkeypatch):
        """No stream registered: dispatch still returns SendResult(success=True)
        (so the gateway's 15-s deadline in _nats_approval_notify passes), and
        the scheduled _drive_approval task lands a "deny" because
        request_interaction maps no-stream → None → "deny" via _parse_approval_reply.
        """
        adapter = _build_adapter()  # No stream registered.

        resolved: list[tuple[str, str]] = []

        def _fake_resolve(session_key, choice, resolve_all=False):
            resolved.append((session_key, choice))
            return 1

        import tools.approval as approval_mod
        monkeypatch.setattr(approval_mod, "resolve_gateway_approval", _fake_resolve)

        result = await adapter.send_exec_approval(
            chat_id="ghost",
            command="rm -rf /",
            session_key="agent:main:nats:dm:ghost",
            description="recursive delete",
        )

        # send_exec_approval reports dispatch success synchronously — even
        # when no stream exists. The reply resolution path runs as a task.
        assert result.success is True

        for _ in range(10):
            await asyncio.sleep(0)
            if resolved:
                break

        assert resolved == [("agent:main:nats:dm:ghost", "deny")]
