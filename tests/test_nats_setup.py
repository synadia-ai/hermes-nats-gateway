"""Regression tests for NATS setup-wizard transport transitions."""

from collections.abc import Iterable

import hermes_cli.setup as setup_helpers
import pytest

from tests._helpers import load_adapter


_nats_mod = load_adapter()


def _run_setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: dict[str, str],
    choices: Iterable[int],
    prompt_values: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Run the wizard with deterministic prompts and return env writes."""
    values = dict(existing)
    writes: list[tuple[str, str]] = []
    choice_iter = iter(choices)
    prompt_values = prompt_values or {}

    def save_env_value(name: str, value: str) -> None:
        values[name] = value
        writes.append((name, value))

    monkeypatch.setattr(_nats_mod, "_discover_nats_contexts", lambda: ["prod"])
    monkeypatch.setattr(_nats_mod, "_find_nats_profile_collisions", lambda *_args: [])
    monkeypatch.setattr(setup_helpers, "get_env_value", lambda name: values.get(name))
    monkeypatch.setattr(setup_helpers, "save_env_value", save_env_value)
    monkeypatch.setattr(setup_helpers, "prompt_choice", lambda *_args: next(choice_iter))
    monkeypatch.setattr(setup_helpers, "prompt_yes_no", lambda *_args: True)
    monkeypatch.setattr(
        setup_helpers,
        "prompt",
        lambda label, default="": prompt_values.get(label, default),
    )
    for name in (
        "print_header",
        "print_info",
        "print_success",
        "print_error",
        "print_warning",
    ):
        monkeypatch.setattr(setup_helpers, name, lambda *_args: None)

    _nats_mod.interactive_setup()
    return values, writes


def _configured_url(**overrides: str) -> dict[str, str]:
    values = {
        "NATS_URL": "nats://old.example:4222",
        "NATS_CONTEXT": "",
        "NATS_CREDS": "/run/secrets/old.creds",
        "NATS_NKEY": "",
        "HERMES_NATS_OWNER": "alice",
        "HERMES_NATS_SESSION_NAME": "demo",
    }
    values.update(overrides)
    return values


def test_switching_to_context_clears_url_credentials(monkeypatch: pytest.MonkeyPatch):
    values, writes = _run_setup(
        monkeypatch,
        existing=_configured_url(NATS_NKEY="/run/secrets/stale.nk"),
        choices=[2, 0],
    )

    assert values["NATS_URL"] == ""
    assert values["NATS_CONTEXT"] == "prod"
    assert values["NATS_CREDS"] == ""
    assert values["NATS_NKEY"] == ""
    assert ("NATS_CREDS", "") in writes
    assert ("NATS_NKEY", "") in writes


def test_switching_to_public_demo_clears_url_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    values, writes = _run_setup(
        monkeypatch,
        existing=_configured_url(NATS_NKEY="/run/secrets/stale.nk"),
        choices=[0],
    )

    assert values["NATS_URL"] == "nats://demo.nats.io"
    assert values["NATS_CONTEXT"] == ""
    assert values["NATS_CREDS"] == ""
    assert values["NATS_NKEY"] == ""
    assert ("NATS_CREDS", "") in writes
    assert ("NATS_NKEY", "") in writes


def test_switching_to_custom_url_preserves_compatible_url_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    values, writes = _run_setup(
        monkeypatch,
        existing=_configured_url(),
        choices=[1],
        prompt_values={"NATS server URL": "tls://new.example:4222"},
    )

    assert values["NATS_URL"] == "tls://new.example:4222"
    assert values["NATS_CONTEXT"] == ""
    assert values["NATS_CREDS"] == "/run/secrets/old.creds"
    assert values["NATS_NKEY"] == ""
    assert not {"NATS_CREDS", "NATS_NKEY"}.intersection(name for name, _ in writes)
