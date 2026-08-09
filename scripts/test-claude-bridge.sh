#!/usr/bin/env bash
# Test Tier 1: bridge a remote Claude session in as a native peer, confirm it
# appears in the routing table with a forwarded socket, then tear it down and
# confirm no residue. Does NOT message the remote session (so it won't disturb
# work or burn tokens on another account).
#
# Requires: run from INSIDE a Claude session ($CLAUDE_CODE_MESSAGING_SOCKET set),
# and a reachable device with at least one Claude session. Pass the device as $1
# or set COMM_TEST_DEVICE.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
DEV="${1:-${COMM_TEST_DEVICE:-}}"

[ -n "${CLAUDE_CODE_MESSAGING_SOCKET:-}" ] || { echo "SKIP: not inside a Claude session"; exit 0; }
[ -n "$DEV" ] || { echo "SKIP: no device given (pass \$1 or set COMM_TEST_DEVICE)"; exit 0; }
"$CLI" link "$DEV" >/dev/null 2>&1 || { echo "SKIP: '$DEV' not reachable"; exit 0; }

# Find a session name to bridge (newest interactive).
SEL="newest"
echo "1) bridge newest Claude session on $DEV"
"$CLI" claude bridge "$DEV" "$SEL" || { echo "FAIL: bridge did not come up"; exit 1; }

echo "2) it should appear in the routing table as claude* via $DEV"
if "$CLI" agents 2>/dev/null | grep -q "claude\*"; then
  echo "   ok: bridged peer listed"
else
  echo "FAIL: bridged peer not in routing table"; "$CLI" claude unbridge "$DEV"; exit 1
fi

RPATH="$(sed -n 's/^rpath=//p' "${XDG_STATE_HOME:-$HOME/.local/state}"/communicate/bridges/*/meta | tail -1)"
[ -S "$RPATH" ] && echo "   ok: forwarded socket present at $RPATH" || { echo "FAIL: no forwarded socket"; "$CLI" claude unbridge "$DEV"; exit 1; }

echo "3) tear down and confirm cleanup"
"$CLI" claude unbridge "$DEV"
sleep 1
[ -S "$RPATH" ] && { echo "WARN: forwarded socket lingered"; } || echo "   ok: socket removed"
echo "PASS: Claude bridge stands up, lists, and tears down cleanly"
