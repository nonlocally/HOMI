# shellcheck shell=bash
# communicate — shared helpers.
# Sourced by bin/communicate and the lib/*.sh modules. No side effects on source
# beyond defining functions and a few readonly-ish globals.

set -o pipefail

# ---- paths -----------------------------------------------------------------

# Where this checkout lives (bin/.. == repo root).
comm_home() {
  local src="${BASH_SOURCE[0]}"
  cd "$(dirname "$src")/.." && pwd
}
COMM_HOME="${COMM_HOME:-$(comm_home)}"

# Runtime state (bridge/peer bookkeeping, session-id memory). Never committed;
# lives outside the repo on purpose.
COMM_STATE="${COMM_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/communicate}"

# Claude Code's per-user session sidecar directory and messaging socket dir.
comm_sessions_dir() { printf '%s\n' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions"; }

# The directory Claude Code keeps its messaging sockets in. When we are running
# *inside* a Claude session, $CLAUDE_CODE_MESSAGING_SOCKET is authoritative;
# otherwise fall back to the documented default.
comm_socket_dir() {
  if [ -n "${CLAUDE_CODE_MESSAGING_SOCKET:-}" ]; then
    printf '%s\n' "${CLAUDE_CODE_MESSAGING_SOCKET%/*}"
  else
    printf '%s\n' "${XDG_RUNTIME_DIR:-/tmp}/cc-socks"
  fi
}

# ---- logging ---------------------------------------------------------------

_comm_tty() { [ -t 2 ]; }
_comm_c() { if _comm_tty; then printf '\033[%sm' "$1"; else printf ''; fi; }
log()  { printf '%s%s%s %s\n' "$(_comm_c '36')" "communicate" "$(_comm_c '0')" "$*" >&2; }
ok()   { printf '%s%s%s %s\n' "$(_comm_c '32')" "  ok" "$(_comm_c '0')" "$*" >&2; }
warn() { printf '%s%s%s %s\n' "$(_comm_c '33')" "warn" "$(_comm_c '0')" "$*" >&2; }
err()  { printf '%s%s%s %s\n' "$(_comm_c '31')" " err" "$(_comm_c '0')" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---- ssh / devices ---------------------------------------------------------
#
# A "device" is one of:
#   local            -> this machine, run commands directly (no ssh)
#   <tailscale-name> -> e.g. aadarshs-mac-mini-2 (resolved via MagicDNS)
#   user@host        -> any ssh target
#
# Transport is strictly ssh (over Tailscale or plain). Nothing else.

# StreamLocalBindMask=0177 makes ssh-forwarded unix sockets mode 0600 instead of
# inheriting the login umask (which is commonly 022 -> world-readable 0755).
COMM_SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StreamLocalBindMask=0177)

# Ensure a socket dir exists AND is safe (owned by us, mode 700). Refuses to use
# a pre-existing dir owned by someone else or group/world-accessible — otherwise
# a hostile local user could pre-create /tmp/cc-socks and read our sockets.
comm_ensure_socket_dir() {
  local dir="$1"
  mkdir -p "$dir" 2>/dev/null || true
  [ -d "$dir" ] || die "socket dir $dir does not exist and could not be created"
  chmod 700 "$dir" 2>/dev/null || true
  # BSD (macOS) vs GNU stat differ; try both.
  local owner mode
  owner="$(stat -f '%u' "$dir" 2>/dev/null || stat -c '%u' "$dir" 2>/dev/null)"
  mode="$(stat -f '%Lp' "$dir" 2>/dev/null || stat -c '%a' "$dir" 2>/dev/null)"
  [ "$owner" = "$(id -u)" ] || die "refusing socket dir $dir: not owned by us (owner uid=$owner)"
  case "$mode" in 700|0700) ;; *) die "refusing socket dir $dir: mode $mode is not 700";; esac
}

comm_is_local() { [ "$1" = "local" ] || [ "$1" = "localhost" ]; }

# Kill a pid for real. bash defers a trapped signal until a running `sleep`
# finishes, so a single SIGTERM can be ignored for the whole nap; escalate to
# SIGKILL if the process is still alive after a short grace period.
comm_kill_hard() {
  local pid="$1"; [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5 6; do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.25; done
  kill -KILL "$pid" 2>/dev/null || true
}

# Run a command on a device. Args after the device are joined into one remote
# command string. For local, runs under bash -c.
on_device() {
  local dev="$1"; shift
  if comm_is_local "$dev"; then
    bash -c "$*"
  else
    ssh "${COMM_SSH_OPTS[@]}" "$dev" "$*"
  fi
}

# Read a file's contents from a device (stdout).
read_file_on() {
  local dev="$1" path="$2"
  on_device "$dev" "cat $(comm_shq "$path") 2>/dev/null"
}

# Write stdin to a file on a device.
write_file_on() {
  local dev="$1" path="$2"
  if comm_is_local "$dev"; then
    cat > "$path"
  else
    ssh "${COMM_SSH_OPTS[@]}" "$dev" "cat > $(comm_shq "$path")"
  fi
}

# Shell-quote a single argument for safe interpolation into a remote command.
comm_shq() { printf "'%s'" "${1//\'/\'\\\'\'}"; }

# Verify a device is reachable over ssh (or is local). Returns 0/1.
device_reachable() {
  local dev="$1"
  comm_is_local "$dev" && return 0
  ssh "${COMM_SSH_OPTS[@]}" -o ConnectTimeout=6 "$dev" true 2>/dev/null
}

# Best-effort Tailscale presence check for nicer diagnostics. Never fatal.
tailscale_bin() {
  if command -v tailscale >/dev/null 2>&1; then command -v tailscale
  elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then
    printf '/Applications/Tailscale.app/Contents/MacOS/Tailscale\n'
  fi
}

# ---- json helpers (python3 is a hard dependency) ---------------------------

comm_need_python() { command -v python3 >/dev/null 2>&1 || die "python3 is required"; }

# Extract a top-level string field from a JSON blob on stdin.
json_get() {
  local key="$1"
  python3 -c 'import sys,json;
d=json.load(sys.stdin); v=d.get(sys.argv[1],"")
sys.stdout.write(v if isinstance(v,str) else (str(v) if v is not None else ""))' "$key"
}

mkdir -p "$COMM_STATE" 2>/dev/null || true
