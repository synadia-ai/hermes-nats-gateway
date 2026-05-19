#!/usr/bin/env bash
# Install the NATS runtime SDKs into the Hermes virtualenv.
#
# `hermes plugins install` clones this plugin but does not install its Python
# dependencies, and the Hermes venv is uv-managed (no pip). This script finds
# the venv the `hermes` command runs from and installs the SDKs into it with uv.
#
# Usage:
#   bash install-sdks.sh                 # auto-detect the Hermes venv
#   bash install-sdks.sh /path/to/venv/bin/python   # or pass it explicitly
set -euo pipefail

SDKS=(synadia-ai-agents synadia-ai-agent-service nkeys)

err() { printf 'error: %s\n' "$*" >&2; exit 1; }

# 1. Resolve the Hermes venv's python.
venv_py="${1:-}"
if [ -z "$venv_py" ]; then
  hermes_bin="$(command -v hermes || true)"
  [ -n "$hermes_bin" ] || err "the 'hermes' command is not on your PATH. Install Hermes first, or pass the venv python explicitly: bash install-sdks.sh /path/to/venv/bin/python"
  # The launcher is either a Python console-script (shebang IS the venv python)
  # or a bash wrapper that exec's <venv>/bin/hermes.
  shebang="$(sed -n '1s/^#![[:space:]]*//p' "$hermes_bin" || true)"
  if printf '%s' "$shebang" | grep -q 'python'; then
    venv_py="${shebang%% *}"
  else
    venv_py="$(grep -oE '/[^"]*/bin/hermes' "$hermes_bin" | head -1 | sed 's,/hermes$,/python,' || true)"
  fi
fi
[ -n "$venv_py" ] && [ -x "$venv_py" ] || err "could not locate the Hermes venv python (resolved: '${venv_py:-<empty>}'). Pass it explicitly: bash install-sdks.sh /path/to/venv/bin/python"

# 2. Resolve uv (the Hermes installer puts it on PATH or at ~/.local/bin/uv).
uv_bin="$(command -v uv || true)"
[ -n "$uv_bin" ] || { [ -x "$HOME/.local/bin/uv" ] && uv_bin="$HOME/.local/bin/uv"; }
[ -n "$uv_bin" ] || err "uv not found. Install it (https://docs.astral.sh/uv/) or add ~/.local/bin to PATH."

echo "Hermes venv python: $venv_py"
echo "Installing: ${SDKS[*]}"
"$uv_bin" pip install --python "$venv_py" "${SDKS[@]}"

# 3. Verify the import the gateway gates on.
"$venv_py" -c 'import synadia_ai.agents, synadia_ai.agent_service' \
  && echo "OK — NATS SDKs importable. Now run: hermes setup  &&  hermes gateway run" \
  || err "install reported success but synadia_ai is not importable from $venv_py"
