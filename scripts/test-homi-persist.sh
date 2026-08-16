#!/usr/bin/env bash
# MANUAL launchd persistence test (macOS). Installs a TEST LaunchAgent under
# an isolated label + state dir, kill -9s the daemon, asserts KeepAlive
# resurrects it, then uninstalls. Touches launchd for real — run by hand.
set -uo pipefail
[ "$(uname -s)" = "Darwin" ] || { echo "skip: launchd test is macOS-only"; exit 0; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-persist.XXXXXX)"
export COMM_STATE="$T/state"
export HOMI_SOCK_DIR="$T/socks"
export HOMI_SESSIONS_DIR="$T/sessions"
export HOMI_SELF="persistest"
export HOMI_LABEL="com.communicate.homi-test"
mkdir -p "$HOMI_SESSIONS_DIR"
HOMIS="$COMM_STATE/homi"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { "$COMM" homi uninstall >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

echo "== install (isolated label: $HOMI_LABEL)"
if "$COMM" homi install >/dev/null 2>&1; then ok "pm install"; else bad "pm install"; fi
if "$COMM" homi status >/dev/null 2>&1; then ok "managed daemon answers"; else bad "managed daemon answers"; fi

echo "== KeepAlive: kill -9 must not be fatal"
PID1="$(cat "$HOMIS/daemon.pid" 2>/dev/null)"
[ -n "$PID1" ] || bad "read pid"
kill -9 "$PID1" 2>/dev/null
deadline=$((SECONDS + 15)); revived=0
while [ $SECONDS -lt $deadline ]; do
  PID2="$(cat "$HOMIS/daemon.pid" 2>/dev/null)"
  if [ -n "$PID2" ] && [ "$PID2" != "$PID1" ] && "$COMM" homi status >/dev/null 2>&1; then
    revived=1; break
  fi
  sleep 0.5
done
if [ "$revived" = "1" ]; then ok "launchd respawned the daemon (pid $PID1 -> $PID2)"; else bad "launchd respawned the daemon"; fi

echo "== uninstall"
if "$COMM" homi uninstall >/dev/null 2>&1; then ok "pm uninstall"; else bad "pm uninstall"; fi
sleep 1.5
if "$COMM" homi status >/dev/null 2>&1; then bad "gone after uninstall"; else ok "gone after uninstall"; fi
if [ ! -f "$HOME/Library/LaunchAgents/$HOMI_LABEL.plist" ]; then ok "plist removed"; else bad "plist removed"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
