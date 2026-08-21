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
if [ "$(printf '%s' "$page" | grep -c -e 'https\?://' -e 'url(' -e '@import' -e '<link' -e 'src=')" = "0" ]; then
  ok "talk page self-contained"
else bad "talk page self-contained"; fi

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

echo "== board rows link to talk"
board="$(curl -sf -m 5 "http://127.0.0.1:$PORT/" 2>/dev/null)"
if printf '%s' "$board" | grep -q "/talk/"; then
  ok "board page carries /talk hrefs"
else bad "board page carries /talk hrefs"; fi

echo "== invalid targets refused"
c="$(send -H "Content-Type: application/json" -H "X-Homi: 1" -H "X-Homi-Token: $TOKEN" -d '{"to":"../evil","text":"x"}')"
if [ "$c" = "400" ]; then ok "bad target name refused (400)"; else bad "bad target refused (got $c)"; fi
c="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/talk/..%2fevil")"
if [ "$c" = "404" ] || [ "$c" = "400" ]; then ok "bad talk path refused"; else bad "bad talk path (got $c)"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
