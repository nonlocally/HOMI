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

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
