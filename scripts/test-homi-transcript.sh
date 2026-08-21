#!/usr/bin/env bash
# Session view test: the read-only transcript surface on the talk page.
# A fake session registry + projects dir (CLAUDE_CONFIG_DIR) stand in for
# Claude's own state; the module resolves name -> live session -> transcript,
# parses records into renderable turns (filtering machine plumbing), serves
# them token-gated at /api/session/<target>, and the talk page grows tabs.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-tsc.XXXXXX)"
export COMM_STATE="$T/state"
export HOMI_SOCK_DIR="$T/socks"
export HOMI_SESSIONS_DIR="$T/cc/sessions"
export CLAUDE_CONFIG_DIR="$T/cc"
export HOMI_SELF="tschost"
export HOMI_TICK=1
export HOMI_BOARD_DIR="$T/board"
mkdir -p "$HOMI_SESSIONS_DIR" "$T/cc/projects/-fake-proj"

pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
SRVPID=""; PORT=""
kill_server(){ [ -n "$SRVPID" ] || return 0
               pkill -f "board --serve $PORT" 2>/dev/null
               pkill -P "$SRVPID" 2>/dev/null; kill "$SRVPID" 2>/dev/null
               wait "$SRVPID" 2>/dev/null; SRVPID=""; }
cleanup(){ kill_server; "$COMM" homi stop >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

echo "== fixtures: registry with collisions + a transcript with every record kind"
touch "$T/dummy.sock"
python3 - "$T" $$ <<'PY'
import json, os, sys
t, pid = sys.argv[1], int(sys.argv[2])
sess = os.path.join(t, "cc", "sessions")
def w(fn, d):
    with open(os.path.join(sess, fn), "w") as f:
        json.dump(d, f)
# two live sessions claim "scout" -> newest startedAt must win
w("100.json", {"name": "scout", "pid": pid, "sessionId": "ses-scout-old",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 1000,
               "kind": "interactive"})
w("101.json", {"name": "scout", "pid": pid, "sessionId": "ses-scout-new",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 2000,
               "kind": "interactive"})
# dead pid -> never resolves
w("102.json", {"name": "ghost", "pid": 99999999, "sessionId": "ses-ghost",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 3000})
proj = os.path.join(t, "cc", "projects", "-fake-proj")
def rec(**kw):
    return json.dumps(kw)
lines = [
    rec(type="user", timestamp="2026-08-21T10:00:00.000Z",
        message={"role": "user", "content": "hello there"}),
    rec(type="assistant", timestamp="2026-08-21T10:00:05.000Z",
        message={"role": "assistant", "content": [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "thinking about it"},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "ls", "description": "List files in dir"}}]}),
    rec(type="user", timestamp="2026-08-21T10:00:06.000Z",
        message={"role": "user", "content": [
            {"type": "tool_result", "content": "giant tool output"}]}),
    rec(type="user", timestamp="2026-08-21T10:00:07.000Z",
        message={"role": "user",
                 "content": "<system-reminder>machine noise</system-reminder>"}),
    rec(type="assistant", timestamp="2026-08-21T10:00:08.000Z",
        message={"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/a/b/code.py", "old_string": "x",
                       "new_string": "y"}}]}),
    rec(type="summary", summary="the earlier arc, compacted"),
    rec(type="custom-title", customTitle="scout", sessionId="ses-scout-new"),
    rec(type="user", timestamp="2026-08-21T10:00:09.000Z",
        message={"role": "user", "content":
                 '<cross-session-message from="uds:/x" from-name="aadarwal">\n'
                 'ping from phone\n</cross-session-message>\n\n'
                 'This came from another Claude session boilerplate.'}),
    rec(type="assistant", timestamp="2026-08-21T10:00:10.000Z",
        message={"role": "assistant", "content": [
            {"type": "text", "text": "done."}]}),
]
with open(os.path.join(proj, "ses-scout-new.jsonl"), "w") as f:
    f.write("\n".join(lines) + "\n")
# decoy transcript for the old session id
with open(os.path.join(proj, "ses-scout-old.jsonl"), "w") as f:
    f.write(rec(type="user", message={"role": "user", "content": "decoy"}) + "\n")
PY
ok "fixtures written"

echo "== module: resolution"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
p = ht.find_transcript('scout')
print(p[0] if p else None)")"
if printf '%s' "$r" | grep -q "ses-scout-new.jsonl"; then
  ok "name -> newest live session -> transcript path (collision resolved)"
else bad "resolution (got: $r)"; fi
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
print(ht.find_transcript('ghost'))")"
if [ "$r" = "None" ]; then ok "dead pid never resolves"; else bad "dead pid (got: $r)"; fi
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
print(ht.find_transcript('nobody'))")"
if [ "$r" = "None" ]; then ok "unknown name resolves to None"; else bad "unknown name (got: $r)"; fi

echo "== module: parsing (filter the plumbing, keep the conversation)"
seq="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
r = ht.turns_for('scout')
for u in r['turns']:
    print(u['role'] + '|' + u['text'].replace(chr(10), ' '))")"
expect="user|hello there
assistant|thinking about it
tool|\$ List files in dir
tool|✎ code.py
mark|· context compacted ·
user|ping from phone
assistant|done."
if [ "$seq" = "$expect" ]; then ok "turn sequence exact: text kept, thinking/tool_result/reminders/title dropped"
else bad "turn sequence (got: $seq)"; fi

echo "== module: incremental append, cursor semantics (one warm process)"
r="$(python3 -c "
import json, sys
sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
a = ht.turns_for('scout')
last = a['turns'][-1]['i']
path, _ = ht.find_transcript('scout')
with open(path, 'a') as f:
    f.write(json.dumps({'type': 'assistant',
                        'timestamp': '2026-08-21T10:00:20.000Z',
                        'message': {'role': 'assistant', 'content': [
                            {'type': 'text', 'text': 'later.'}]}}) + chr(10))
b = ht.turns_for('scout', after=last)
print(len(b['turns']), b['turns'][-1]['text'] if b['turns'] else '-')")"
if [ "$r" = "0 -" ]; then
  bad "incremental after-cursor (append invisible: $r)"
elif printf '%s' "$r" | grep -q "^1 later.$"; then
  ok "append re-read; after=<last> returns exactly the new turn"
else bad "incremental after-cursor (got: $r)"; fi

echo "== server: token-gated session API on the talk page"
"$COMM" homi start >/dev/null 2>&1
"$COMM" homi init --handle alice >/dev/null 2>&1
"$COMM" homi claim scout >/dev/null 2>&1
PORT=$((18921 + RANDOM % 500))
"$COMM" homi board --serve "$PORT" --bind 127.0.0.1 --no-remote >/dev/null 2>&1 &
SRVPID=$!
up=""
for i in $(seq 1 20); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/state.json" >/dev/null 2>&1 && { up=1; break; }
  sleep 0.5
done
[ -n "$up" ] && ok "server up" || bad "server up"
page="$(curl -sf -m 5 "http://127.0.0.1:$PORT/talk/scout" 2>/dev/null)"
TOKEN="$(printf '%s' "$page" | grep -o '"token": *"[a-f0-9]*"' | head -1 | grep -o '[a-f0-9]\{16,\}')"
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/session/scout")"
if [ "$c" = "403" ]; then ok "session API without token refused (403)"; else bad "no-token refused (got $c)"; fi
r="$(curl -s -m 5 -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/session/scout")"
n="$(printf '%s' "$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("turns") or []), d.get("ok"), d.get("live"))' 2>/dev/null)"
if [ "$n" = "8 True True" ]; then ok "session API serves the parsed turns (live:true)"
else bad "session API turns (got: $n / $(printf '%s' "$r" | head -c 120))"; fi
r="$(curl -s -m 5 -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/session/nobody")"
n="$(printf '%s' "$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ok"), d.get("live"), len(d.get("turns") or []))' 2>/dev/null)"
if [ "$n" = "True False 0" ]; then ok "no live session -> honest live:false, empty"
else bad "no-session shape (got: $n)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/session/scout@remote")"
if [ "$c" = "400" ]; then ok "qualified target refused honestly (session view is local-only)"
else bad "qualified target (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/session/..%2fetc")"
if [ "$c" = "400" ]; then ok "invalid name refused (no traversal)"
else bad "invalid name (got $c)"; fi

echo "== the talk page grows tabs"
if printf '%s' "$page" | grep -q 'data-tab="session"' && printf '%s' "$page" | grep -q 'api/session'; then
  ok "talk page carries the session tab + its poll wiring"
else bad "talk page tabs"; fi
if [ "$(printf '%s' "$page" | grep -c -e 'https\?://' -e 'url(' -e '@import' -e '<link' -e 'src=')" = "0" ]; then
  ok "talk page still self-contained"
else bad "talk page self-contained"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" = "0" ]
