#!/usr/bin/env bash
# Cross-fleet: two isolated daemons as two OPERATORS (fleets) linked over direct
# sockets. Verifies the deny-by-default grant model: ungranted names are refused
# with one ambiguous error, granted+claimed names deliver, no auto-claim oracle,
# foreign senders proxy fleet-qualified, control ops demand the token once a
# fleet link exists, and card/invite encoding round-trips.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-fleet.XXXXXX)"
mkdir -p "$T/a-sess" "$T/b-sess"
acomm(){ COMM_STATE="$T/a" HOMI_SOCK_DIR="$T/as" HOMI_SESSIONS_DIR="$T/a-sess" \
         HOMI_SELF=alice-dev HOMI_FLEET=alice HOMI_TICK=1 "$COMM" "$@"; }
bcomm(){ COMM_STATE="$T/b" HOMI_SOCK_DIR="$T/bs" HOMI_SESSIONS_DIR="$T/b-sess" \
         HOMI_SELF=bob-dev HOMI_FLEET=bob HOMI_TICK=1 "$COMM" "$@"; }
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ acomm homi stop >/dev/null 2>&1||true; bcomm homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT

acomm homi start >/dev/null 2>&1; bcomm homi start >/dev/null 2>&1

echo "== fleet links both ways (direct socket transport; kind=fleet)"
acomm homi link bob   --fleet --sock "$T/b/homi/in/alice.sock" >/dev/null 2>&1
bcomm homi link alice --fleet --sock "$T/a/homi/in/bob.sock" >/dev/null 2>&1
grep -q '"kind": "fleet"' "$T/a/homi/links.json" && ok "A persisted a fleet link" || bad "A fleet link kind"
grep -q '"kind": "fleet"' "$T/b/homi/links.json" && ok "B persisted a fleet link" || bad "B fleet link kind"

echo "== deny-by-default: A -> ungranted name on B is refused ambiguously"
bcomm homi claim librarian >/dev/null 2>&1     # exists on B but NOT granted
acomm homi send librarian@bob "hello?" --from scout >/dev/null 2>&1
sleep 2.5
dl="$(ls "$T/a/homi/out/bob/dead" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$dl" -ge 1 ]; then ok "ungranted send dead-lettered on the sender"; else bad "ungranted send (dead=$dl)"; fi
if ! bcomm homi inbox librarian 2>/dev/null | grep -q 'hello?'; then ok "nothing landed in the ungranted inbox"; else bad "ungranted mail leaked in"; fi

echo "== no-auto-claim oracle: granted-but-unclaimed name is also refused"
bcomm homi grant alice ghost >/dev/null 2>&1   # granted but never claimed
acomm homi send ghost@bob "anyone?" --from scout >/dev/null 2>&1
sleep 2.5
if [ ! -d "$T/b/homi/mail/ghost" ]; then ok "no mailbox was created for a foreigner"; else bad "mailbox-creation oracle"; fi

echo "== the granted path: grant librarian, mail flows, proxy is fleet-qualified"
bcomm homi grant alice librarian >/dev/null 2>&1
acomm homi send librarian@bob "REQUEST sweep budget=2h" --from orchestrator >/dev/null 2>&1
deadline=$((SECONDS+12)); got=""
while [ $SECONDS -lt $deadline ]; do
  if bcomm homi inbox librarian 2>/dev/null | grep -q 'REQUEST sweep'; then got=1; break; fi
  sleep 1
done
[ -n "$got" ] && ok "granted mail crossed the fleet boundary" || bad "granted mail delivery"
if bcomm homi inbox librarian 2>/dev/null | grep -q 'orchestrator@alice'; then
  ok "sender proxied fleet-qualified (orchestrator@alice)"
else bad "fleet-qualified attribution"; fi
if grep -q '"orchestrator@alice"' "$T/b/homi/identities.json" 2>/dev/null; then
  ok "proxy identity stored fleet-qualified"
else bad "proxy stored bare (squat risk)"; fi

echo "== control token: required once a fleet link exists"
tok="$(cat "$T/b/homi/control.token")"
raw="$(python3 - "$T/b/homi/homi.sock" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])
s.sendall(b'{"op":"claim","name":"intruder"}\n'); s.shutdown(socket.SHUT_WR)
buf = b""
while b"\n" not in buf:
    c = s.recv(4096)
    if not c: break
    buf += c
print(buf.decode().strip())
PY
)"
if printf '%s' "$raw" | grep -q 'control token required'; then ok "tokenless control op refused"; else bad "token gate (got: $raw)"; fi
if ! grep -q '"intruder"' "$T/b/homi/identities.json" 2>/dev/null; then ok "intruder claim did not land"; else bad "intruder claimed"; fi
# and the legit CLI (which reads the token file) still works:
bcomm homi claim tokentest >/dev/null 2>&1
grep -q '"tokentest"' "$T/b/homi/identities.json" && ok "token-bearing CLI still works" || bad "CLI with token"

echo "== card / invite encoding round-trips"
CARD="$(acomm homi card 2>/dev/null)"
if printf '%s' "$CARD" | python3 -c '
import base64,json,sys
c=json.loads(base64.b64decode(sys.stdin.read().strip()))
assert c["kind"]=="homi-card" and c["fleet"]=="alice" and c["pubkey"].startswith("ssh-ed25519")
assert c["inbound_dir"].endswith("/homi/in")
print("ok")' 2>/dev/null | grep -q ok; then ok "card encodes fleet+pubkey+inbound_dir"; else bad "card contents"; fi
acomm homi federate status 2>/dev/null | grep -q 'fleet=alice' && ok "federate status prints the fleet" || bad "federate status"

echo "== cross-fleet ask/reply: token rewritten at the boundary, reply routes home"
bcomm homi grant alice librarian >/dev/null 2>&1
( acomm homi ask librarian@bob "what is 2+2?" --from orchestrator --timeout 25 --json > "$T/ask.json" 2>/dev/null ) &
ASKPID=$!
deadline=$((SECONDS+15)); TOKEN=""
while [ $SECONDS -lt $deadline ]; do
  TOKEN="$(bcomm homi inbox librarian 2>/dev/null | grep -o 'homi reply [^ ]*' | tail -1 | awk '{print $3}')"
  [ -n "$TOKEN" ] && break
  sleep 1
done
if printf '%s' "$TOKEN" | grep -q '^orchestrator@alice~'; then
  ok "ask token rewritten to the fleet petname ($TOKEN)"
else bad "token rewrite (got: $TOKEN)"; fi
bcomm homi reply "$TOKEN" "it is 4" --from librarian >/dev/null 2>&1
wait $ASKPID
if python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("ok") and d.get("reply")=="it is 4" else 1)' "$T/ask.json" 2>/dev/null; then
  ok "cross-fleet ask got the correlated reply"
else bad "cross-fleet ask/reply (got: $(cat "$T/ask.json" 2>/dev/null))"; fi
if grep -q '"orchestrator"' "$T/a/homi/grants/bob.json" 2>/dev/null; then
  ok "asking auto-granted the return path (orchestrator to fleet bob)"
else bad "return-path auto-grant"; fi

echo "== grants listing + ungrant"
bcomm homi ungrant alice librarian >/dev/null 2>&1
acomm homi send librarian@bob "after revoke" --from orchestrator >/dev/null 2>&1
sleep 2.5
if ! bcomm homi inbox librarian 2>/dev/null | grep -q 'after revoke'; then ok "ungrant closes the door again"; else bad "ungrant"; fi

acomm homi stop >/dev/null 2>&1; bcomm homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
