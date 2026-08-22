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
# A long-lived foreground command so the busy window can't expire mid-check
# under load (a fixed short sleep raced the sampler -- see history). Poll for
# busy instead of a single fixed-delay sample, same idiom as the
# deadline=$((SECONDS+N)) loops in test-homi-move.sh / test-homi-fleet.sh.
"$COMM" homi seat send "$SEAT" 'sleep 30' >/dev/null 2>&1
deadline=$((SECONDS+10)); busy=""
while [ $SECONDS -lt $deadline ]; do
  st="$("$COMM" homi seat state "$SEAT" 2>/dev/null)"
  if [ "$st" = "busy" ]; then busy=1; break; fi
  sleep 0.3
done
if [ -n "$busy" ]; then ok "running sleep reads busy"; else bad "busy detection (got $st)"; fi
# seat wait should observe busy and then settle to idle -- without waiting
# out the full 30s. Interrupt the sleep for real (a background job fires a
# Ctrl-C on the pane a few seconds in, once wait is certain to have sampled
# busy). `seat interrupt` sends Escape, which a foreground `sleep` ignores
# (verified: state stayed busy through it); a real SIGINT on the pane is the
# only thing that ends it without killing/respawning the seat, so this still
# exercises a genuine busy->idle transition, not a busy->dead one.
( sleep 3; tmux -L "$TMUXSOCK" send-keys -t "$SEAT" C-c ) &
intpid=$!
w="$("$COMM" homi seat wait "$SEAT" --timeout 20 2>/dev/null)"
wait "$intpid" 2>/dev/null
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

echo "== the surface is measured, never asserted"
"$COMM" homi claim surf >/dev/null 2>&1
S2="$("$COMM" homi seat spawn 'bash --norc --noprofile' 2>/dev/null)"
"$COMM" homi seat bind "$S2" surf >/dev/null 2>&1
sleep 1
"$COMM" homi agents --json 2>/dev/null | python3 -c '
import json,sys
a={x["name"]:x for x in json.load(sys.stdin)["agents"]}
s=a["surf"]["surface"]
assert s and s["driver"]=="tmux" and s["state"] in ("idle","busy","booting"), s
' && ok "a live seat reports a measured surface" || bad "surface measurement"

"$COMM" homi seat kill "$S2" >/dev/null 2>&1
sleep 1
"$COMM" homi agents --json 2>/dev/null | python3 -c '
import json,sys
a={x["name"]:x for x in json.load(sys.stdin)["agents"]}
s=a["surf"]["surface"]
assert s["state"]=="dead", s
' && ok "a dead seat reports dead, not a stale handle" || bad "dead surface honesty"

echo "== the seat relay: mail types into an AGENT seat, never a shell"
"$COMM" homi claim tester >/dev/null 2>&1
"$COMM" homi claim relaybot >/dev/null 2>&1
"$COMM" homi claim shellbot >/dev/null 2>&1
# agent surface: a real node REPL (pane_current_command == node -> is_agent)
ASEAT="$("$COMM" homi seat spawn 'node -e "process.stdin.resume()"' --name relayagent 2>/dev/null)"
# non-agent surface: a bare shell
BSEAT="$("$COMM" homi seat spawn 'bash --norc --noprofile' --name relayshell 2>/dev/null)"
# an agent surface that is NOT idle (node -i shows the "Welcome to" banner ->
# reads booting) — proves the state gate holds even on a real agent seat
"$COMM" homi claim bootbot >/dev/null 2>&1
CSEAT="$("$COMM" homi seat spawn 'node -i' --name relaybooting 2>/dev/null)"
sleep 2
"$COMM" homi seat bind "$ASEAT" relaybot >/dev/null 2>&1
"$COMM" homi seat bind "$BSEAT" shellbot >/dev/null 2>&1
"$COMM" homi seat bind "$CSEAT" bootbot >/dev/null 2>&1

# CRITICAL: a shell seat must NEVER receive typed mail (mail-send is not
# command execution). Send, wait past several ticks, assert absence.
"$COMM" homi send shellbot "touch /tmp/homi-relay-should-never-run-$$" --from tester >/dev/null 2>&1
sleep 6
if [ -f "/tmp/homi-relay-should-never-run-$$" ]; then
  bad "SHELL SEAT EXECUTED RELAYED MAIL (critical)"; rm -f "/tmp/homi-relay-should-never-run-$$"
else ok "shell seat never typed into (mail-send stays mail-send)"; fi
if tmux -L "$TMUXSOCK" capture-pane -t "$BSEAT" -p 2>/dev/null | grep -q "homi-relay-should-never-run"; then
  bad "mail text reached the shell composer"
else ok "shell seat held its mail (not an agent surface)"; fi

# the agent seat DOES receive it, typed with attribution + reply path
"$COMM" homi send relaybot "hello from the mail plane" --from tester >/dev/null 2>&1
typed=""
for i in $(seq 1 12); do
  tmux -L "$TMUXSOCK" capture-pane -t "$ASEAT" -p 2>/dev/null | grep -q "hello from the mail plane" && { typed=1; break; }
  sleep 1
done
if [ -n "$typed" ]; then ok "mail typed into the bound AGENT seat"; else bad "agent seat relay typing"; fi
cap="$(tmux -L "$TMUXSOCK" capture-pane -t "$ASEAT" -p 2>/dev/null)"
if printf '%s' "$cap" | grep -q "from @tester"; then ok "typed mail carries attribution"
else bad "typed attribution"; fi
if printf '%s' "$cap" | grep -q "communicate homi send tester"; then ok "typed mail teaches the reply path"
else bad "typed reply instruction"; fi
sleep 3
n="$(tmux -L "$TMUXSOCK" capture-pane -t "$ASEAT" -p 2>/dev/null | grep -c "hello from the mail plane")"
if [ "$n" = "1" ]; then ok "cursor advanced: delivered once, never retyped"
else bad "retype guard (copies=$n)"; fi

"$COMM" homi send bootbot "should wait for idle" --from tester >/dev/null 2>&1
sleep 5
if tmux -L "$TMUXSOCK" capture-pane -t "$CSEAT" -p 2>/dev/null | grep -q "should wait for idle"; then
  bad "typed into a non-idle AGENT seat (state gate bypassed)"
else ok "agent seat that is not idle holds its mail (state gate)"; fi


"$COMM" homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
