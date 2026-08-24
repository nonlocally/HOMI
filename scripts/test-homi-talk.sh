#!/usr/bin/env bash
# Talk test: the conversation surface over the fabric's own send/mailbox ops.
# One daemon (@alice), a local agent, the board server with /talk + /api —
# write-path auth (mutation token, custom header, JSON content type, Host
# allowlist), the merged conversation view, and the since filter.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-talk.XXXXXX)"
export COMM_STATE="$T/state"
export HOMI_SOCK_DIR="$T/socks"
export HOMI_SESSIONS_DIR="$T/sessions"
export HOMI_SELF="talkhost"
export HOMI_TICK=1
export HOMI_BOARD_DIR="$T/board"
mkdir -p "$HOMI_SESSIONS_DIR"

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

jget() { python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d[int(k)] if isinstance(d,list) else d[k]
print(d)' "$@" 2>/dev/null; }

echo "== setup: claimed fabric with one agent, server up"
"$COMM" homi start >/dev/null 2>&1
"$COMM" homi init --handle alice >/dev/null 2>&1
"$COMM" homi claim scout >/dev/null 2>&1
PORT=$((18421 + RANDOM % 500))
"$COMM" homi board --serve "$PORT" --bind 127.0.0.1 --no-remote >/dev/null 2>&1 &
SRVPID=$!
up=""
for i in $(seq 1 20); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/state.json" >/dev/null 2>&1 && { up=1; break; }
  sleep 0.5
done
[ -n "$up" ] && ok "server up" || bad "server up"

echo "== the human is a claimed identity"
ag="$("$COMM" homi agents --json 2>/dev/null)"
if printf '%s' "$ag" | grep -q '"alice"'; then
  ok "handle claimed as a local identity (the human's mailbox exists)"
else bad "handle claimed as identity (got: $(printf '%s' "$ag" | head -c 200))"; fi

echo "== the talk page"
page="$(curl -sf -m 5 "http://127.0.0.1:$PORT/talk/scout" 2>/dev/null)"
if [ -n "$page" ]; then ok "/talk/scout serves"; else bad "/talk/scout serves"; fi
if printf '%s' "$page" | grep -q "composer"; then ok "composer present"; else bad "composer present"; fi
TOKEN="$(printf '%s' "$page" | grep -o '"token": *"[a-f0-9]*"' | head -1 | grep -o '[a-f0-9]\{16,\}')"
if [ -n "$TOKEN" ]; then ok "mutation token injected into the page"; else bad "mutation token injected"; fi
n="$(printf '%s' "$page" | grep -c "$TOKEN")"
if [ "$n" = "1" ]; then ok "token appears exactly once"; else bad "token appears exactly once (got $n)"; fi
# Self-contained means NO THIRD-PARTY ORIGIN — not "no <link> tag". The page
# now links its own manifest and icon so a phone can install it; those are
# root-relative and served by this same process. What must never appear is an
# absolute URL, a remote font/style import, or a src= pointing off-origin.
ext="$(printf '%s' "$page" | grep -c -e 'https\?://' -e 'url(' -e '@import')"
offsite="$(printf '%s' "$page" | grep -oE '(href|src)="[^"]*"' | grep -vE '(href|src)="/' | grep -c . )"
if [ "$ext" = "0" ] && [ "$offsite" = "0" ]; then
  ok "talk page self-contained (no third-party origin; own manifest/icon ok)"
else bad "talk page self-contained (external=$ext offsite=$offsite)"; fi

echo "== write-path auth, layer by layer"
send() { curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/api/send" "$@"; }
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"hello from the phone"}')"
if [ "$c" = "200" ]; then ok "full-auth send accepted (200)"; else bad "full-auth send accepted (got $c)"; fi
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "missing token refused (403)"; else bad "missing token refused (got $c)"; fi
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: deadbeefdeadbeef" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "wrong token refused (403)"; else bad "wrong token refused (got $c)"; fi
c="$(send -H "Content-Type: application/json" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "missing X-Homi header refused"; else bad "missing X-Homi refused (got $c)"; fi
c="$(send -H "Content-Type: text/plain" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "non-JSON content type refused"; else bad "non-JSON refused (got $c)"; fi
c="$(send -H "Host: evil.example.com" -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "foreign Host refused (DNS-rebinding pin)"; else bad "foreign Host refused (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: evil.example.com" "http://127.0.0.1:$PORT/state.json")"
if [ "$c" = "403" ]; then ok "foreign Host refused on reads too"; else bad "foreign Host on reads (got $c)"; fi

echo "== the send landed in the fabric with honest attribution"
sleep 1
IN="$COMM_STATE/homi/mail/scout/inbox.jsonl"
if grep -q "hello from the phone" "$IN" 2>/dev/null && grep -q '"from_name": "alice"' "$IN"; then
  ok "message in scout's durable inbox, from_name=alice"
else bad "message in scout's inbox with attribution"; fi
J="$T/board/talk/scout.jsonl"
if [ -f "$J" ] && grep -q "hello from the phone" "$J"; then
  ok "outgoing journaled server-side"
else bad "outgoing journaled (looked at $J)"; fi

echo "== the conversation merges both directions in order"
"$COMM" homi send alice "scout reporting back" --from scout >/dev/null 2>&1
sleep 1
conv="$(curl -sf -m 5 -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/conv/scout" 2>/dev/null)"
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
es=d["entries"]
texts=[(e["dir"], e["text"]) for e in es]
assert ("out","hello from the phone") in texts, texts
assert ("in","scout reporting back") in texts, texts
ts=[e["ts"] for e in es]
assert ts == sorted(ts), ts
' "$conv" 2>/dev/null; then
  ok "conv has out+in entries, time-ordered"
else bad "conv merged view (got: $(printf '%s' "$conv" | head -c 300))"; fi
last="$(printf '%s' "$conv" | python3 -c 'import json,sys; print(json.load(sys.stdin)["entries"][-1]["ts"])' 2>/dev/null)"
conv2="$(curl -sf -m 5 -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/conv/scout?since=$last" 2>/dev/null)"
if [ "$(printf '%s' "$conv2" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["entries"]))' 2>/dev/null)" = "0" ]; then
  ok "since filter returns only newer entries"
else bad "since filter (got: $(printf '%s' "$conv2" | head -c 200))"; fi

echo "== routed tag is honest"
r="$(curl -s -m 5 -X POST "http://127.0.0.1:$PORT/api/send" -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"second"}')"
routed="$(printf '%s' "$r" | jget routed)"
if [ "$routed" = "inbox" ] || [ "$routed" = "live" ]; then
  ok "send returns the daemon's routed verdict ($routed)"
else bad "routed verdict (got: $r)"; fi

echo "== C1: a fleet-qualified from_name never leaks into a bare local thread"
# Plant an arrival that LOOKS like scout but is fleet-qualified (scout@peer,
# via=peer) directly into the human's inbox — the confirmed-spoof shape.
python3 - "$COMM_STATE/homi/mail/alice/inbox.jsonl" <<'PY'
import json, sys, time
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"ts": time.time(), "msg_id": "spoof1",
                        "from": "", "from_name": "scout@peer", "via": "peer",
                        "text": "IMPOSTOR"}) + "\n")
PY
conv="$(curl -sf -m 5 -H "X-Homi-Token: $TOKEN" "http://127.0.0.1:$PORT/api/conv/scout" 2>/dev/null)"
if printf '%s' "$conv" | grep -q "IMPOSTOR"; then
  bad "fleet-qualified arrival leaked into the bare local thread (C1)"
else ok "scout@peer does NOT appear in the bare scout thread (spoof closed)"; fi

echo "== Sec-Fetch-Site: same-origin passes, cross-site refused"
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -H "Sec-Fetch-Site: same-origin" -d '{"to":"scout","text":"sfs ok"}')"
if [ "$c" = "200" ]; then ok "Sec-Fetch-Site same-origin accepted"; else bad "SFS same-origin (got $c)"; fi
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -H "Sec-Fetch-Site: cross-site" -d '{"to":"scout","text":"x"}')"
if [ "$c" = "403" ]; then ok "Sec-Fetch-Site cross-site refused"; else bad "SFS cross-site (got $c)"; fi

echo "== a daemon refusal is a 400 with the honest reason, not a 500"
r="$(curl -s -m 5 -X POST "http://127.0.0.1:$PORT/api/send" -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -w '\n%{http_code}' -d '{"to":"ghostagent","text":"x"}')"
code="$(printf '%s' "$r" | tail -1)"
if [ "$code" = "400" ] && printf '%s' "$r" | grep -qi "unknown identity"; then
  ok "unknown target -> 400 + daemon reason surfaced"
else bad "unknown target 400+reason (got: $r)"; fi

echo "== negative Content-Length does not hang the server"
( curl -s -m 4 -X POST "http://127.0.0.1:$PORT/api/send" -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -H "Content-Length: -1" -d '{}' >/dev/null 2>&1 ) &
BADPID=$!
sleep 0.5
if curl -sf -m 4 "http://127.0.0.1:$PORT/state.json" >/dev/null 2>&1; then
  ok "server still answers while a bad request is in flight"
else bad "server wedged by negative Content-Length"; fi
kill "$BADPID" 2>/dev/null

echo "== the journal is private (0700 dir, 0600 file)"
dm="$(stat -f '%Lp' "$T/board/talk" 2>/dev/null || stat -c '%a' "$T/board/talk" 2>/dev/null)"
fm="$(stat -f '%Lp' "$T/board/talk/scout.jsonl" 2>/dev/null || stat -c '%a' "$T/board/talk/scout.jsonl" 2>/dev/null)"
if [ "$dm" = "700" ] && [ "$fm" = "600" ]; then
  ok "talk dir 0700, journal 0600"
else bad "talk privacy (dir=$dm file=$fm)"; fi

echo "== board rows link to talk"
board="$(curl -sf -m 5 "http://127.0.0.1:$PORT/" 2>/dev/null)"
if printf '%s' "$board" | grep -q 'href = "/talk/" + encodeURIComponent'; then
  ok "board page builds /talk hrefs (link construction present)"
else bad "board page builds /talk hrefs"; fi

echo "== a spoken turn is marked, so the agent knows it is being listened to"
# The skill tells the agent to answer out loud, in one or two sentences, when
# a turn was SPOKEN. Nothing sent that signal: the voice page posted the same
# body as the typed page, so the instruction keyed on a marker the system
# never produced. The page knows (?v=1 is what turns the ear on) -- say so.
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"how much battery","voice":true}')"
sleep 1
IN="$COMM_STATE/homi/mail/scout/inbox.jsonl"
if [ "$c" = "200" ] && grep -q '\[spoken\] how much battery' "$IN" 2>/dev/null; then
  ok "voice:true marks the turn [spoken] in the agent's inbox"
else bad "voice:true marks the turn (http $c)"; fi

if grep -q '\[spoken\] how much battery' "$T/board/talk/scout.jsonl" 2>/dev/null; then
  ok "the human's transcript records it was spoken, same text the agent got"
else bad "transcript records the spoken marker"; fi

c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"scout","text":"typed not spoken"}')"
sleep 1
if [ "$c" = "200" ] && grep -q '"text": "typed not spoken"' "$IN" 2>/dev/null; then
  ok "a typed turn is left alone (no marker)"
else bad "typed turn unmarked (http $c)"; fi

echo "== the page tells the server when the ear is on"
# Grepping the page for the flag is NOT enough, and the first version of this
# test proved it: `voice: earOn` was present and correct-looking, but `earOn`
# is declared in a different IIFE, so the expression would have thrown
# ReferenceError the first time anyone pressed send. A string the page
# contains is not a value the page can compute.
#
# So: pull the real body expression out of the page and EVALUATE it, in a
# scope that has only the globals the browser would have. A free variable
# throws here exactly as it would there.
tp="$(curl -sf -m 5 "http://127.0.0.1:$PORT/talk/scout?v=1" 2>/dev/null)"
printf '%s' "$tp" > "$T/talk-page.html"
if python3 - "$T/talk-page.html" <<'PYEOF'
import re, subprocess, sys, json
page = open(sys.argv[1]).read()
m = re.search(r"body:\s*JSON\.stringify\((\{.*?\})\),", page, re.S)
if not m:
    print("no send body found"); sys.exit(1)
expr = m.group(1)
# Only `target`, `text` and real browser globals may appear. `window.__voice`
# is the published contract; anything else is a free variable.
for ear, want in ((True, True), (False, False)):
    js = ("var target='scout', text='hi';"
          "var window={__voice:{earOn:%s}};"
          "var out=JSON.stringify(%s);"
          "console.log(out);" % ("true" if ear else "false", expr))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    if r.returncode != 0:
        print("body expression does not evaluate:", r.stderr.strip()[:200])
        sys.exit(1)
    got = json.loads(r.stdout.strip())
    if got.get("voice") is not want or got.get("text") != "hi":
        print("wrong payload for earOn=%s: %r" % (ear, got)); sys.exit(1)
# And with no voice module at all (speech unsupported), it must not throw.
js = ("var target='scout', text='hi'; var window={};"
      "console.log(JSON.stringify(%s));" % expr)
r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
if r.returncode != 0 or json.loads(r.stdout.strip()).get("voice") is not False:
    print("throws or mis-reports when speech is unsupported:", r.stderr.strip()[:200])
    sys.exit(1)
PYEOF
then
  ok "send body evaluates, and voice tracks the ear (both ways, and absent)"
else bad "send body is not a computable expression"; fi

echo "== the published ear is live, not a snapshot taken at page load"
# The test above feeds a MOCKED window.__voice, so it proves the send
# expression is correct and proves nothing about whether the page ever
# updates the value it reads. That distinction is not academic: ?v=1 is not
# the only way the ear turns on -- pressing the mic on an ordinary talk page
# sets it mid-life, in rec.onend. Publishing `earOn: earOn` copies the
# boolean once at load, so that path sends voice:false, gets a markdown
# answer, and reads the asterisks aloud. The exact symptom the flag exists
# to prevent, surviving in the case it was aimed at.
#
# So build the REAL published object and flip the variable the mic path
# flips. A snapshot fails this; a getter passes it.
if python3 - "$T/talk-page.html" <<'PYEOF'
import re, subprocess, sys, json
page = open(sys.argv[1]).read()
i = page.find("window.__voice = {")
if i < 0:
    print("no __voice export found"); sys.exit(1)
start = page.index("{", i)
depth, j = 0, start
while j < len(page):
    if page[j] == "{":
        depth += 1
    elif page[j] == "}":
        depth -= 1
        if depth == 0:
            break
    j += 1
literal = page[start:j + 1]
js = ("var earOn = false, synthOK = true, gen = 0;"
      # NATIVE is the in-app speech flag. The literal references it, so the
      # sandbox must define it — false here, because this test is about the
      # BROWSER path's ear staying live, not about the app path.
      "var NATIVE = false;"
      "function chunkText(t){ return [t]; }"
      "function speakChunks(){}"
      "var window = { speechSynthesis: { cancel: function(){} } };"
      "window.__voice = " + literal + ";"
      "var before = window.__voice.earOn;"
      "earOn = true;"                       # exactly what rec.onend does
      "var after = window.__voice.earOn;"
      "console.log(JSON.stringify({before: before, after: after}));")
r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
if r.returncode != 0:
    print("published voice object does not evaluate:", r.stderr.strip()[:200])
    sys.exit(1)
d = json.loads(r.stdout.strip())
if d.get("before") is not False or d.get("after") is not True:
    print("stale ear: turning the mic on did not reach the send path", d)
    sys.exit(1)
PYEOF
then
  ok "turning the ear on mid-life reaches the send path (no stale snapshot)"
else bad "published ear is a snapshot, not live"; fi

echo "== invalid targets refused"
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"../evil","text":"x"}')"
if [ "$c" = "400" ]; then ok "bad target name refused (400)"; else bad "bad target refused (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/talk/..%2fevil")"
if [ "$c" = "404" ] || [ "$c" = "400" ]; then ok "bad talk path refused"; else bad "bad talk path (got $c)"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
