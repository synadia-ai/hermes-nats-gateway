"""Top-level conftest: keep pytest from walking the plugin-root package.

The plugin-root ``__init__.py`` is force-included into the wheel as
``hermes_nats_gateway/__init__.py`` and does
``from .adapter import register`` — only valid inside the installed package.
If pytest auto-discovers it (because it sits at rootdir) it tries to import
the file as a top-level module and the relative import fails. Telling pytest
to ignore the plugin's source files at rootdir keeps test collection focused
on ``tests/``.
"""

collect_ignore_glob = ["__init__.py", "adapter.py", "_approval.py"]
