# Changelog

All notable changes to this project will be documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Transport-gap diagnosis. A profile configured only through `required_env`
  (owner + session, no `NATS_URL` / `NATS_CONTEXT`) failed to start with the
  generic "config validation failed" / "adapter creation failed (check
  dependencies and config)", which read as a missing-SDK problem. Now
  `validate_config` logs a precise, once-per-process warning naming the missing
  transport and stating it is *not* a dependency issue, and the `setup gateway`
  wizard no longer reports such a half-config as "already configured" (it only
  counts a complete config — transport + owner + session — so it walks the user
  through picking a transport instead of defaulting the reconfigure prompt to
  No). `plugin.yaml` now flags `NATS_URL` / `NATS_CONTEXT` as a required
  transport (the XOR can't be expressed in `required_env`, so it stays enforced
  at runtime).
- README profiles section: document that each profile needs its own
  `hermes -p <name> plugins install …` (plugins live per-`HERMES_HOME`), that
  the SDK install is once-per-machine (shared venv) not per-profile, and that
  full configuration uses `hermes -p <name> setup` (LLM keys + NATS), not
  `setup gateway` (NATS-only).

## [0.1.1] - 2026-05-20

### Fixed
- SDK-install gap: `hermes plugins install` git-clones the plugin but does not
  install its Python dependencies. Ship `scripts/install-sdks.sh` and
  `after-install.md`, and correct the README's "pulled in automatically" claim
  so the NATS SDK is actually installed.
- Stabilized the intermittently-flaky
  `tests/test_nats_inbound.py::TestRunTextPromptFallback::test_final_text_skipped_when_deltas_already_streamed`
  (~1/3 of CI matrix jobs). Test-side fix only: the executor is now run inline
  and the assertion checks the streamed texts. Root cause was a CI-runner
  loop-teardown race in the test harness reaching the `_delta_callback`
  shutdown drop branch — not a defect in the transport or hot path.

## [0.1.0] - 2026-05-20

### Added
- Initial standalone extraction of the Hermes NATS gateway plugin from the
  in-tree fork at `synadia-ai/hermes-agent-work` branch `feat/nats-gateway-plugin`.
- `send_exec_approval` adapter hook on `NatsAdapter` for in-band
  dangerous-command approval over the NATS reply inbox. Works on stock
  Hermes >= v0.14.0 via the duck-typed dispatch path in
  `hermes_agent.gateway.run` — no upstream changes required.
- Full user-facing `README.md`: quick install, prerequisites, configure
  (wizard / `.env` / `config.yaml`), profiles, run + verify, dangerous-command
  approval cheatsheet, limitations, troubleshooting, and development.
- CI: `.github/workflows/tests.yml` (ruff + offline pytest on Python
  3.11/3.12/3.13, push + PR) and `.github/workflows/release.yml` (tag-gated
  version verification, `uv build`, and a GitHub Release with sdist + wheel
  attached). No PyPI publish step — PyPI enablement is a deliberate v0.2+
  decision.

### Fixed
- Ruff cleanup across the test suite: removed unused imports and an unused
  local variable, added an explicit `from typing import List` under
  `from __future__ import annotations`, and marked the intentional
  post-`load_adapter()` import as `# noqa: E402`. `ruff check .` is now clean.

### Removed
- Core-PR vendoring in `_approval.py`:
  `dispatch_approval_via_request_interaction`, `get_current_approval_entry_id`,
  `adapter_supports_request_interaction`, and the `_current_approval_entry_id`
  `ContextVar`. Unreachable on stock Hermes and superseded by the new
  `send_exec_approval` adapter hook.
- Removed obsolete regression-guard test
  `tests/gateway/test_nats_no_core_pr_dependency.py`. The Core-PR symbols it
  guarded against (`dispatch_approval_via_request_interaction`,
  `get_current_approval_entry_id`, `adapter_supports_request_interaction`,
  `_current_approval_entry_id`) were removed from `_approval.py` in the Stage B
  pivot, so the regression cannot occur.
