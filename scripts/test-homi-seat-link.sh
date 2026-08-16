#!/usr/bin/env bash
# A3: cross-device seats. Two isolated homi daemons linked over direct sockets;
# seat ops ride the link as a "seat" envelope whose ack carries the result. Seat
# control is opt-in per link (--allow-seats), upgradable in place.
set -uo pipefail
command -v tmux >/dev/null 2>&1 || { echo "skip: tmux not installed"; exit 0; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-seatlink.XXXXXX)"
ATMUX="homi-seatA-$$"; BTMUX="homi-seatB-$$"
mkdir -p "$T/a-sess" "$T/b-sess"
acomm(){ COMM_STATE="$T/a" HOMI_SOCK_DIR="$T/as" HOMI_SESSIONS_DIR="$T/a-sess" \
         HOMI_SELF=alpha HOMI_TICK=1 HOMI_TMUX_SOCKET="$ATMUX" HOMI_SEAT_SESSION=sa "$COMM" "$@"; }
bcomm(){ COMM_STATE="$T/b" HOMI_SOCK_DIR="$T/bs" HOMI_SESSIONS_DIR="$T/b-sess" \
         HOMI_SELF=beta  HOMI_TICK=1 HOMI_TMUX_SOCKET="$BTMUX" HOMI_SEAT_SESSION=sb "$COMM" "$@"; }
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ acomm homi stop >/dev/null 2>&1||true; bcomm homi stop >/dev/null 2>&1||true
  tmux -L "$ATMUX" kill-server >/dev/null 2>&1||true; tmux -L "$BTMUX" kill-server >/dev/null 2>&1||true
  rm -rf "$T"; }
trap cleanup EXIT

acomm homi start >/dev/null 2>&1; bcomm homi start >/dev/null 2>&1

echo "== symmetric link; B grants A seat control, A does NOT grant B"
acomm homi link beta  --sock "$T/b/homi/in/alpha.sock" >/dev/null 2>&1
bcomm homi link alpha --sock "$T/a/homi/in/beta.sock" --allow-seats >/dev/null 2>&1
[ -S "$T/a/homi/in/beta.sock" ] && ok "A inbound bound" || bad "A inbound bound"
[ -S "$T/b/homi/in/alpha.sock" ] && ok "B inbound bound" || bad "B inbound bound"

echo "== A drives B's seats (granted): spawn + send + read + state, all remote"
SEAT="$(acomm homi seat spawn 'bash --norc --noprofile' --device beta 2>/dev/null)"
if printf '%s' "$SEAT" | grep -q '^beta:%[0-9]'; then ok "remote spawn returned beta:%N ($SEAT)"; else bad "remote spawn (got $SEAT)"; fi
sleep 1
st="$(acomm homi seat state "$SEAT" 2>/dev/null)"
if [ "$st" = "idle" ]; then ok "remote state = idle"; else bad "remote state (got $st)"; fi
acomm homi seat send "$SEAT" 'echo REMOTE_SEAT_4821' >/dev/null 2>&1
sleep 0.9
if acomm homi seat read "$SEAT" 2>/dev/null | grep -q 'REMOTE_SEAT_4821'; then ok "remote send+read roundtrip over the link"; else bad "remote send/read"; fi
# The seat truly lives on B: B sees it locally.
if bcomm homi seat ls 2>/dev/null | grep -q "${SEAT#beta:}"; then ok "seat physically lives on B"; else bad "seat lives on B"; fi

echo "== grant gate: B driving A's seats is refused (A never granted B)"
r="$(bcomm homi seat spawn 'bash --norc' --device alpha 2>&1)"
if printf '%s' "$r" | grep -qi 'not granted'; then ok "ungranted seat control refused"; else bad "ungranted refusal (got: $r)"; fi

echo "== upgrade the grant in place, then it works"
acomm homi link beta --allow-seats >/dev/null 2>&1
r="$(bcomm homi seat spawn 'bash --norc --noprofile' --device alpha 2>/dev/null)"
if printf '%s' "$r" | grep -q '^alpha:%[0-9]'; then ok "after --allow-seats upgrade, B drives A"; else bad "in-place grant upgrade (got: $r)"; fi

echo "== revoke in place, then it is refused again"
acomm homi link beta --revoke-seats >/dev/null 2>&1
r="$(bcomm homi seat state alpha:%99 2>&1)"
if printf '%s' "$r" | grep -qi 'not granted'; then ok "revoke-seats denies again"; else bad "revoke (got: $r)"; fi

acomm homi stop >/dev/null 2>&1; bcomm homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
