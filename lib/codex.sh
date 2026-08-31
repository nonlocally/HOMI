# shellcheck shell=bash
# communicate :: codex — talk to a Codex agent on any device over pure ssh.
#
# Codex has no peer socket, so the honest primitive is request/response:
# `codex exec` run non-interactively on the target device. We:
#   * pass the message on STDIN (never on a command line) so arbitrary text —
#     quotes, backticks, $(), newlines — cannot break the remote shell parse;
#   * use --json to capture BOTH the final assistant text and the thread id, so
#     follow-up asks can `codex exec resume` the same session for continuity;
#   * default to the read-only sandbox (safe, conversational; never blocks on
#     approvals). --auto opts into workspace-write for an agent that acts.

_codex_state_dir() { printf '%s/codex\n' "$COMM_STATE"; }

_codex_thread_file() {
  local dev="$1" thread="$2"
  printf '%s/%s.%s.id\n' "$(_codex_state_dir)" "$(printf '%s' "$dev" | tr '/@: ' '____')" "$thread"
}

# Parse codex --json JSONL on stdin -> "<thread_id>\t<final agent text>".
_codex_parse() {
  python3 -c '
import sys, json
tid = ""; text = ""
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: e = json.loads(line)
    except Exception: continue
    if e.get("type") == "thread.started" and e.get("thread_id"):
        tid = e["thread_id"]
    it = e.get("item") if isinstance(e.get("item"), dict) else None
    if e.get("type") == "item.completed" and it and it.get("type") == "agent_message":
        text = it.get("text", text)
sys.stdout.write(tid + "\t" + text)
'
}

# Codex installs commonly land in ~/.local/bin (a symlink to the standalone
# release) which is NOT on the PATH of a non-interactive ssh shell — so a bare
# `command -v codex` over ssh misses it. Prepend the well-known codex locations
# to PATH for every remote codex invocation so dispatch works without touching
# the remote's shell profile. Override with $COMM_CODEX_PATH if codex lives
# elsewhere on a device.
_codex_path_prefix() {
  printf 'export PATH="%s:$PATH"; ' \
    "${COMM_CODEX_PATH:-\$HOME/.local/bin:\$HOME/.codex/packages/standalone/current/bin}"
}

# Is codex installed on a device? Prints version or empty.
codex_probe() {
  local dev="$1"
  on_device "$dev" "$(_codex_path_prefix)"'command -v codex >/dev/null 2>&1 && codex --version 2>/dev/null || true'
}

# Queue a turn for an existing Codex session. This is deliberately
# asynchronous and persistent: success means enqueued, while the reply remains
# in that session's UI/history after it consumes the turn. Unlike
# `codex_ask --thread`, this targets a native Codex UUID or exact session name
# and never creates a replacement session when the target is absent.
#
# Usage: codex_queue <device> <session-name|uuid> [--] <message...>
codex_queue() {
  comm_need_python
  local dev="${1:-}"; shift || true
  local target="${1:-}"; shift || true
  [ -n "$dev" ] && [ -n "$target" ] || \
    die "usage: communicate codex queue <device> <session-name|uuid> <message>"

  local -a msg=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --) shift; msg+=("$@"); break;;
      *)  msg+=("$1"); shift;;
    esac
  done
  [ "${#msg[@]}" -gt 0 ] || die "empty message"
  device_reachable "$dev" || die "device '$dev' not reachable over ssh"
  [ -n "$(codex_probe "$dev")" ] || die "codex is not installed on '$dev'"

  local text
  printf -v text '%s ' "${msg[@]}"
  text="${text% }"
  local pp; pp="$(_codex_path_prefix)"

  # `codex queue` requires --message rather than accepting stdin. Send the
  # target and text as a NUL-delimited payload to a tiny Python argv adapter so
  # neither is interpolated into a local or remote shell command; quotes,
  # newlines, and shell syntax stay literal.
  local py
  py='import subprocess, sys
target, message = sys.stdin.buffer.read().split(b"\0", 1)
p = subprocess.run(["codex", "queue", "--thread=" + target.decode(),
                    "--message=" + message.decode()])
raise SystemExit(p.returncode)'
  local remote_cmd="${pp}python3 -c $(comm_shq "$py")"

  if comm_is_local "$dev"; then
    printf '%s\0%s' "$target" "$text" | bash -c "$remote_cmd"
  else
    # Force no PTY: a host-level RequestTTY setting could otherwise transform
    # or buffer the NUL-delimited binary payload.
    printf '%s\0%s' "$target" "$text" | ssh -T "${COMM_SSH_OPTS[@]}" "$dev" "$remote_cmd"
  fi
}

# Ask a Codex agent a question and print its reply.
# Usage: codex_ask <device> [--dir D] [--thread NAME] [--new] [--auto] [--model M] -- <message...>
codex_ask() {
  comm_need_python
  local dev="$1"; shift || true
  [ -n "$dev" ] || die "usage: communicate codex ask <device> [opts] <message>"

  local dir="." thread="default" fresh=0 auto=0 model=""
  local -a msg=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir)    dir="$2"; shift 2;;
      --thread) thread="$2"; shift 2;;
      --new)    fresh=1; shift;;
      --auto)   auto=1; shift;;
      --model)  model="$2"; shift 2;;
      --)       shift; msg+=("$@"); break;;
      *)        msg+=("$1"); shift;;
    esac
  done
  [ "${#msg[@]}" -gt 0 ] || die "empty message"
  device_reachable "$dev" || die "device '$dev' not reachable over ssh"
  [ -n "$(codex_probe "$dev")" ] || die "codex is not installed on '$dev'"

  local text; text="$(printf '%s ' "${msg[@]}")"; text="${text% }"
  local sandbox="read-only"; [ "$auto" -eq 1 ] && sandbox="workspace-write"
  local tf; tf="$(_codex_thread_file "$dev" "$thread")"; mkdir -p "$(dirname "$tf")"
  local tid=""; [ "$fresh" -eq 0 ] && [ -f "$tf" ] && tid="$(cat "$tf")"

  local mopt=""; [ -n "$model" ] && mopt="-m $(comm_shq "$model")"
  local pp; pp="$(_codex_path_prefix)"
  local remote_cmd
  if [ -n "$tid" ]; then
    # Resume: no -s/-C/--color on resume; use config override + remote cd.
    remote_cmd="${pp}cd $(comm_remote_path "$dir") 2>/dev/null; codex exec resume $(comm_shq "$tid") --json --skip-git-repo-check -c sandbox_mode='\"$sandbox\"' $mopt -"
  else
    remote_cmd="${pp}codex exec -s $sandbox -C $(comm_remote_path "$dir") --json --color never --skip-git-repo-check $mopt -"
  fi

  local out rc parsed newtid reply
  if comm_is_local "$dev"; then
    out="$(printf '%s' "$text" | bash -c "$remote_cmd" 2>/dev/null)"; rc=$?
  else
    out="$(printf '%s' "$text" | ssh "${COMM_SSH_OPTS[@]}" "$dev" "$remote_cmd" 2>/dev/null)"; rc=$?
  fi
  if [ "$rc" -ne 0 ] && [ -z "$out" ]; then
    # Resume can fail if the stored session rolled off; retry fresh once.
    if [ -n "$tid" ]; then
      warn "codex resume failed on $dev; starting a fresh thread"
      rm -f "$tf"
      local -a af=(); [ "$auto" -eq 1 ] && af=(--auto)
      codex_ask "$dev" --dir "$dir" --thread "$thread" --new "${af[@]}" -- "${msg[@]}"; return $?
    fi
    die "codex exec failed on $dev (exit $rc)"
  fi
  parsed="$(printf '%s' "$out" | _codex_parse)"
  newtid="${parsed%%$'\t'*}"; reply="${parsed#*$'\t'}"
  [ -n "$newtid" ] && printf '%s' "$newtid" > "$tf"
  printf '%s\n' "$reply"
}

# Forget stored Codex thread ids. Usage: codex_forget <device|all>
codex_forget() {
  local target="${1:-all}"
  if [ "$target" = "all" ]; then rm -f "$(_codex_state_dir)"/*.id 2>/dev/null || true
  else rm -f "$(_codex_state_dir)/$(printf '%s' "$target" | tr '/@: ' '____')".*.id 2>/dev/null || true; fi
  ok "cleared codex thread memory for $target"
}
