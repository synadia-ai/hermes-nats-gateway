# Changelog

All notable changes to this project will be documented in this file. The format
is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0a1] - 2026-05-19

### Added
- Initial standalone extraction of the Hermes NATS gateway plugin from the
  in-tree fork at `synadia-ai/hermes-agent-work` branch `feat/nats-gateway-plugin`.
- `send_exec_approval` adapter hook on `NatsAdapter` for in-band
  dangerous-command approval over the NATS reply inbox. Works on stock
  Hermes >= v0.14.0 via the duck-typed dispatch path in
  `hermes_agent.gateway.run` — no upstream changes required.

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
