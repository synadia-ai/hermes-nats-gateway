"""Standalone test loader for the plugin adapter and _approval module.

The working clone's tests/gateway/_plugin_adapter_loader.py handled the
multi-plugin in-tree case (one loader for any plugin). Here we only ever load
ourselves, so the helper is much smaller.

``adapter.py`` does ``from ._approval import …`` (relative import), so it must
be loaded as part of a package. We register a synthetic parent package under a
private name and load both ``adapter`` and ``_approval`` as its submodules.
The public aliases ``hermes_nats_gateway_adapter`` /
``hermes_nats_gateway_approval`` are kept in ``sys.modules`` so tests can refer
to them in ``monkeypatch.setattr`` targets if needed. They deliberately do
not collide with the pip-installed ``hermes_nats_gateway.*`` package, nor with
the runtime ``hermes_plugins.nats_platform.*`` namespace.
"""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "_hermes_nats_gateway_under_test"


def _ensure_synthetic_package() -> ModuleType:
    cached = sys.modules.get(_PKG_NAME)
    if cached is not None:
        return cached
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    pkg.__package__ = _PKG_NAME
    sys.modules[_PKG_NAME] = pkg
    return pkg


def _load_submodule(submodule: str, source: str, public_alias: str) -> ModuleType:
    cached = sys.modules.get(public_alias)
    if cached is not None:
        return cached
    _ensure_synthetic_package()
    full_name = f"{_PKG_NAME}.{submodule}"
    spec = importlib.util.spec_from_file_location(full_name, _PLUGIN_ROOT / source)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    sys.modules[public_alias] = mod
    spec.loader.exec_module(mod)
    return mod


def load_approval() -> ModuleType:
    return _load_submodule("_approval", "_approval.py", "hermes_nats_gateway_approval")


def load_adapter() -> ModuleType:
    # Preload _approval so adapter.py's `from ._approval import …` resolves.
    load_approval()
    return _load_submodule("adapter", "adapter.py", "hermes_nats_gateway_adapter")
