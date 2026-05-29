#!/usr/bin/env bash
# Install the opencode CLI and wire it to the Xiaomi MiMo API for use as the
# xaxiu-swarm "opencode" backend.
#
# What it does
#   1. Installs the `opencode` binary if it is not already on PATH
#      (npm → curl install script → bun, whichever is available).
#   2. Writes/merges a MiMo (OpenAI-compatible) provider into the GLOBAL opencode
#      config (~/.config/opencode/opencode.json) using opencode's {env:MIMO_API_KEY}
#      substitution, so no secret is written to disk.
#   3. Warms up opencode (one-time DB migration) so subsequent non-interactive
#      `opencode run` invocations produce clean stdout.
#
# The xaxiu-swarm `opencode` backend also auto-generates its own managed config,
# so this script is OPTIONAL for the backend — but it makes `opencode` usable
# standalone (e.g. `opencode run -m mimo/mimo-v2.5-pro "..."`) and pre-migrates
# the DB. Re-running is safe (idempotent).
#
# Usage:
#   bash scripts/install_opencode.sh
#   export MIMO_API_KEY=sk-...   # pay-as-you-go (sk-…) or Token Plan (tp-…) key
#   opencode run -m mimo/mimo-v2.5-pro "say hi"
#   xaxiu-swarm dispatch packet.md --backend opencode
set -euo pipefail

MIMO_BASE_URL="${MIMO_BASE_URL:-https://api.xiaomimimo.com/v1}"
MIMO_MODEL="${MIMO_MODEL:-mimo-v2.5-pro}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
CONFIG_FILE="$CONFIG_DIR/opencode.json"

log() { printf '[install_opencode] %s\n' "$*"; }

# --- 1. install opencode if missing -----------------------------------------
if command -v opencode >/dev/null 2>&1; then
  log "opencode already installed: $(opencode --version 2>/dev/null || echo '?') ($(command -v opencode))"
else
  log "opencode not found — installing..."
  if command -v npm >/dev/null 2>&1; then
    log "installing via npm (opencode-ai)"
    npm install -g opencode-ai
  elif command -v curl >/dev/null 2>&1; then
    log "installing via opencode.ai install script"
    curl -fsSL https://opencode.ai/install | bash
  elif command -v bun >/dev/null 2>&1; then
    log "installing via bun (opencode-ai)"
    bun add -g opencode-ai
  else
    log "ERROR: need one of npm / curl / bun to install opencode" >&2
    exit 1
  fi
  command -v opencode >/dev/null 2>&1 || {
    log "ERROR: opencode still not on PATH after install. Add its bin dir to PATH." >&2
    exit 1
  }
  log "installed: $(opencode --version 2>/dev/null || echo '?')"
fi

# --- 2. write/merge the MiMo provider into the global config -----------------
mkdir -p "$CONFIG_DIR"
log "wiring MiMo provider into $CONFIG_FILE (baseURL=$MIMO_BASE_URL)"
MIMO_BASE_URL="$MIMO_BASE_URL" CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import json, os
from pathlib import Path

cfg_file = Path(os.environ["CONFIG_FILE"])
base_url = os.environ["MIMO_BASE_URL"]

cfg = {}
if cfg_file.exists():
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except ValueError:
        # Don't clobber an unparseable config — back it up and start fresh.
        backup = cfg_file.with_suffix(".json.bak")
        cfg_file.replace(backup)
        print(f"[install_opencode] existing config was invalid JSON; backed up to {backup}")
        cfg = {}

cfg.setdefault("$schema", "https://opencode.ai/config.json")
providers = cfg.setdefault("provider", {})
providers["mimo"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Xiaomi MiMo",
    "options": {"baseURL": base_url, "apiKey": "{env:MIMO_API_KEY}"},
    "models": {
        "mimo-v2.5-pro": {"name": "MiMo V2.5 Pro",
                          "limit": {"context": 1048576, "output": 131072}},
        "mimo-v2.5": {"name": "MiMo V2.5"},
        "mimo-v2-pro": {"name": "MiMo V2 Pro"},
        "mimo-v2-flash": {"name": "MiMo V2 Flash"},
    },
}
cfg_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print(f"[install_opencode] provider 'mimo' written; models: {sorted(providers['mimo']['models'])}")
PY

# --- 3. warm up (one-time DB migration; validates provider parses) -----------
log "warming up opencode (one-time DB migration + provider check)"
if opencode models mimo >/dev/null 2>&1; then
  log "provider OK — 'opencode models mimo' lists: $(opencode models mimo 2>/dev/null | tr '\n' ' ')"
else
  log "WARN: 'opencode models mimo' did not list models; check $CONFIG_FILE" >&2
fi

cat <<EOF

[install_opencode] Done. Next steps:
  export MIMO_API_KEY=sk-...            # your Xiaomi MiMo API key
  opencode run -m mimo/${MIMO_MODEL} "say hi"
  xaxiu-swarm dispatch packet.md --backend opencode
EOF
