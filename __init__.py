# When force-included into the wheel as ``hermes_nats_gateway/__init__.py``,
# this file is loaded as part of a proper package and ``from .adapter import
# register`` resolves. When pytest discovers this file at the rootdir of the
# source tree (no parent package), the relative import would fail — skip it
# so collection succeeds.
if __package__:
    from .adapter import register  # noqa: F401

__all__ = ["register"]
