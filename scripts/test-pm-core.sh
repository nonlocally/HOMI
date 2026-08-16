#!/usr/bin/env bash
# End-to-end test for the postmaster core: lifecycle, identities, store→wake,
# probe/status. Fully isolated — overrides COMM_STATE and PM_SOCK_DIR so it
# never touches the real state dir, the real /tmp/cc-socks, or a real Claude
# sessions dir (PM_SESSIONS_DIR points discovery at a scratch dir too).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/pm-test.XXXXXX)"
export COMM_STATE="$T/state"
export PM_SOCK_DIR="$T/socks"
export PM_SESSIONS_DIR="$T/sessions"
export PM_SELF="testhost"
export PM_TICK=1
PMS="$COMM_STATE/pm"
mkdir -p "$PM_SESSIONS_DIR"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { "$COMM" pm stop >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

jget() { python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d[k]
print(d)' "$@" 2>/dev/null; }

echo "== section 1: lifecycle"
if "$COMM" pm start >/dev/null 2>&1; then ok "pm start"; else bad "pm start"; fi
if [ -f "$PMS/daemon.pid" ] && kill -0 "$(cat "$PMS/daemon.pid")" 2>/dev/null; then
  ok "daemon running (pidfile)"
else bad "daemon running (pidfile)"; fi
if "$COMM" pm start >/dev/null 2>&1; then bad "double start refused"; else ok "double start refused"; fi

echo "== section 2: status + self-probe"
st="$("$COMM" pm status --json 2>/dev/null)"
if [ -n "$st" ]; then ok "status --json emits"; else bad "status --json emits"; fi
if [ "$(printf '%s' "$st" | jget self device)" = "testhost" ]; then ok "self.device"; else bad "self.device"; fi
if [ "$(printf '%s' "$st" | jget self socks pm.sock state)" = "live" ]; then ok "pm.sock self-probe live"; else bad "pm.sock self-probe live"; fi
if [ "$(printf '%s' "$st" | jget self socks pm.sock provenance)" = "probed" ]; then ok "provenance probed"; else bad "provenance probed"; fi

if "$COMM" pm stop >/dev/null 2>&1; then ok "pm stop"; else bad "pm stop"; fi
sleep 0.7
if [ ! -S "$PMS/pm.sock" ]; then ok "control socket unlinked"; else bad "control socket unlinked"; fi
pid_gone=1
if [ -f "$PMS/daemon.pid" ] && kill -0 "$(cat "$PMS/daemon.pid" 2>/dev/null)" 2>/dev/null; then pid_gone=0; fi
if [ "$pid_gone" -eq 1 ]; then ok "daemon exited"; else bad "daemon exited"; fi

echo "== section 3: identities (claim/release)"
"$COMM" pm start >/dev/null 2>&1
if "$COMM" pm claim alice >/dev/null 2>&1; then ok "claim alice"; else bad "claim alice"; fi
ASOCK="$PM_SOCK_DIR/pm-alice.sock"
ASIDE="$PM_SESSIONS_DIR/pm-alice.json"
if [ -S "$ASOCK" ]; then ok "identity socket bound"; else bad "identity socket bound"; fi
amode="$(stat -f '%Lp' "$ASOCK" 2>/dev/null || stat -c '%a' "$ASOCK" 2>/dev/null)"
if [ "$amode" = "600" ]; then ok "identity socket 0600"; else bad "identity socket 0600 (got $amode)"; fi
if [ -f "$ASIDE" ] && grep -q '"name":"alice"' "$ASIDE" && grep -q '"version":"communicate-pm"' "$ASIDE"; then
  ok "sweep-proof sidecar planted (compact)"
else bad "sweep-proof sidecar planted"; fi
dpid="$(cat "$PMS/daemon.pid")"
if grep -q "\"pid\":$dpid" "$ASIDE"; then ok "sidecar pid is daemon's (live)"; else bad "sidecar pid is daemon's"; fi
st="$("$COMM" pm status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget identities alice kind)" = "local" ]; then ok "status lists alice"; else bad "status lists alice"; fi
if [ "$(printf '%s' "$st" | jget self socks pm-alice.sock state)" = "live" ]; then ok "alice sock self-probed live"; else bad "alice sock self-probed live"; fi
if [ -d "$PMS/mail/alice" ]; then ok "mailbox dir created"; else bad "mailbox dir created"; fi
if "$COMM" pm release alice >/dev/null 2>&1; then ok "release alice"; else bad "release alice"; fi
sleep 0.3
if [ ! -S "$ASOCK" ] && [ ! -f "$ASIDE" ]; then ok "release unbinds + unplants"; else bad "release unbinds + unplants"; fi
"$COMM" pm claim alice >/dev/null 2>&1
"$COMM" pm stop >/dev/null 2>&1; sleep 0.5
"$COMM" pm start >/dev/null 2>&1; sleep 1.5
if [ -S "$ASOCK" ] && [ -f "$ASIDE" ]; then ok "claim survives restart"; else bad "claim survives restart"; fi
"$COMM" pm stop >/dev/null 2>&1

echo "== section 4: inbox store + dedup"
"$COMM" pm start >/dev/null 2>&1
"$COMM" pm claim alice >/dev/null 2>&1
ASOCK="$PM_SOCK_DIR/pm-alice.sock"
AINBOX="$PMS/mail/alice/inbox.jsonl"
python3 "$HERE/lib/cc_peer.py" send --to "$ASOCK" --text "hello alice" \
  --from "$T/sender.sock" --name tester 2>/dev/null
sleep 0.5
if [ "$(wc -l < "$AINBOX" 2>/dev/null | tr -d ' ')" = "1" ]; then ok "frame stored"; else bad "frame stored"; fi
if grep -q '"text": "hello alice"' "$AINBOX" && grep -q '"from_name": "tester"' "$AINBOX"; then
  ok "stored unwrapped text + attribution"
else bad "stored unwrapped text + attribution"; fi
python3 - "$ASOCK" <<'PY'
import json, socket, sys
frame = {"type": "user", "message": {"role": "user", "content": "dup-test"},
         "priority": "next", "from": "uds:/tmp/nowhere.sock", "msg_id": "fixed123"}
for _ in range(2):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5); s.connect(sys.argv[1])
    s.sendall((json.dumps(frame) + "\n").encode()); s.close()
PY
sleep 0.5
if [ "$(grep -c 'fixed123' "$AINBOX")" = "1" ]; then ok "msg_id dedup"; else bad "msg_id dedup"; fi
if "$COMM" pm send alice "via control op" --from opsender >/dev/null 2>&1; then ok "pm send (local)"; else bad "pm send (local)"; fi
sleep 0.3
if grep -q '"text": "via control op"' "$AINBOX"; then ok "pm send stored"; else bad "pm send stored"; fi
if [ "$("$COMM" pm inbox alice 2>/dev/null | wc -l | tr -d ' ')" = "3" ]; then ok "pm inbox prints 3"; else bad "pm inbox prints 3"; fi

echo "== section 5: store→wake"
"$COMM" pm claim bob >/dev/null 2>&1
BINBOX="$PMS/mail/bob/inbox.jsonl"
BCUR="$PMS/mail/bob/.cursor"
"$COMM" pm send bob "M1 while down" --from opsender >/dev/null 2>&1
sleep 0.5
if [ "$(cat "$BCUR" 2>/dev/null || echo 0)" = "0" ]; then ok "M1 held (cursor 0)"; else bad "M1 held (cursor 0)"; fi
STANDIN_SOCK="$PM_SOCK_DIR/real-bob.sock"
STANDIN_MAIL="$T/bob-standin.jsonl"
python3 "$HERE/lib/cc_peer.py" mailbox --socket "$STANDIN_SOCK" --name bob \
  --sessions-dir "$PM_SESSIONS_DIR" --mailbox "$STANDIN_MAIL" >/dev/null 2>&1 &
STANDIN_PID=$!
deadline=$((SECONDS + 8)); woke=0
while [ $SECONDS -lt $deadline ]; do
  grep -q "M1 while down" "$STANDIN_MAIL" 2>/dev/null && { woke=1; break; }
  sleep 0.5
done
if [ "$woke" = "1" ]; then ok "store→wake drained M1 into live session"; else bad "store→wake drained M1"; fi
if [ "$(cat "$BCUR" 2>/dev/null || echo 0)" = "1" ]; then ok "cursor advanced"; else bad "cursor advanced"; fi
sleep 1.5
if [ ! -f "$PM_SESSIONS_DIR/pm-bob.json" ]; then ok "pm sidecar unplanted while live"; else bad "pm sidecar unplanted while live"; fi
"$COMM" pm send bob "M2 while live" --from opsender >/dev/null 2>&1
deadline=$((SECONDS + 6)); m2=0
while [ $SECONDS -lt $deadline ]; do
  grep -q "M2 while live" "$STANDIN_MAIL" 2>/dev/null && { m2=1; break; }
  sleep 0.5
done
if [ "$m2" = "1" ]; then ok "live delivery M2"; else bad "live delivery M2"; fi
kill "$STANDIN_PID" 2>/dev/null; sleep 2.5
if [ -f "$PM_SESSIONS_DIR/pm-bob.json" ]; then ok "sidecar replanted after death"; else bad "sidecar replanted after death"; fi
"$COMM" pm send bob "M3 after death" --from opsender >/dev/null 2>&1
sleep 0.5
if [ "$(cat "$BCUR" 2>/dev/null)" = "2" ] && grep -q "M3 after death" "$BINBOX"; then
  ok "M3 held durably"
else bad "M3 held durably"; fi
"$COMM" pm stop >/dev/null 2>&1

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
