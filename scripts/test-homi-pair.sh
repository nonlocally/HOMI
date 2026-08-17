#!/usr/bin/env bash
# Pair test: the measured handshake (ping envelope + link-check) and the
# one-sided device-enrollment orchestration (homi pair). Two isolated homis on
# one host over DIRECT socket paths, same harness as test-homi-link.sh; the
# full ssh path is opt-in (HOMI_TEST_SSH=1 needs `ssh localhost` batch auth).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-pair.XXXXXX)"

A_STATE="$T/a-state"; A_SOCKS="$T/a-socks"; A_SESS="$T/a-sess"
B_STATE="$T/b-state"; B_SOCKS="$T/b-socks"; B_SESS="$T/b-sess"
mkdir -p "$A_SESS" "$B_SESS"

acomm() { COMM_STATE="$A_STATE" HOMI_SOCK_DIR="$A_SOCKS" HOMI_SESSIONS_DIR="$A_SESS" HOMI_SELF=alpha HOMI_TICK=1 "$COMM" "$@"; }
bcomm() { COMM_STATE="$B_STATE" HOMI_SOCK_DIR="$B_SOCKS" HOMI_SESSIONS_DIR="$B_SESS" HOMI_SELF=beta  HOMI_TICK=1 "$COMM" "$@"; }

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { acomm homi stop >/dev/null 2>&1 || true; bcomm homi stop >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

jget() { python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d[k]
print(d)' "$@" 2>/dev/null; }

echo "== setup: two homis, symmetric direct links"
acomm homi start >/dev/null 2>&1 || bad "A start"
bcomm homi start >/dev/null 2>&1 || bad "B start"
acomm homi link beta --sock "$B_STATE/homi/in/alpha.sock" >/dev/null 2>&1 || bad "A link beta"
bcomm homi link alpha --sock "$A_STATE/homi/in/beta.sock" >/dev/null 2>&1 || bad "B link alpha"
bcomm homi init --handle betauser >/dev/null 2>&1 || bad "B claims a handle"

echo "== link-check: the measured handshake"
r="$(acomm homi link beta --check --json 2>/dev/null)"
if [ "$(printf '%s' "$r" | jget ok)" = "True" ] && [ "$(printf '%s' "$r" | jget far_device)" = "beta" ]; then
  ok "link-check reaches beta and names it from the wire"
else bad "link-check reaches beta (got: $r)"; fi
rtt="$(printf '%s' "$r" | jget rtt_ms)"
if [ -n "$rtt" ] && [ "$rtt" -ge 0 ] 2>/dev/null; then
  ok "round trip is MEASURED (${rtt} ms)"
else bad "round trip measured (got: $rtt)"; fi
srcv="$(grep -m1 '^HOMI_VERSION' "$HERE/lib/homi.py" | cut -d'"' -f2)"
if [ "$(printf '%s' "$r" | jget far_version)" = "$srcv" ]; then
  ok "far daemon version rides the pong"
else bad "far daemon version rides the pong"; fi
if [ "$(printf '%s' "$r" | jget far_user)" = "betauser" ]; then
  ok "far user handle rides the pong"
else bad "far user handle rides the pong (got: $(printf '%s' "$r" | jget far_user))"; fi

echo "== raw ping envelopes: stateless, dedup-neutral, fail-closed"
ping_once() { python3 - "$B_STATE/homi/in/alpha.sock" "$1" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(5)
s.connect(sys.argv[1])
env = {"v": 1, "kind": "ping", "ts": 0.0}
if sys.argv[2] != "nomid":
    env["msg_id"] = sys.argv[2]
s.sendall((json.dumps(env) + "\n").encode())
print(s.makefile().readline().strip())
PY
}
p1="$(ping_once fixedping1)"
p2="$(ping_once fixedping1)"
if printf '%s' "$p1" | grep -q '"pong": true' && printf '%s' "$p1" | grep -q '"device": "beta"'; then
  ok "ping acked with pong + device"
else bad "ping acked with pong + device (got: $p1)"; fi
if printf '%s' "$p2" | grep -q '"pong": true' && ! printf '%s' "$p2" | grep -q '"dup"'; then
  ok "repeated ping is not deduped (stateless — no seen-ring pollution)"
else bad "repeated ping is not deduped (got: $p2)"; fi
if grep -q "fixedping1" "$B_STATE/homi/seen/alpha" 2>/dev/null; then
  bad "ping msg_id leaked into the dedup ring"
else ok "ping msg_id never enters the dedup ring"; fi
pn="$(ping_once nomid)"
if printf '%s' "$pn" | grep -q '"bad envelope"'; then
  ok "ping without msg_id refused (bad envelope)"
else bad "ping without msg_id refused (got: $pn)"; fi

echo "== link-check when the far side is down: honest failure, healthy daemon"
bcomm homi stop >/dev/null 2>&1; sleep 0.7
r="$(acomm homi link beta --check --json 2>/dev/null)"
if [ "$(printf '%s' "$r" | jget ok)" = "False" ] && [ -n "$(printf '%s' "$r" | jget err)" ]; then
  ok "far-down reports ok:false with a reason"
else bad "far-down reports ok:false (got: $r)"; fi
st="$(acomm homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget ok)" = "True" ]; then ok "A stays healthy"; else bad "A stays healthy"; fi

echo "== legacy peer: an old daemon that rejects the ping kind"
LEG="$T/legacy.sock"
python3 - "$LEG" <<'PY' &
import json, socket, sys
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sys.argv[1]); srv.listen(4); srv.settimeout(20)
try:
    while True:
        c, _ = srv.accept()
        c.makefile().readline()
        c.sendall((json.dumps({"ok": False, "err": "bad envelope"}) + "\n").encode())
        c.close()
except Exception:
    pass
PY
LEGPID=$!
sleep 0.5
acomm homi link legacydev --sock "$LEG" >/dev/null 2>&1
r="$(acomm homi link legacydev --check --json 2>/dev/null)"
if [ "$(printf '%s' "$r" | jget ok)" = "True" ] && [ "$(printf '%s' "$r" | jget legacy_peer)" = "True" ] \
   && [ "$(printf '%s' "$r" | jget transport)" = "up" ]; then
  ok "legacy peer: transport measured up, honestly flagged legacy"
else bad "legacy peer flagged (got: $r)"; fi
kill "$LEGPID" 2>/dev/null

echo "== pair --dry-run: the golden plan, zero mutation"
links_before="$(cat "$A_STATE/homi/links.json" 2>/dev/null)"
out="$(acomm homi pair fake@nowhere.invalid --dry-run 2>&1)"
rc=$?
if [ $rc -eq 0 ]; then ok "pair --dry-run exits 0"; else bad "pair --dry-run exits 0 (rc=$rc; out: $out)"; fi
for step in "1/7" "2/7" "3/7" "4/7" "5/7" "6/7" "7/7"; do
  if printf '%s' "$out" | grep -q "$step"; then ok "dry-run plan names step $step"; else bad "dry-run plan names step $step"; fi
done
if printf '%s' "$out" | grep -qi "ssh"; then ok "dry-run plan mentions the ssh probe"; else bad "dry-run plan mentions ssh"; fi
links_after="$(cat "$A_STATE/homi/links.json" 2>/dev/null)"
if [ "$links_before" = "$links_after" ]; then
  ok "dry-run mutated nothing (links.json unchanged)"
else bad "dry-run mutated links.json"; fi

echo "== pair refuses without reachability (measured, exact fix printed)"
out="$(acomm homi pair fake@nowhere.invalid 2>&1)"
rc=$?
if [ $rc -ne 0 ]; then ok "pair against unreachable host fails"; else bad "pair against unreachable host fails"; fi
if printf '%s' "$out" | grep -q "ssh-copy-id\|ssh "; then
  ok "failure prints the exact fix"
else bad "failure prints the exact fix (got: $out)"; fi

if [ "${HOMI_TEST_SSH:-0}" = "1" ]; then
  echo "== full pair over ssh localhost (opt-in)"
  FARHOME="$T/farhome"
  mkdir -p "$FARHOME"
  # A fresh far HOME with no homi: pair must stage the kernel, start the
  # daemon, link both ways, sync the handle, and measure both round trips.
  acomm homi init --handle pairtester --force >/dev/null 2>&1
  out="$(HOMI_PAIR_FAR_HOME="$FARHOME" acomm homi pair "$(whoami)@localhost" --name farbox --no-persist 2>&1)"
  rc=$?
  if [ $rc -eq 0 ]; then ok "pair localhost completes"; else bad "pair localhost completes (out: $out)"; fi
  if printf '%s' "$out" | grep -q "round trip"; then ok "pair reports measured round trips"; else bad "pair reports round trips"; fi
  out2="$(HOMI_PAIR_FAR_HOME="$FARHOME" acomm homi pair "$(whoami)@localhost" --name farbox --no-persist 2>&1)"
  if [ $? -eq 0 ] && printf '%s' "$out2" | grep -qi "kept\|already"; then
    ok "second pair run is idempotent"
  else bad "second pair run is idempotent"; fi
fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
