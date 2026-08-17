#!/usr/bin/env bash
# openwebui/setup.sh — bring up the orchestrator stack on this host.
#   setup.sh          install anything missing, start everything, print status
#   setup.sh status   show what is running
#   setup.sh stop     stop open-webui + open-terminal (leaves ollama service)
set -o pipefail
STATE="$HOME/.local/state/communicate/openwebui"
SECRETS="$HOME/.local/state/communicate/secrets"
OWUI_PORT=8080; OT_PORT=8000
mkdir -p "$STATE" "$SECRETS"; chmod 700 "$SECRETS"

note() { printf '\033[36msetup\033[0m %s\n' "$*" >&2; }
up()   { curl -sf -o /dev/null -m 3 "$1"; }

pidfile_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_bg() { # name, pidfile, logfile, cmd...
  local name="$1" pf="$2" lf="$3"; shift 3
  pidfile_alive "$pf" && { note "$name already running (pid $(cat "$pf"))"; return 0; }
  nohup "$@" >"$lf" 2>&1 & echo $! > "$pf"; disown 2>/dev/null || true
  note "$name started (pid $(cat "$pf"), log $lf)"
}

case "${1:-start}" in
  stop)
    for s in open-webui open-terminal; do
      pidfile_alive "$STATE/$s.pid" && kill "$(cat "$STATE/$s.pid")" && note "stopped $s"
      rm -f "$STATE/$s.pid"
    done; exit 0;;
  status)
    for s in open-webui open-terminal; do
      pidfile_alive "$STATE/$s.pid" && note "$s: up (pid $(cat "$STATE/$s.pid"))" || note "$s: down"
    done
    up "http://127.0.0.1:11434/api/tags" && note "ollama: up" || note "ollama: down"
    exit 0;;
esac

# --- ollama + model ---
command -v ollama >/dev/null || brew install ollama
brew services list | grep -q '^ollama.*started' || brew services start ollama
for i in $(seq 1 30); do up "http://127.0.0.1:11434/api/tags" && break; sleep 1; done
ollama list | grep -q '^qwen3:14b' || { note "pulling qwen3:14b (large)"; ollama pull qwen3:14b; }

# --- open-terminal (bare metal, localhost, api key) ---
KEYF="$SECRETS/open-terminal.key"
[ -f "$KEYF" ] || { openssl rand -hex 24 > "$KEYF"; chmod 600 "$KEYF"; }
command -v open-terminal >/dev/null || uv tool install open-terminal
start_bg open-terminal "$STATE/open-terminal.pid" "$STATE/open-terminal.log" \
  open-terminal run --host 127.0.0.1 --port "$OT_PORT" --api-key "$(cat "$KEYF")"

# --- open webui (bare metal, localhost) ---
# Pin the session-signing key in $SECRETS, the same way open-terminal's is pinned
# above. Without WEBUI_SECRET_KEY set, open-webui generates one into
# .webui_secret_key in its WORKING DIRECTORY — which is how a signing key once
# ended up committed to this repo. With it set, that file is never written.
WKEY="$SECRETS/webui-secret.key"
[ -f "$WKEY" ] || { openssl rand -hex 32 > "$WKEY"; chmod 600 "$WKEY"; }
command -v open-webui >/dev/null || uv tool install --python 3.12 open-webui
start_bg open-webui "$STATE/open-webui.pid" "$STATE/open-webui.log" \
  env OLLAMA_BASE_URL="http://127.0.0.1:11434" WEBUI_URL="http://127.0.0.1:$OWUI_PORT" \
      WEBUI_SECRET_KEY="$(cat "$WKEY")" \
  open-webui serve --host 127.0.0.1 --port "$OWUI_PORT"

for i in $(seq 1 60); do up "http://127.0.0.1:$OWUI_PORT/health" && break; sleep 2; done
up "http://127.0.0.1:$OWUI_PORT/health" && note "open-webui: ready on :$OWUI_PORT" || note "open-webui: NOT healthy yet — check $STATE/open-webui.log"
up "http://127.0.0.1:$OT_PORT/docs" && note "open-terminal: ready on :$OT_PORT" || note "open-terminal: NOT healthy — check $STATE/open-terminal.log"
