"""Shared fixtures for the hermes-nats-gateway test suite.

Distilled from the in-tree Hermes test conftest. Keeps only the hermetic
invariants the NATS plugin tests actually need:

1. **No credential env vars.** All provider/credential-shaped env vars are
   unset before every test.
2. **Isolated HERMES_HOME.** HERMES_HOME points to a per-test tempdir.
3. **Deterministic runtime.** TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0.
4. **Module-state reset for `tools.approval`** — the only Hermes module
   the plugin actually mutates at the module level. Wrapped in try/except
   ImportError so containers without hermes-agent installed degrade
   gracefully.
5. **30-second SIGALRM timeout per test** (Unix-only).
6. **Default event loop** for sync tests that call get_event_loop().

The synadia_ai SDK mock is registered at import time below. It
short-circuits when the real package is installed (the case in PIPE_VENV).
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

# Ensure project root is importable so tests can do `from tests._helpers …`.
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Register the synadia_ai SDK mock at collection time, before any test
# file's module-level imports run. The mock short-circuits when the real
# package is already installed (PIPE_VENV case).
from tests._nats_sdk_mock import _ensure_synadia_agents_mock  # noqa: E402

_ensure_synadia_agents_mock()


def _ensure_nats_platform_registered() -> None:
    """Register the NATS platform with stock Hermes's platform registry.

    Stock Hermes's ``Platform`` enum accepts ``Platform("nats")`` only when
    the value is either a bundled plugin discovered via filesystem scan
    (``plugins/platforms/<name>/``) or runtime-registered via
    ``platform_registry``. The standalone repo doesn't sit under stock
    Hermes's ``plugins/platforms/``, so register at runtime.

    Idempotent — repeat calls are a no-op.
    """
    try:
        from gateway.platform_registry import platform_registry, PlatformEntry
    except ImportError:
        return
    if platform_registry.is_registered("nats"):
        return

    from types import SimpleNamespace
    from tests._helpers import load_adapter

    nats_mod = load_adapter()

    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)

    nats_mod.register(SimpleNamespace(register_platform=_capture))
    if not captured:
        return
    kwargs = captured[0]
    # ``PlatformEntry`` requires ``source``; the plugin's
    # ``register_platform`` injects it on the real path. Mirror that here.
    kwargs.setdefault("source", "plugin")
    kwargs.setdefault("plugin_name", "hermes-nats-gateway")
    try:
        entry = PlatformEntry(**kwargs)
    except TypeError:
        # If ``PlatformEntry`` rejects ``transport_authed`` on this stock
        # version, the plugin's ``register()`` already retries — but the
        # capture path collected the first kwargs. Drop the field and
        # retry.
        kwargs.pop("transport_authed", None)
        entry = PlatformEntry(**kwargs)
    platform_registry.register(entry)


_ensure_nats_platform_registered()


# ── Credential env-var filter ──────────────────────────────────────────────

_CREDENTIAL_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
    "_ACCESS_KEY",
    "_SECRET_ACCESS_KEY",
    "_PRIVATE_KEY",
    "_OAUTH_TOKEN",
    "_WEBHOOK_SECRET",
    "_ENCRYPT_KEY",
    "_APP_SECRET",
    "_CLIENT_SECRET",
    "_CORP_SECRET",
    "_AES_KEY",
)

_CREDENTIAL_NAMES = frozenset({
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
})


def _looks_like_credential(name: str) -> bool:
    if name in _CREDENTIAL_NAMES:
        return True
    return any(name.endswith(suf) for suf in _CREDENTIAL_SUFFIXES)


# HERMES_* / NATS_* / GATEWAY_* behavioral vars that the NATS suite needs
# unset on a clean test start.
_HERMES_BEHAVIORAL_VARS = frozenset({
    "HERMES_HOME_MODE",
    "HERMES_SESSION_PLATFORM",
    "HERMES_SESSION_CHAT_ID",
    "HERMES_SESSION_CHAT_NAME",
    "HERMES_SESSION_THREAD_ID",
    "HERMES_SESSION_SOURCE",
    "HERMES_SESSION_KEY",
    "HERMES_GATEWAY_SESSION",
    "HERMES_KANBAN_DB",
    "HERMES_KANBAN_BOARD",
    "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_LOGS_ROOT",
    "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_WORKSPACE",
    "GATEWAY_ALLOWED_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
    "NATS_URL",
    "NATS_USER",
    "NATS_PASSWORD",
    "NATS_CREDS",
    "NATS_TOKEN",
    "NATS_TLS_CA",
    "NATS_TLS_CERT",
    "NATS_TLS_KEY",
    "NATS_NAME",
    "NATS_PLATFORM",
    "NATS_ALLOWED_USERS",
    "NATS_ALLOW_ALL_USERS",
    "NATS_BASE_TOPIC",
    "NATS_AGENT_NAME",
})


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path, monkeypatch):
    """Blank credential/behavioral env vars; redirect HERMES_HOME per test."""
    for name in list(os.environ.keys()):
        if _looks_like_credential(name):
            monkeypatch.delenv(name, raising=False)

    for name in _HERMES_BEHAVIORAL_VARS:
        monkeypatch.delenv(name, raising=False)

    fake_hermes_home = tmp_path / "hermes_test"
    fake_hermes_home.mkdir()
    (fake_hermes_home / "sessions").mkdir()
    (fake_hermes_home / "cron").mkdir()
    (fake_hermes_home / "memories").mkdir()
    (fake_hermes_home / "skills").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(fake_hermes_home))

    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_METADATA_SERVICE_TIMEOUT", "1")
    monkeypatch.setenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Clear module-level mutable state and ContextVars between tests.

    Stock Hermes' tools.approval module carries process-global state that
    leaks across tests. Reset on every test entry. Degrades gracefully if
    hermes-agent isn't installed.
    """
    logging.disable(logging.NOTSET)
    for _logger_name in ("tools", "run_agent", "hermes_cli"):
        _logger = logging.getLogger(_logger_name)
        _logger.disabled = False
        _logger.setLevel(logging.NOTSET)
        _logger.propagate = True

    try:
        from tools import approval as _approval_mod
        _approval_mod._session_approved.clear()
        _approval_mod._session_yolo.clear()
        _approval_mod._permanent_approved.clear()
        _approval_mod._pending.clear()
        _approval_mod._gateway_queues.clear()
        _approval_mod._gateway_notify_cbs.clear()
        _approval_mod._approval_session_key.set("")
    except ImportError:
        pass
    except Exception:
        pass

    yield


def _timeout_handler(signum, frame):
    raise TimeoutError("Test exceeded 30 second timeout")


@pytest.fixture(autouse=True)
def _ensure_current_event_loop(request):
    """Provide a default event loop for sync tests that call get_event_loop()."""
    if request.node.get_closest_marker("asyncio") is not None:
        yield
        return

    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if loop is None and sys.version_info < (3, 12):
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            loop = None

    created = loop is None or loop.is_closed()
    if created:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        yield
    finally:
        if created and loop is not None:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)


@pytest.fixture(autouse=True)
def _enforce_test_timeout():
    """Kill any individual test that takes longer than 30 seconds."""
    if sys.platform == "win32":
        yield
        return
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


@pytest.fixture()
def tmp_dir(tmp_path):
    """Provide a temporary directory that is cleaned up automatically."""
    return tmp_path


@pytest.fixture()
def mock_config():
    """Return a minimal hermes config dict suitable for unit tests."""
    return {
        "model": "test/mock-model",
        "toolsets": ["terminal", "file"],
        "max_turns": 10,
        "terminal": {
            "backend": "local",
            "cwd": "/tmp",
            "timeout": 30,
        },
        "compression": {"enabled": False},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "command_allowlist": [],
    }


# ── Plugin-adapter anti-pattern guard (simplified for standalone) ──────────
#
# In the in-tree layout, two plugins could each insert their dir on sys.path
# and race for sys.modules["adapter"]. In the standalone repo there is only
# one adapter, but a bare `import adapter` at module level would still
# shadow the load_adapter() helper. Catch that one shape at collection.

import ast  # noqa: E402

_TESTS_DIR = Path(__file__).resolve().parent


def _scan_for_bare_adapter_import(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    offenses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "adapter":
                    offenses.append(
                        f"line {node.lineno}: bare `import adapter` — "
                        "use tests._helpers.load_adapter() instead."
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "adapter" and node.level == 0:
                offenses.append(
                    f"line {node.lineno}: bare `from adapter import …` — "
                    "use tests._helpers.load_adapter() instead."
                )
    return offenses


def pytest_configure(config):
    """Reject bare adapter imports in test files at collection time."""
    if hasattr(config, "workerinput"):
        return
    violations: list[str] = []
    for path in _TESTS_DIR.rglob("test_*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "adapter" not in source:
            continue
        offenses = _scan_for_bare_adapter_import(source)
        if offenses:
            violations.append(
                f"  {path.relative_to(_TESTS_DIR.parent)}:\n    "
                + "\n    ".join(offenses)
            )
    if violations:
        raise pytest.UsageError(
            "Bare `adapter` import detected in tests:\n" + "\n".join(violations)
        )
