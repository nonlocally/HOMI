#!/usr/bin/env bash
# Cards: set/show/clear by name and self, roster precedence over titles, and
# the key property — a card keyed by sessionId survives resume/name churn.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
STATE="$(mktemp -d)"; trap 'rm -rf "$STATE"' EXIT
mkdir -p "$STATE/cfg/sessions" "$STATE/cfg/projects/proj" "$STATE/comm"
export CLAUDE_CONFIG_DIR="$STATE/cfg" COMM_STATE="$STATE/comm"
unset CLAUDE_CODE_MESSAGING_SOCKET
fails=0; ok(){ echo "  ok  $*"; }; fail(){ echo "  FAIL $*"; fails=$((fails+1)); }

sidecar() { # <name> <sessionId> <socket>
  python3 - "$STATE/cfg/sessions" "$@" <<'P'
import json, os, sys, time, zlib
d, name, sid, sock = sys.argv[1:5]
n = 3000000 + zlib.crc32((name+sid).encode()) % 900000
json.dump({"pid": os.getppid(), "sessionId": sid, "cwd": "/tmp/w", "startedAt": int(time.time()*1000),
           "version": "test", "peerProtocol": 1, "kind": "interactive", "entrypoint": "test",
           "messagingSocketPath": sock, "name": name, "status": "idle"},
          open(os.path.join(d, "%d.json" % n), "w"))
P
}

echo "1) set by name; show; roster shows card"
sidecar agent-a sidA /tmp/cc-a.sock
"$CLI" card set agent-a --what "photonics solver" --ask-me-for "FDTD runs" >/dev/null 2>&1 || fail "card set errored"
"$CLI" card show agent-a 2>/dev/null | grep -q '"what": "photonics solver"' && ok "show" || fail "show"
"$CLI" agents | grep agent-a | grep -q "photonics solver -- ask me for: FDTD runs" && ok "roster shows card" || fail "roster: $("$CLI" agents | grep agent-a)"

echo "2) precedence: card beats title; clear falls back to title"
python3 - "$STATE/cfg/projects/proj/sidA.jsonl" <<'P'
import json, sys
open(sys.argv[1], "a").write(json.dumps({"type":"custom-title","customTitle":"Some chat title","sessionId":"sidA"}, separators=(",",":")) + "\n")
P
"$CLI" agents | grep agent-a | grep -q "photonics solver" && ok "card wins over title" || fail "precedence"
"$CLI" agents --json | python3 -c "import json,sys; r=[x for x in json.load(sys.stdin) if x['name']=='agent-a'][0]; assert r['description_source']=='card' and r['title']=='Some chat title'" && ok "json provenance + title kept" || fail "json provenance"
"$CLI" card clear agent-a >/dev/null 2>&1
"$CLI" agents | grep agent-a | grep -q "Some chat title" && ok "clear falls back to title" || fail "fallback"

echo "3) self resolution via the session socket env"
sidecar me-x sidME /tmp/cc-me.sock
CLAUDE_CODE_MESSAGING_SOCKET=/tmp/cc-me.sock "$CLI" card set self --ask-me-for "reviews" >/dev/null 2>&1 || fail "self set errored"
"$CLI" card show me-x 2>/dev/null | grep -q '"ask_me_for": "reviews"' && ok "self resolved to own sidecar" || fail "self"
"$CLI" card set self --what x >/dev/null 2>&1; [ $? -eq 3 ] && ok "self without env dies cleanly" || fail "self-no-env exit"

echo "4) THE key property: card survives resume (same sessionId, new name/pid)"
rm "$STATE/cfg/sessions/"*.json
sidecar me-y7 sidME /tmp/cc-me2.sock     # resumed: new hex name, same sessionId
"$CLI" agents | grep me-y7 | grep -q "ask me for: reviews" && ok "card followed the sessionId across rename/resume" || fail "resume persistence: $("$CLI" agents | grep me-y7)"

echo "5) unknown target dies"
"$CLI" card set ghost --what x >/dev/null 2>&1; [ $? -eq 3 ] && ok "unknown -> exit 3" || fail "unknown target exit"

[ "$fails" -eq 0 ] && echo "PASS: cards — set/show/clear, precedence, self, resume-stable" || { echo "FAIL: $fails"; exit 1; }
