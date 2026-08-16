#!/usr/bin/env bash
# A2: the seat plane over a real (isolated) tmux server. Spawns a bash seat and
# drives it through spawn/send/read/state/wait/bind/kill. Uses a throwaway tmux
# socket so it never touches the user's ambient tmux.
set -uo pipefail
command -v tmux >/dev/null 2>&1 || { echo "skip: tmux not installed"; exit 0; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-seat.XXXXXX)"
TMUXSOCK="homi-seat-test-$$"
export COMM_STATE="$T/state"
export HOMI_SOCK_DIR="$T/socks"
export HOMI_SESSIONS_DIR="$T/sessions"
export HOMI_SELF="seathost"
export HOMI_TICK=1
export HOMI_TMUX_SOCKET="$TMUXSOCK"
export HOMI_SEAT_SESSION="seat-test"
mkdir -p "$HOMI_SESSIONS_DIR"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() {
  "$COMM" homi stop >/dev/null 2>&1 || true
  tmux -L "$TMUXSOCK" kill-server >/dev/null 2>&1 || true
  rm -rf "$T"
}
trap cleanup EXIT

"$COMM" homi start >/dev/null 2>&1

echo "== spawn a bash seat"
SEAT="$("$COMM" homi seat spawn 'bash --norc --noprofile' --name work 2>/dev/null)"
if printf '%s' "$SEAT" | grep -q '^%[0-9]'; then ok "seat spawned ($SEAT)"; else bad "seat spawn (got $SEAT)"; fi
sleep 1.2

echo "== state of an idle shell"
st="$("$COMM" homi seat state "$SEAT" 2>/dev/null)"
if [ "$st" = "idle" ]; then ok "idle shell reads idle"; else bad "idle shell state (got $st)"; fi

echo "== send + read (deliver-and-verify)"
"$COMM" homi seat send "$SEAT" 'echo HOMISEAT_MARKER_9713' >/dev/null 2>&1
sleep 0.8
scr="$("$COMM" homi seat read "$SEAT" 2>/dev/null)"
if printf '%s' "$scr" | grep -q 'HOMISEAT_MARKER_9713'; then ok "send delivered + read saw the output"; else bad "send/read roundtrip"; fi

echo "== busy detection: a foreground process reads busy, then settles idle"
"$COMM" homi seat send "$SEAT" 'sleep 3' >/dev/null 2>&1
sleep 0.6
st="$("$COMM" homi seat state "$SEAT" 2>/dev/null)"
if [ "$st" = "busy" ]; then ok "running sleep reads busy"; else bad "busy detection (got $st)"; fi
# seat wait should block through the sleep and return idle
w="$("$COMM" homi seat wait "$SEAT" --timeout 12 2>/dev/null)"
if printf '%s' "$w" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("state")=="idle" and d.get("sawbusy") else 1)' 2>/dev/null; then
  ok "seat wait saw busy and settled to idle"
else bad "seat wait (got $w)"; fi

echo "== secret redaction on read (unless --raw)"
"$COMM" homi seat send "$SEAT" 'echo tok_sk-abcdef0123456789abcd' >/dev/null 2>&1
sleep 0.6
red="$("$COMM" homi seat read "$SEAT" 2>/dev/null)"
raw="$("$COMM" homi seat read "$SEAT" --raw 2>/dev/null)"
if printf '%s' "$red" | grep -q 'REDACTED' && ! printf '%s' "$red" | grep -q 'sk-abcdef0123456789'; then
  ok "read redacts a token by default"
else bad "read redaction"; fi
if printf '%s' "$raw" | grep -q 'sk-abcdef0123456789'; then ok "--raw shows the token"; else bad "--raw passthrough"; fi

echo "== seat ls + bind to an identity"
"$COMM" homi claim worker >/dev/null 2>&1
"$COMM" homi seat bind "$SEAT" worker >/dev/null 2>&1
if grep -q "\"seat\": \"$SEAT\"" "$COMM_STATE/homi/identities.json" 2>/dev/null; then ok "bind recorded seat on the identity"; else bad "seat bind persisted"; fi
if "$COMM" homi seat ls 2>/dev/null | grep -q "$SEAT"; then ok "seat ls lists the seat"; else bad "seat ls"; fi

echo "== kill + dead detection"
"$COMM" homi seat kill "$SEAT" >/dev/null 2>&1
sleep 0.5
st="$("$COMM" homi seat state "$SEAT" 2>/dev/null)"
if [ "$st" = "dead" ]; then ok "killed seat reads dead"; else bad "dead detection (got $st)"; fi

"$COMM" homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
