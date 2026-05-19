"""Transport helpers for the NATS plugin's dangerous-command approval flow.

Two pure functions consumed by ``adapter.NatsAdapter.send_exec_approval``:

* :func:`_format_approval_prompt` — render an approval request as the short
  plain-text prompt that ``request_interaction`` ships on the reply inbox.
* :func:`_parse_approval_reply` — map a free-form caller reply
  (``once`` / ``s`` / ``deny`` …) to one of the four canonical choice
  tokens that ``tools.approval.resolve_gateway_approval`` accepts.

Both are transport-agnostic and depend only on stdlib; the actual NATS
query/response and the call into ``resolve_gateway_approval`` live in
``adapter.py`` so the helpers stay easy to unit-test without an event
loop or a live broker.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Approval reply tokens (verbatim from gateway/platforms/base.py:495-504) ──
_APPROVAL_REPLY_ONCE = frozenset(
    {"once", "o", "yes", "y", "ok", "okay", "approve", "approved", "allow", "1"}
)
_APPROVAL_REPLY_SESSION = frozenset({"session", "s"})
_APPROVAL_REPLY_ALWAYS = frozenset(
    {"always", "a", "permanent", "perm", "persist"}
)
_APPROVAL_REPLY_DENY = frozenset(
    {"deny", "d", "no", "n", "nope", "reject", "cancel", "stop", "block", "0"}
)


def _format_approval_prompt(approval_data: Dict[str, Any]) -> str:
    """Render an approval request as a short prompt for ``request_interaction``.

    Shape is intentionally transport-agnostic: plain text, no markdown
    fences that would be miscounted by callers counting code-block state.
    Long commands are truncated to 500 chars so an oversized payload can
    still be acked within the NATS max_payload budget.
    """
    cmd = str(approval_data.get("command") or "")
    desc = str(approval_data.get("description") or "dangerous command")
    if len(cmd) > 500:
        cmd_preview = cmd[:500] + "…"
    else:
        cmd_preview = cmd
    return (
        f"⚠️ Dangerous command requires approval: {desc}\n\n"
        f"Command:\n{cmd_preview}\n\n"
        f"Reply with: once | session | always | deny"
    )


def _parse_approval_reply(reply: Optional[str]) -> str:
    """Map a free-form user reply to the canonical approval choice.

    Returns one of ``"once"`` / ``"session"`` / ``"always"`` / ``"deny"``.
    Unknown / empty / ``None`` replies fall to ``"deny"`` — fail-safe
    matches the "no answer ⇒ blocked" semantic in ``tools/approval.py``.
    """
    if not isinstance(reply, str):
        return "deny"
    normalized = reply.strip().lower()
    if not normalized:
        return "deny"
    token = normalized.split()[0]
    if token in _APPROVAL_REPLY_ALWAYS:
        return "always"
    if token in _APPROVAL_REPLY_SESSION:
        return "session"
    if token in _APPROVAL_REPLY_ONCE:
        return "once"
    if token in _APPROVAL_REPLY_DENY:
        return "deny"
    return "deny"
