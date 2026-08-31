#!/usr/bin/env bash
# Deterministic local test for `communicate codex queue`: use a fake Codex CLI
# to verify that a native session name and awkward message text survive the
# stdin -> Python argv adapter exactly, without shell interpretation. Exercises
# both local and fake-SSH paths, option-looking text, trailing newlines, and
# downstream failure propagation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/state"

cat > "$T/bin/codex" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli 0.151.0"
  exit 0
fi
[ "${1:-}" = "queue" ] || { echo "unexpected command: $*" >&2; exit 2; }
shift
thread=""; message=""
while [ $# -gt 0 ]; do
  case "$1" in
    --thread=*)  thread="${1#--thread=}"; shift;;
    --message=*) message="${1#--message=}"; shift;;
    *) echo "unexpected argument: $1" >&2; exit 2;;
  esac
done
[ "$thread" != "fail-target" ] || exit 37
printf '%s' "$thread" > "$FAKE_CODEX_THREAD_FILE"
printf '%s' "$message" > "$FAKE_CODEX_MESSAGE_FILE"
echo "Queued fake message for $thread"
SH
chmod +x "$T/bin/codex"

cat > "$T/bin/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
saw_no_tty=0
while [ $# -gt 0 ]; do
  case "$1" in
    -o) shift 2;;
    -T) saw_no_tty=1; shift;;
    -*) shift;;
    *) break;;
  esac
done
[ $# -ge 2 ] || { echo "fake ssh: missing device/command" >&2; exit 2; }
device="$1"; shift
[ "$device" = "fake-device" ] || { echo "fake ssh: wrong device $device" >&2; exit 2; }
printf '%s %s\n' "$device" "$saw_no_tty" >> "$FAKE_SSH_LOG"
bash -c "$1"
SH
chmod +x "$T/bin/ssh"

export COMM_CODEX_PATH="$T/bin"
export COMM_STATE="$T/state"
export FAKE_CODEX_THREAD_FILE="$T/thread"
export FAKE_CODEX_MESSAGE_FILE="$T/message"
export FAKE_SSH_LOG="$T/ssh.log"
export PATH="$T/bin:$PATH"

assert_payload() {
  local label="$1" expected_thread="$2" expected_message="$3"
  printf '%s' "$expected_thread" > "$T/expected-thread"
  printf '%s' "$expected_message" > "$T/expected-message"
  cmp -s "$T/expected-thread" "$FAKE_CODEX_THREAD_FILE" || {
    echo "FAIL ($label): session name changed in transit"; exit 1;
  }
  cmp -s "$T/expected-message" "$FAKE_CODEX_MESSAGE_FILE" || {
    echo "FAIL ($label): message changed in transit"; exit 1;
  }
}

LOCAL_THREAD="say hi 'exactly'"
LOCAL_MESSAGE=$'--help\nLiteral shell: $HOME `uname` $(id)\n\n'
LOCAL_OUT="$("$CLI" codex queue local "$LOCAL_THREAD" -- "$LOCAL_MESSAGE")"
assert_payload local "$LOCAL_THREAD" "$LOCAL_MESSAGE"
printf '%s' "$LOCAL_OUT" | grep -q "Queued fake message" || {
  echo "FAIL (local): queue output was not forwarded"; exit 1;
}

REMOTE_THREAD="remote 'session'"
REMOTE_MESSAGE=$'-status\nRemote literal payload\n\n'
REMOTE_OUT="$("$CLI" codex queue fake-device "$REMOTE_THREAD" -- "$REMOTE_MESSAGE")"
assert_payload remote "$REMOTE_THREAD" "$REMOTE_MESSAGE"
printf '%s' "$REMOTE_OUT" | grep -q "Queued fake message" || {
  echo "FAIL (remote): queue output was not forwarded"; exit 1;
}
grep -q '^fake-device 1$' "$FAKE_SSH_LOG" || {
  echo "FAIL: queue SSH path did not force no-PTY mode"; exit 1;
}

set +e
"$CLI" codex queue local fail-target -- "must fail" >/dev/null 2>&1
FAIL_RC=$?
set -e
[ "$FAIL_RC" -eq 37 ] || {
  echo "FAIL: downstream exit 37 became $FAIL_RC"; exit 1;
}

echo "PASS: codex queue preserves payloads locally/remotely and propagates failures"
