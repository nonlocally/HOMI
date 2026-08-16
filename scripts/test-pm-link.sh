#!/usr/bin/env bash
# Postmaster↔postmaster link test: two isolated postmasters on one host,
# linked over DIRECT socket paths (the --sock transport override; the ssh
# transport wraps exactly this wire protocol). Covers: envelope delivery with
# arrival-line attribution, acks emptying the outbound queue, hold-and-retry
# while the far side is down, dedup by msg_id, proxy identities for remote
# senders, and reply routing with auto-claimed mailboxes.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/pm-link.XXXXXX)"

A_STATE="$T/a-state"; A_SOCKS="$T/a-socks"; A_SESS="$T/a-sess"
B_STATE="$T/b-state"; B_SOCKS="$T/b-socks"; B_SESS="$T/b-sess"
mkdir -p "$A_SESS" "$B_SESS"

acomm() { COMM_STATE="$A_STATE" PM_SOCK_DIR="$A_SOCKS" PM_SESSIONS_DIR="$A_SESS" PM_SELF=alpha PM_TICK=1 "$COMM" "$@"; }
bcomm() { COMM_STATE="$B_STATE" PM_SOCK_DIR="$B_SOCKS" PM_SESSIONS_DIR="$B_SESS" PM_SELF=beta  PM_TICK=1 "$COMM" "$@"; }

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { acomm pm stop >/dev/null 2>&1 || true; bcomm pm stop >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

wait_for() { # wait_for <deadline-s> <desc> <cmd...>
  local dl=$((SECONDS + $1)); shift
  local desc="$1"; shift
  while [ $SECONDS -lt $dl ]; do
    if "$@" >/dev/null 2>&1; then ok "$desc"; return 0; fi
    sleep 0.5
  done
  bad "$desc"; return 1
}

echo "== setup: two postmasters, symmetric links"
acomm pm start >/dev/null 2>&1 || bad "A start"
bcomm pm start >/dev/null 2>&1 || bad "B start"
bcomm pm claim remote-x >/dev/null 2>&1 || bad "B claim remote-x"
# A's outbound targets B's inbound-for-alpha; B's outbound targets A's inbound-for-beta.
if acomm pm link beta --sock "$B_STATE/pm/in/alpha.sock" >/dev/null 2>&1; then ok "A link beta"; else bad "A link beta"; fi
if bcomm pm link alpha --sock "$A_STATE/pm/in/beta.sock" >/dev/null 2>&1; then ok "B link alpha"; else bad "B link alpha"; fi
[ -S "$A_STATE/pm/in/beta.sock" ] && ok "A inbound-for-beta bound" || bad "A inbound-for-beta bound"
[ -S "$B_STATE/pm/in/alpha.sock" ] && ok "B inbound-for-alpha bound" || bad "B inbound-for-alpha bound"

echo "== cross-postmaster delivery + ack"
acomm pm send remote-x@beta "hello across" --from tester >/dev/null 2>&1 || bad "A send remote-x@beta"
BX="$B_STATE/pm/mail/remote-x/inbox.jsonl"
wait_for 10 "delivered into B's remote-x inbox" grep -q "hello across" "$BX"
if grep -q '"via": "alpha"' "$BX"; then ok "arrival-line attribution (via alpha)"; else bad "arrival-line attribution"; fi
sleep 1
qn="$(ls "$A_STATE/pm/out/beta" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$qn" = "0" ]; then ok "A outbound queue empty (acked)"; else bad "A outbound queue empty (got $qn)"; fi

echo "== proxy identity for the remote sender"
wait_for 6 "B grew proxy socket for tester" test -S "$B_SOCKS/pm-tester.sock"
if grep -q '"name":"tester"' "$B_SESS/pm-tester.json" 2>/dev/null; then ok "B planted proxy sidecar"; else bad "B planted proxy sidecar"; fi

echo "== reply routing (proxy -> link -> auto-claimed mailbox)"
python3 "$HERE/lib/cc_peer.py" send --to "$B_SOCKS/pm-tester.sock" --text "reply back" \
  --from "$B_SOCKS/replier.sock" --name remote-x 2>/dev/null
AT="$A_STATE/pm/mail/tester/inbox.jsonl"
wait_for 10 "reply landed in A's auto-claimed tester mailbox" grep -q "reply back" "$AT"
if grep -q '"from_name": "remote-x"' "$AT"; then ok "reply attribution preserved"; else bad "reply attribution preserved"; fi

echo "== hold + retry while far side down"
bcomm pm stop >/dev/null 2>&1; sleep 1
acomm pm send remote-x@beta "while down" --from tester >/dev/null 2>&1
sleep 2
qn="$(ls "$A_STATE/pm/out/beta" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$qn" = "1" ]; then ok "queued while beta down"; else bad "queued while beta down (got $qn)"; fi
bcomm pm start >/dev/null 2>&1
wait_for 15 "drained after beta restart" grep -q "while down" "$BX"
sleep 1
qn="$(ls "$A_STATE/pm/out/beta" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$qn" = "0" ]; then ok "queue emptied after restart"; else bad "queue emptied (got $qn)"; fi

echo "== dedup by msg_id at the receiving postmaster"
python3 - "$B_STATE/pm/in/alpha.sock" <<'PY'
import json, socket, sys
env = {"v": 1, "kind": "m", "to": "remote-x", "from": "tester",
       "msg_id": "dupenv42", "text": "dup envelope", "ts": 0}
for _ in range(2):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5); s.connect(sys.argv[1])
    s.sendall((json.dumps(env) + "\n").encode())
    print(s.recv(4096).decode().strip())
    s.close()
PY
sleep 0.5
if [ "$(grep -c 'dup envelope' "$BX")" = "1" ]; then ok "envelope dedup"; else bad "envelope dedup"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
