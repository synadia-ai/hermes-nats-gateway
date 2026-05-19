# One more step: install the NATS runtime SDKs

`hermes plugins install` clones this plugin but does **not** install its Python
dependencies (Hermes plugins are distributed as git clones, not pip packages).
The gateway needs three runtime SDKs in the **Hermes virtualenv**, or it will log
`NATS: synadia-ai-agents / synadia-ai-agent-service not installed` at startup and
skip registering the adapter.

Run the bundled installer — it locates the Hermes venv automatically (works for
both the `install.sh` and pip/editable layouts) and installs the SDKs with uv:

```bash
bash ~/.hermes/plugins/nats-platform/scripts/install-sdks.sh
```

That's it. If auto-detection ever fails (a non-standard install), pass your
Hermes venv's `python` explicitly:

```bash
bash ~/.hermes/plugins/nats-platform/scripts/install-sdks.sh /path/to/venv/bin/python
```

Then configure and start the gateway:

```bash
hermes setup          # pick "NATS"
hermes gateway run
```

See the README's **Configure** and **Troubleshooting** sections for details.
