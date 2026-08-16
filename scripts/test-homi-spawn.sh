#!/usr/bin/env bash
# A4: homi spawn / fan / consult — the fabric creates agents. Tested with stub
# bash "agents" (adopt off; real-agent /rename adoption is the dogfood phase).
set -uo pipefail
command -v tmux >/dev/null 2>&1 || { echo "skip: tmux not installed"; exit 0; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-spawn.XXXXXX)"
TMUXSOCK="homi-spawn-$$"
export COMM_STATE="$T/state" HOMI_SOCK_DIR="$T/socks" HOMI_SESSIONS_DIR="$T/sessions"
export HOMI_SELF="spawnhost" HOMI_TICK=1 HOMI_TMUX_SOCKET="$TMUXSOCK" HOMI_SEAT_SESSION="sp"
mkdir -p "$HOMI_SESSIONS_DIR"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ "$COMM" homi stop >/dev/null 2>&1||true; tmux -L "$TMUXSOCK" kill-server >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT
ID="$COMM_STATE/homi/identities.json"

"$COMM" homi start >/dev/null 2>&1

echo "== spawn: claim + seat + bind in one verb"
out="$("$COMM" homi spawn worker1 -- bash --norc --noprofile 2>/dev/null)"
if printf '%s' "$out" | grep -q 'worker1 -> %[0-9]'; then ok "spawn returned name -> seat ($out)"; else bad "spawn output ($out)"; fi
seat="${out##*-> }"
if grep -q '"worker1"' "$ID" 2>/dev/null; then ok "spawn claimed the identity"; else bad "spawn claimed identity"; fi
if grep -q "\"seat\": \"$seat\"" "$ID" 2>/dev/null; then ok "spawn bound the seat to the identity"; else bad "spawn bound seat"; fi
sleep 0.8
if [ "$("$COMM" homi seat state "$seat" 2>/dev/null)" = "idle" ]; then ok "spawned seat is a live shell"; else bad "spawned seat live"; fi
# the identity is mailable
"$COMM" homi send worker1 "hello worker" --from tester >/dev/null 2>&1
sleep 0.3
if "$COMM" homi inbox worker1 2>/dev/null | grep -q 'hello worker'; then ok "spawned identity receives mail"; else bad "spawned identity mailable"; fi

echo "== fan: N agents + an addressable group"
"$COMM" homi fan --n 3 --prefix team -- bash --norc --noprofile >/dev/null 2>&1
sleep 0.5
c=0
for nm in team-1 team-2 team-3; do grep -q "\"$nm\"" "$ID" && c=$((c+1)); done
if [ "$c" = "3" ]; then ok "fan claimed 3 identities"; else bad "fan identities ($c/3)"; fi
"$COMM" homi group team-1,team-2,team-3 "fan hello" --from lead >/dev/null 2>&1
sleep 0.3
g=0
for nm in team-1 team-2 team-3; do "$COMM" homi inbox "$nm" 2>/dev/null | grep -q 'fan hello' && g=$((g+1)); done
if [ "$g" = "3" ]; then ok "the fan group is addressable"; else bad "fan group ($g/3)"; fi

echo "== consult: spawn-or-reuse a private peer"
# First consult spawns consult-bash; the ask times out (stub can't answer) but
# the peer is created. Second consult REUSES it (no second seat).
"$COMM" homi consult "ping?" --cli bash --timeout 2 --json > "$T/c1.json" 2>/dev/null
r2="$("$COMM" homi consult "ping again?" --cli bash --timeout 2 --json 2>/dev/null)"
if grep -q '"consult-bash"' "$ID" 2>/dev/null; then ok "consult claimed a private peer identity"; else bad "consult peer identity"; fi
if python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("reused") else 1)' "$r2" 2>/dev/null; then
  ok "second consult reused the peer"; else bad "consult reuse (got: $r2)"; fi
nwin="$(tmux -L "$TMUXSOCK" list-windows -a 2>/dev/null | grep -c consult-bash || true)"
if [ "$nwin" = "1" ]; then ok "reuse spawned no second seat (1 window)"; else bad "consult seat reuse ($nwin windows)"; fi

"$COMM" homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
