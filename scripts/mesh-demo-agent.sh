#!/usr/bin/env bash
# scripts/mesh-demo-agent.sh — stand up (or tear down) a lightweight always-on
# "echo agent" on a remote Tailscale device, reachable through the communicate
# router as a native peer, so the Open WebUI orchestrator can relay a message to
# another device and capture the reply. This is a TRANSPORT demo: the far-end
# process is a canned responder (cc_peer respond --loop), not a full Claude —
# but the path (router -> send -> ssh tunnel -> remote peer -> reply -> mailbox)
# is exactly the one a real remote Claude session uses.
#
#   mesh-demo-agent.sh up   [device] [name]
#   mesh-demo-agent.sh down [device] [name]
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="${2:-aadarshs-mac-mini-2}"
NAME="${3:-mesh-mini}"
SOCK="/tmp/cc-socks/$NAME.sock"
SIDECAR="$HOME/.claude/sessions/$NAME-mesh.json"
TPIDF="$HOME/.local/state/communicate/openwebui/$NAME.tunnel.pid"
SSH_OPTS=(-o BatchMode=yes -o ExitOnForwardFailure=yes -o StreamLocalBindUnlink=yes
          -o ServerAliveInterval=15 -o ServerAliveCountMax=3)

note() { printf 'mesh-demo %s\n' "$*" >&2; }

up() {
  mkdir -p /tmp/cc-socks; chmod 700 /tmp/cc-socks
  scp -q -o BatchMode=yes "$ROOT/lib/cc_peer.py" "$DEVICE:/tmp/cc_peer.py" || { note "scp failed"; exit 1; }
  # 1) looping responder on the remote device
  ssh -o BatchMode=yes "$DEVICE" "pkill -f 'cc_peer.py respond --socket $SOCK' 2>/dev/null; \
    rm -f $SOCK; nohup python3 /tmp/cc_peer.py respond --socket $SOCK \
    --reply 'hi back from $NAME on $DEVICE' --name $NAME --loop \
    >/tmp/$NAME-responder.log 2>&1 & echo started" >/dev/null
  # 2) persistent two-way tunnel: reach the responder locally (-L),
  #    carry its replies to our orchestrator mailbox (-R)
  [ -f "$TPIDF" ] && kill "$(cat "$TPIDF")" 2>/dev/null
  # Clear a stale reverse-bind path on the remote (left by a prior tunnel/bridge)
  # so the -R bind below can claim it.
  ssh -o BatchMode=yes "$DEVICE" "rm -f /tmp/cc-socks/orchestrator.sock" 2>/dev/null
  ssh "${SSH_OPTS[@]}" -f -N \
    -L "$SOCK:$SOCK" \
    -R "/tmp/cc-socks/orchestrator.sock:/tmp/cc-socks/orchestrator.sock" \
    "$DEVICE"
  pgrep -f "ssh.*-L $SOCK:$SOCK" | head -1 > "$TPIDF"
  # 3) plant a local sidecar so the router resolves NAME -> forwarded socket
  python3 - "$SOCK" "$SIDECAR" "$NAME" "$DEVICE" <<'PY'
import json, os, sys, time
sock, sc, name, dev = sys.argv[1:5]
now = int(time.time() * 1000)
json.dump({"pid": 990000 + (hash(name) % 9000), "sessionId": name, "cwd": "-",
           "startedAt": now, "messagingSocketPath": sock, "name": name,
           "kind": "interactive", "status": "idle", "peerProtocol": 1,
           "updatedAt": now}, open(sc, "w"), separators=(",", ":"))
PY
  sleep 1
  if "$ROOT/bin/communicate" whereis "$NAME" >/dev/null 2>&1; then
    note "up: '$NAME' is live on $DEVICE and resolvable via the router"
  else
    note "up: started, but '$NAME' not yet resolvable — check the tunnel"
  fi
}

down() {
  [ -f "$TPIDF" ] && kill "$(cat "$TPIDF")" 2>/dev/null; rm -f "$TPIDF"
  rm -f "$SIDECAR" "$SOCK"
  ssh -o BatchMode=yes "$DEVICE" "pkill -f 'cc_peer.py respond --socket $SOCK' 2>/dev/null" >/dev/null 2>&1
  note "down: '$NAME' torn down"
}

case "${1:-up}" in
  up) up;;
  down) down;;
  *) echo "usage: mesh-demo-agent.sh {up|down} [device] [name]" >&2; exit 1;;
esac
