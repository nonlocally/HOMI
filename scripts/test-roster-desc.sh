#!/usr/bin/env bash
# The roster's DIR/DESCRIPTION join, across the session lifecycle: fresh spawn
# (no transcript), auto-title, retitle, weird title content, --json. Sandboxed
# via CLAUDE_CONFIG_DIR; no live bus involvement.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
STATE="$(mktemp -d)"; trap 'rm -rf "$STATE"' EXIT
mkdir -p "$STATE/sessions" "$STATE/projects/proj"
export CLAUDE_CONFIG_DIR="$STATE"
fails=0; ok(){ echo "  ok  $*"; }; fail(){ echo "  FAIL $*"; fails=$((fails+1)); }

sidecar() { # <name> <sessionId> <cwd>
  python3 - "$STATE/sessions" "$@" <<'P'
import json, os, sys, time, zlib
d, name, sid, cwd = sys.argv[1:5]
n = 3000000 + zlib.crc32(name.encode()) % 900000
json.dump({"pid": os.getppid(), "sessionId": sid, "cwd": cwd, "startedAt": int(time.time()*1000),
           "version": "test", "peerProtocol": 1, "kind": "interactive", "entrypoint": "test",
           "messagingSocketPath": "/tmp/cc-socks/none-%s.sock" % name, "name": name, "status": "idle"},
          open(os.path.join(d, "%d.json" % n), "w"))
P
}
title() { # <sessionId> <title...>
  python3 - "$STATE/projects/proj" "$@" <<'P'
import json, os, sys
d, sid = sys.argv[1:3]
t = " ".join(sys.argv[3:])
with open(os.path.join(d, sid + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "some-other-record", "x": 1}, separators=(",", ":")) + "\n")
    f.write(json.dumps({"type": "custom-title", "customTitle": t, "sessionId": sid}, separators=(",", ":")) + "\n")
P
}

echo "1) fresh spawn: sidecar, no transcript -> row renders, empty description"
sidecar fresh-a sidA /Users/me/src/alpha
row="$("$CLI" agents --json | python3 -c "import json,sys; print(json.dumps([r for r in json.load(sys.stdin) if r['name']=='fresh-a'][0]))")"
echo "$row" | grep -q '"description": ""' && echo "$row" | grep -q '"dir": "alpha"' && ok "spawn row: dir yes, desc empty" || fail "spawn row: $row"

echo "2) auto-title lands -> description appears"
sidecar titled-b sidB /Users/me/src/beta
title sidB "Widget dashboard exploration"
"$CLI" agents | grep "titled-b" | grep -q "Widget dashboard exploration" && ok "auto-title shown" || fail "auto-title missing"

echo "3) retitle/rename -> NEWEST title wins"
title sidB "Beta payments refactor"
"$CLI" agents | grep "titled-b" | grep -q "Beta payments refactor" && ok "newest title wins" || fail "stale title shown"
"$CLI" agents | grep "titled-b" | grep -q "Widget dashboard" && fail "old title leaked" || ok "old title gone"

echo "4) gnarly title content: newlines + quotes squashed, truncated, table intact"
python3 - "$STATE/projects/proj" <<'P'
import json, os, sys
d = sys.argv[1]
t = 'line one\n\nline "two" with a very very very very very very long tail that should be truncated'
with open(os.path.join(d, "sidB.jsonl"), "a") as f:
    f.write(json.dumps({"type": "custom-title", "customTitle": t, "sessionId": "sidB"}, separators=(",", ":")) + "\n")
P
line="$("$CLI" agents | grep "titled-b")"
case "$line" in *$'\n'*) fail "newline leaked";; *) ok "single line";; esac
echo "$line" | grep -q 'line one line "two"' && ok "squashed content shown" || fail "squash wrong: $line"
[ "$(echo "$line" | grep -c .)" = 1 ] && [ ${#line} -lt 140 ] && ok "truncated sanely (${#line} chars)" || fail "row too long: ${#line}"

echo "5) --json is machine-clean and carries all fields"
"$CLI" agents --json | python3 -c "
import json, sys
rows = json.load(sys.stdin)
assert all(set(r) == {'name','type','via','status','socket','dir','description','description_source','title','card'} for r in rows), 'fields'
assert any(r['name']=='titled-b' and r['description'].startswith('line one') for r in rows), 'content'
print('parsed', len(rows), 'rows')" >/dev/null && ok "--json parses with full fields" || fail "--json broken"

[ "$fails" -eq 0 ] && echo "PASS: roster DIR/DESCRIPTION across the session lifecycle" || { echo "FAIL: $fails"; exit 1; }
