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

echo "== attribution + embedded reminders (fixture extension)"
python3 - "$T" <<'PY'
import json, os, sys
proj = os.path.join(sys.argv[1], "cc", "projects", "-fake-proj")
with open(os.path.join(proj, "ses-scout-new.jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "timestamp": "2026-08-21T10:00:30.000Z",
        "message": {"role": "user", "content":
            '<cross-session-message from="uds:/y" from-name="librarian@peer">\n'
            'hey from the fleet\n</cross-session-message>\n\nboilerplate.'}}) + "\n")
    f.write(json.dumps({"type": "user", "timestamp": "2026-08-21T10:00:31.000Z",
        "message": {"role": "user", "content":
            "real text <system-reminder>machine noise</system-reminder>"}}) + "\n")
PY
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
ts = ht.turns_for('scout')['turns']
a, b = ts[-2], ts[-1]
print(a['role'], a.get('who'), a['text'], '/', b['text'])")"
if [ "$r" = "user librarian@peer hey from the fleet / real text" ]; then
  ok "cross-session sender carried (who), embedded reminder stripped"
else bad "attribution/reminder (got: $r)"; fi

echo "== hostile sender shapes never fail open to the operator's byline"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
def parse(text):
    return ht._parse_record({'type': 'user',
                             'message': {'role': 'user', 'content': text}})
# (a) 200-char from-name: must be truncated, never dropped to who=None
long_name = 'e' * 200
t = parse('<cross-session-message from=\"uds:/x\" from-name=\"' + long_name
          + '\">\nhi\n</cross-session-message>')
a_ok = (len(t) == 1 and t[0].get('who')
        and t[0]['who'] != long_name and len(t[0]['who']) <= 121
        and t[0]['text'] == 'hi')
# (b) wrapper missing from-name entirely: honest unknown, never bare
t = parse('<cross-session-message from=\"uds:/x\">\nhi\n</cross-session-message>')
b_ok = (len(t) == 1 and t[0].get('who') == 'unknown sender')
# (c) opener tag pushed past the 4096 bound but properly closed: the turn
# is machine noise — skipped, and raw wrapper markup never renders
t = parse('<cross-session-message from-name=\"x\" junk=\"' + 'A' * 5000
          + '\">\ninner\n</cross-session-message>')
c_ok = (t == [])
print(a_ok, b_ok, c_ok)")"
if [ "$r" = "True True True" ]; then
  ok "long/absent/oversized senders: truncated, unknown-sender, or skipped — never 'you'"
else bad "hostile sender shapes (got: $r)"; fi

echo "== same-inode same-head growing overwrite is detected"
python3 - "$T" $$ <<'PY'
import json, os, sys
t, pid = sys.argv[1], int(sys.argv[2])
with open(os.path.join(t, "cc", "sessions", "112.json"), "w") as f:
    json.dump({"name": "inplace", "pid": pid, "sessionId": "ses-inplace",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 1,
               "kind": "interactive"}, f)
PY
r="$(python3 -c "
import json, os, sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
t = '$T'
path = os.path.join(t, 'cc', 'projects', '-fake-proj', 'ses-inplace.jsonl')
header = json.dumps({'type': 'user', 'message':
    {'role': 'user', 'content': 'H' * 300}}) + chr(10)   # head lives in line 1
with open(path, 'w') as f:
    f.write(header)
    f.write(json.dumps({'type': 'user', 'message':
        {'role': 'user', 'content': 'old branch'}}) + chr(10))
a = ht.turns_for('inplace')
ino0 = os.stat(path).st_ino
with open(path, 'r+b') as f:   # same inode: in-place divergent overwrite
    f.seek(0)
    f.write(header.encode())
    for i in range(3):
        f.write((json.dumps({'type': 'user', 'message':
            {'role': 'user', 'content': 'new branch %d' % i}}) + chr(10)).encode())
    f.truncate()
b = ht.turns_for('inplace')
texts = [x['text'] for x in b['turns']]
print(os.stat(path).st_ino == ino0, len(b['turns']),
      any('old branch' in x for x in texts),
      sum('new branch' in x for x in texts))")"
if [ "$r" = "True 4 False 3" ]; then
  ok "in-place divergent overwrite reparsed — no stale turns, no silent drops"
else bad "same-inode overwrite (got: $r)"; fi

echo "== window arithmetic, explicit index sets (10-turn fixture)"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
def ids(**kw): return [t['i'] for t in ht.turns_for('scout', **kw)['turns']]
print(ids(after=-1) == list(range(10)),
      ids(before=0) == [],
      ids(before=3, n=2) == [1, 2],
      ids(after=3, before=9) == [4, 5, 6, 7, 8, 9],
      ids(n=99999) == list(range(10)),
      ids(after=999) == [])")"
if [ "$r" = "True True True True True True" ]; then
  ok "after/before boundaries exact (negative, zero, both-cursors, huge n, stale)"
else bad "window arithmetic (got: $r)"; fi

echo "== malformed records never poison the cache"
python3 - "$T" $$ <<'PY'
import json, os, sys
t, pid = sys.argv[1], int(sys.argv[2])
with open(os.path.join(t, "cc", "sessions", "110.json"), "w") as f:
    json.dump({"name": "brute", "pid": pid, "sessionId": "ses-brute",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 1,
               "kind": "interactive"}, f)
proj = os.path.join(t, "cc", "projects", "-fake-proj")
with open(os.path.join(proj, "ses-brute.jsonl"), "w") as f:
    f.write(json.dumps({"type": "user", "message": "not-a-dict"}) + "\n")
    f.write("this line is not json\n")
    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": [1, 2]}]}}) + "\n")
    f.write(json.dumps({"type": "user", "message":
        {"role": "user", "content": "survivor"}}) + "\n")
PY
r="$(python3 -c "
import json, sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
a = ht.turns_for('brute'); b = ht.turns_for('brute')
texts_a = [t['text'] for t in a['turns'] if t['role'] == 'user']
stable = [t['i'] for t in a['turns']] == [t['i'] for t in b['turns']]
path, _ = ht.find_transcript('brute')
with open(path, 'a') as f:
    f.write(json.dumps({'type': 'user', 'message':
        {'role': 'user', 'content': 'after-bad'}}) + chr(10))
c = ht.turns_for('brute', after=b['turns'][-1]['i'])
print('survivor' in texts_a, stable, len(c['turns']), c['turns'][-1]['text'] if c['turns'] else '-')")"
if [ "$r" = "True True 1 after-bad" ]; then
  ok "bad lines skipped, offset advances, repeat calls stable, appends still seen"
else bad "malformed-record poisoning (got: $r)"; fi

echo "== pathological unclosed wrappers parse in bounded time"
r="$(python3 -c "
import json, sys, time; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
t0 = time.time()
for text in ['<cross-session-message ' * 8000,
             '<cross-session-message ' + 'x' * (512 * 1024),
             '<cross-session-message a=b>' + 'x' * (512 * 1024)]:
    ht._parse_record({'type': 'user',
                      'message': {'role': 'user', 'content': text}})
print('fast' if time.time() - t0 < 1.0 else 'slow')")"
if [ "$r" = "fast" ]; then ok "unclosed/half-megabyte wrappers parse under a second"
else bad "pathological wrapper timing (got: $r)"; fi

echo "== replacement by a LONGER file is detected"
python3 - "$T" $$ <<'PY'
import json, os, sys
t, pid = sys.argv[1], int(sys.argv[2])
with open(os.path.join(t, "cc", "sessions", "111.json"), "w") as f:
    json.dump({"name": "swap", "pid": pid, "sessionId": "ses-swap",
               "messagingSocketPath": t + "/dummy.sock", "startedAt": 1,
               "kind": "interactive"}, f)
proj = os.path.join(t, "cc", "projects", "-fake-proj")
with open(os.path.join(proj, "ses-swap.jsonl"), "w") as f:
    f.write(json.dumps({"type": "user", "message":
        {"role": "user", "content": "old world"}}) + "\n")
PY
r="$(python3 -c "
import json, os, sys; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
a = ht.turns_for('swap')
path, _ = ht.find_transcript('swap')
tmp = path + '.tmp'
with open(tmp, 'w') as f:
    for i in range(5):
        f.write(json.dumps({'type': 'user', 'message':
            {'role': 'user', 'content': 'new world %d' % i}}) + chr(10))
os.replace(tmp, path)
b = ht.turns_for('swap')
texts = [t['text'] for t in b['turns']]
print(len(a['turns']), len(b['turns']), any('old world' in x for x in texts))")"
if [ "$r" = "1 5 False" ]; then
  ok "longer replacement reparsed from zero — no spliced history"
else bad "longer-replacement detection (got: $r)"; fi

echo "== collision resolution matches the daemon's chooser (probe parity)"
r="$(python3 -c "
import json, os, socket, sys, threading
sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
import homi
t = '$T'
live = t + '/live.sock'
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(live); srv.listen(8)
held = []
def acceptor():
    while True:
        try:
            c, _ = srv.accept()
            held.append(c)   # hold open: probe's recv must TIME OUT (live)
        except OSError:
            return
threading.Thread(target=acceptor, daemon=True).start()
sess = os.path.join(t, 'cc', 'sessions')
recs = []
for fn, sid, sock, started in [('120.json', 'ses-twin-live', live, 1000),
                               ('121.json', 'ses-twin-dead', t + '/dummy.sock', 2000)]:
    d = {'name': 'twin', 'pid': int(sys.argv[1]), 'sessionId': sid,
         'messagingSocketPath': sock, 'startedAt': started,
         'kind': 'interactive'}
    recs.append(d)
    with open(os.path.join(sess, fn), 'w') as f:
        json.dump(d, f)
proj = os.path.join(t, 'cc', 'projects', '-fake-proj')
for sid in ('ses-twin-live', 'ses-twin-dead'):
    with open(os.path.join(proj, sid + '.jsonl'), 'w') as f:
        f.write(json.dumps({'type': 'user', 'message':
            {'role': 'user', 'content': sid}}) + chr(10))
oracle = homi.Homi._choose_session(recs)['sessionId']
mine = ht.find_session('twin')['sessionId']
srv.close()
print(oracle, mine, oracle == mine)" $$)"
if printf '%s' "$r" | grep -q "True$"; then
  ok "find_session agrees with Homi._choose_session under a dead-socket collision"
else bad "collision parity (got: $r)"; fi

echo "== concurrent readers on distinct transcripts"
r="$(python3 -c "
import sys, threading; sys.path.insert(0, '$HERE/lib')
import homi_transcript as ht
errs = []
def go(name, n):
    try:
        for _ in range(20):
            r = ht.turns_for(name)
            assert r['ok'] and len(r['turns']) == n, (name, len(r['turns']))
    except Exception as e:
        errs.append(repr(e))
a = threading.Thread(target=go, args=('scout', 10))
b = threading.Thread(target=go, args=('brute', 3))
a.start(); b.start(); a.join(); b.join()
print(errs or 'clean')")"
if [ "$r" = "clean" ]; then ok "two threads, two transcripts, no interference"
else bad "concurrency (got: $r)"; fi

echo "== every board-imported homi module ships in all three distribution lists"
r="$(python3 -c "
import re
board = open('$HERE/lib/homi_board.py').read()
mods = sorted(set(re.findall(r'import (homi_[a-z]+)', board)))
missing = []
for src, pat in [('$HERE/lib/homi.py', r'kernel_files = \[[^\]]*\)\]'),
                 ('$HERE/packages/homi/scripts/vendor.mjs', r'const files = \[[^\]]*\]'),
                 ('$HERE/packages/homi/src/cli.ts', r'for \(const f of \[[^\]]*\]')]:
    s = open(src).read()
    m = re.search(pat, s, re.S)
    seg = m.group(0) if m else ''
    for mod in mods:
        if mod + '.py' not in seg:
            missing.append(src.split('/')[-1] + ':' + mod)
print(missing or 'complete')")"
if [ "$r" = "complete" ]; then ok "kernel_files + vendor.mjs + cli.ts all carry every board module"
else bad "distribution lists (missing: $r)"; fi

echo "== server: token-gated session API on the talk page"
"$COMM" homi start >/dev/null 2>&1
"$COMM" homi init --handle alice >/dev/null 2>&1
"$COMM" homi claim scout >/dev/null 2>&1
PORT=$((18921 + RANDOM % 500))
HOMI_BOARD_HOSTS="agents.example.com" \
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
n="$(printf '%s' "$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("turns") or []), d.get("ok"), d.get("live"), d.get("sid"), "cwd" in d)' 2>/dev/null)"
if [ "$n" = "10 True True ses-scout-new False" ]; then
  ok "session API serves the turns + sid, and no cwd disclosure"
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

echo "== a fronting domain can be allowlisted; strangers still cannot"
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: agents.example.com" "http://127.0.0.1:$PORT/state.json")"
if [ "$c" = "200" ]; then ok "HOMI_BOARD_HOSTS host accepted"
else bad "allowlisted host (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: AGENTS.Example.com" "http://127.0.0.1:$PORT/state.json")"
if [ "$c" = "200" ]; then ok "allowlisted host is case-insensitive"
else bad "case-insensitive host (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: evil.example.com" "http://127.0.0.1:$PORT/state.json")"
if [ "$c" = "403" ]; then ok "unlisted host still refused"
else bad "unlisted host (got $c)"; fi

echo "== the talk page grows tabs"
if printf '%s' "$page" | grep -q 'data-tab="session"' && printf '%s' "$page" | grep -q 'api/session'; then
  ok "talk page carries the session tab + its poll wiring"
else bad "talk page tabs"; fi
if printf '%s' "$page" | grep -q '\[hidden\]' && printf '%s' "$page" | grep -A1 '\[hidden\]' | grep -q 'display: *none'; then
  ok "hidden panes actually hide (author [hidden] guard beats the flex rule)"
else bad "hidden guard css"; fi
if printf '%s' "$page" | grep -q 'sSid' && printf '%s' "$page" | grep -q 'session restarted'; then
  ok "client detects a restarted session (sid tracked, pane reset)"
else bad "sid tracking in page"; fi
if printf '%s' "$page" | grep -q 't.who'; then
  ok "client renders foreign senders by name, not as you"
else bad "who byline in page"; fi
if [ "$(printf '%s' "$page" | grep -c -e 'https\?://' -e 'url(' -e '@import' -e '<link' -e 'src=')" = "0" ]; then
  ok "talk page still self-contained"
else bad "talk page self-contained"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" = "0" ]
