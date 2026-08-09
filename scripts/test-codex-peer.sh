#!/usr/bin/env bash
# End-to-end test of Tier 3 (Codex-as-native-peer) with NO Claude session and no
# ssh: stand up a local Codex peer, send it a message over the cc-socks wire
# protocol, and capture the real codex reply on a throwaway listener socket.
#
# Requires: codex installed locally, python3.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
PY="$ROOT/lib/cc_peer.py"
SOCKDIR="${CLAUDE_CODE_MESSAGING_SOCKET:+$(dirname "$CLAUDE_CODE_MESSAGING_SOCKET")}"
SOCKDIR="${SOCKDIR:-${XDG_RUNTIME_DIR:-/tmp}/cc-socks}"
mkdir -p "$SOCKDIR"; chmod 700 "$SOCKDIR" 2>/dev/null || true

command -v codex >/dev/null || { echo "SKIP: codex not installed"; exit 0; }

NAME="test-peer-$$"
LISTEN="$SOCKDIR/test-listen-$$.sock"
OUT="$(mktemp)"
cleanup() { "$CLI" codex unpeer local >/dev/null 2>&1; rm -f "$LISTEN" "$OUT"; kill "$RECV" 2>/dev/null; }
trap cleanup EXIT

echo "1) start a listener for the reply"
python3 "$PY" recv --socket "$LISTEN" --timeout 90 >"$OUT" 2>/dev/null &
RECV=$!
for i in $(seq 1 20); do [ -S "$LISTEN" ] && break; sleep 0.2; done

echo "2) stand up a local codex peer"
"$CLI" codex peer local "$NAME" --dir /tmp >/dev/null || { echo "FAIL: could not start peer"; exit 1; }
PEERSOCK="$(sed -n 's/^socket=//p' "${XDG_STATE_HOME:-$HOME/.local/state}"/communicate/peers/*/meta | tail -1)"
[ -S "$PEERSOCK" ] || { echo "FAIL: peer socket missing"; exit 1; }

echo "3) send a message to the peer; reply routes to our listener"
python3 "$PY" send --to "$PEERSOCK" --from "$LISTEN" --name tester \
  --text "Reply with exactly the word PEERPONG. Do not run any tools."

echo "4) wait for the codex reply"
wait "$RECV" 2>/dev/null
REPLY="$(cat "$OUT")"
echo "   reply: ${REPLY:-<empty>}"

if printf '%s' "$REPLY" | grep -qi 'PEERPONG'; then
  echo "PASS: codex answered through the peer socket"
  exit 0
else
  echo "FAIL: no valid reply captured"
  exit 1
fi
