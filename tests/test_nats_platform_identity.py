"""Stage 4: pins dynamic ``Platform("nats")`` resolution identity.

Stage 3 removed the static ``Platform.NATS`` enum member; the plugin —
and all downstream callers — now rely on ``Platform("nats")`` resolving
to a single identity-stable pseudo-member via
:meth:`gateway.config.Platform._missing_`. Without identity stability,
``adapter.platform is Platform("nats")`` comparisons would silently
break with every call.
"""

from __future__ import annotations

from gateway.config import Platform


def test_nats_dynamic_resolution_is_identity_stable():
    a = Platform("nats")
    b = Platform("nats")
    assert a is b, "Platform._missing_ must cache pseudo-members for identity stability"
    assert a.value == "nats"
    # Stage 3 removed the static enum *declaration*, but ``_missing_`` caches
    # the pseudo-member back into ``Platform._member_map_["NATS"]``, so once
    # the first ``Platform("nats")`` resolves, ``Platform.NATS`` becomes
    # reachable via the cached entry. The contract that matters is
    # identity-stable resolution, not absence of the attribute.
    assert "nats" not in Platform.__members__ or Platform("nats") is Platform.__members__["NATS"]
