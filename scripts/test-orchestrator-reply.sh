#!/usr/bin/env bash
# End-to-end test of the orchestrator reply-capture path, LOCAL and deterministic
# (no ssh, no remote device): start the orchestrator mailbox, stand up a
# cooperating peer that auto-replies, send from the orchestrator identity, and
# assert the reply is captured in the mailbox. Proves the mechanism the
# dispatch tool's Claude path relies on.
#
# Requires: python3.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/lib/cc_peer.py"
SOCKDIR="${XDG_RUNTIME_DIR:-/tmp}/cc-socks"
mkdir -p "$SOCKDIR"; chmod 700 "$SOCKDIR" 2>/dev/null || true
STATE="$(mktemp -d)"
ORCH="$SOCKDIR/test-orch-$$.sock"
PEER="$SOCKDIR/test-peer-$$.sock"
MBOX="$STATE/mailbox.jsonl"
SESS="$STATE/sessions"; mkdir -p "$SESS"

cleanup() {
  [ -n "${MBPID:-}" ] && kill "$MBPID" 2>/dev/null
  [ -n "${RPID:-}" ] && kill "$RPID" 2>/dev/null
  rm -f "$ORCH" "$PEER"; rm -rf "$STATE"
}
trap cleanup EXIT

echo "1) start the orchestrator mailbox"
python3 "$PY" mailbox --socket "$ORCH" --name orchestrator \
  --sessions-dir "$SESS" --mailbox "$MBOX" >"$STATE/mb.log" 2>&1 &
MBPID=$!
for i in $(seq 1 20); do [ -S "$ORCH" ] && break; sleep 0.2; done
[ -S "$ORCH" ] || { echo "FAIL: mailbox socket never appeared"; exit 1; }

echo "2) stand up a cooperating peer that auto-replies once"
python3 "$PY" respond --socket "$PEER" --reply "REPLY-OK" --name test-peer --timeout 30 \
  >"$STATE/resp.log" 2>&1 &
RPID=$!
for i in $(seq 1 20); do [ -S "$PEER" ] && break; sleep 0.2; done
[ -S "$PEER" ] || { echo "FAIL: peer socket never appeared"; exit 1; }

echo "3) send from the orchestrator identity to the peer"
python3 "$PY" send --to "$PEER" --from "$ORCH" --name orchestrator \
  --text "please reply for the test"

echo "4) wait for the reply to land in the mailbox"
for i in $(seq 1 40); do
  [ -s "$MBOX" ] && break; sleep 0.25
done
if grep -q 'REPLY-OK' "$MBOX" 2>/dev/null; then
  echo "PASS: orchestrator captured the peer reply"
  exit 0
else
  echo "FAIL: no reply captured"; cat "$MBOX" 2>/dev/null
  exit 1
fi
