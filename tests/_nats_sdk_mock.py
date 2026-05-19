"""Out-of-conftest home for the synadia/nats SDK mock so it travels with the NATS test files into the §4 resilience-clone copy step.

The reference clone's ``conftest.py`` is upstream and has no synadia/nats SDK
fixture; the mock must register on import so the §4 Stage 4 copy step works
without conftest changes on the reference side. The module-level call at the
bottom fires once per process; the function itself is idempotent.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock


def _ensure_synadia_agents_mock() -> None:
    """Install a minimal synadia_ai.agents mock in sys.modules.

    Idempotent — skips when the real SDK is already imported. Mirrors the
    Telegram/Discord pattern so gateway tests can import the NATS adapter
    module without requiring synadia-ai-agents to be installed.

    Also mocks ``nats`` (nats-py): the adapter calls ``nats.connect(...)``
    directly because the SDK explicitly does NOT own NATS connections
    (callers build the client and hand it to ``AgentService``).
    """
    # Mock each submodule independently so a partial install (e.g. one SDK
    # ships on PyPI before the other) doesn't cause the mock for the
    # already-installed module to clobber the real one. The earlier
    # combined guard would fall through if EITHER module was missing,
    # then unconditionally overwrite ``sys.modules["synadia_ai.agents"]``
    # with a MagicMock — silently breaking any production code path that
    # imported the real SDK in the same process.
    need_client_mock = not (
        "synadia_ai.agents" in sys.modules
        and hasattr(sys.modules["synadia_ai.agents"], "__file__")
    )
    need_agent_service_mock = not (
        "synadia_ai.agent_service" in sys.modules
        and hasattr(sys.modules["synadia_ai.agent_service"], "__file__")
    )

    if not need_client_mock and not need_agent_service_mock:
        return  # Both real SDKs are installed — nothing to mock

    if need_client_mock:
        mod = MagicMock()

        # Context-options helper that the adapter uses for the `context` path
        # (no return shape needed beyond "a dict that splats into nats.connect").
        mod.load_context_options = MagicMock(return_value={"servers": ["nats://stub:4222"]})

        # Real exception classes so ``except sdk.QueryTimeout`` works.
        mod.QueryTimeout = type("QueryTimeout", (Exception,), {})
        mod.ProtocolError = type("ProtocolError", (Exception,), {})

        # Envelope / Attachment / chunk types — pydantic-ish stand-ins.
        class _FakeAttachment:
            def __init__(self, filename: str = "", content: str = ""):
                self.filename = filename
                self.content = content

            def to_bytes(self) -> bytes:
                return b""

            @classmethod
            def from_path(cls, path):
                instance = cls(filename=str(path))
                return instance

            @classmethod
            def from_bytes(cls, filename, data):
                return cls(filename=filename)

        mod.Attachment = _FakeAttachment
        mod.Envelope = MagicMock
        # ResponseChunk / StatusChunk are constructed via kwargs (text=..., status=...).
        # Use simple stand-ins that accept kwargs and remember them — tests assert
        # on ``.text`` / ``.status`` to verify the adapter wrapped outgoing content
        # correctly.
        class _FakeResponseChunk:
            def __init__(self, *, text: str = "", attachments=None):
                self.text = text
                self.attachments = attachments

        class _FakeStatusChunk:
            def __init__(self, *, status: str):
                self.status = status

        mod.ResponseChunk = _FakeResponseChunk
        mod.StatusChunk = _FakeStatusChunk
    else:
        mod = sys.modules["synadia_ai.agents"]

    if need_agent_service_mock:
        # Host-side surface — AgentService / PromptStream / PromptHandler.
        agent_service_mod = MagicMock()
        agent_service_mod.AgentService = MagicMock()
        agent_service_mod.AgentService.return_value.start = AsyncMock()
        agent_service_mod.AgentService.return_value.stop = AsyncMock()
        agent_service_mod.PromptStream = MagicMock()
        agent_service_mod.PromptHandler = MagicMock  # forward-looking; hermes never imports it
    else:
        agent_service_mod = sys.modules["synadia_ai.agent_service"]

    # Register only the modules we actually mocked. Re-anchor the parent
    # package via a MagicMock if missing, but never clobber an already-real
    # submodule with our stand-in.
    parent = sys.modules.get("synadia_ai") or MagicMock()
    parent.agents = mod
    parent.agent_service = agent_service_mod
    sys.modules["synadia_ai"] = parent
    if need_client_mock:
        sys.modules["synadia_ai.agents"] = mod
    if need_agent_service_mock:
        sys.modules["synadia_ai.agent_service"] = agent_service_mod

    # ``nats`` (nats-py) is the connection factory the adapter calls
    # directly. Mock it only if the real package isn't installed — most
    # CI / dev installs have nats-py since it's a transitive dep of
    # synadia-ai-agents itself.
    if "nats" not in sys.modules or not hasattr(sys.modules["nats"], "__file__"):
        nats_mod = MagicMock()
        nats_mod.connect = AsyncMock()
        nats_mod.connect.return_value.close = AsyncMock()
        sys.modules["nats"] = nats_mod


# Register on import so the reference clone (which has no conftest fixture)
# picks up the mock automatically. Idempotent — early-returns when the real
# SDK is installed or the mock is already in place.
_ensure_synadia_agents_mock()
