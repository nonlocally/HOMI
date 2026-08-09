# shellcheck shell=bash
# communicate :: claude — bridge a remote Claude Code session so it appears as a
# native local peer to `SendMessage` / `ListAgents`.
#
# Mechanism (see docs/MECHANISM.md):
#   1. Claude discovers peers by reading sidecar JSON files in ~/.claude/sessions/
#      and probing the `messagingSocketPath` each names for liveness.
#   2. So we forward the remote session's unix socket to the *same absolute path*
#      on this machine (ssh -L), and forward our own socket to the same path on
#      the remote (ssh -R) for the reply direction.
#   3. Then we plant each session's sidecar on the other machine, so both
#      discovery layers list the other as a local peer.
#   4. A supervisor keeps the tunnel up and re-plants sidecars so the periodic
#      "dead peer" sweep self-heals.

# Stream every remote sidecar object, newline-delimited, to stdout.
_claude_sidecars_on() {
  local dev="$1"
  on_device "$dev" 'for f in "$HOME"/.claude/sessions/*.json; do [ -e "$f" ] && cat "$f" && echo; done'
}

# Pick one session sidecar JSON by selector. Selector: a name, a numeric pid, or
# empty/"newest" for the most recently started interactive session. Prints the
# chosen object as compact JSON, or nothing.
_claude_pick() {
  local dev="$1" sel="${2:-newest}"
  _claude_sidecars_on "$dev" | python3 -c '
import sys, json
sel = sys.argv[1]
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except Exception:
        pass
def is_pid(s): return s.isdigit()
chosen = None
if sel in ("", "newest"):
    cand = [r for r in rows if r.get("kind") == "interactive" and r.get("messagingSocketPath")]
    chosen = max(cand, key=lambda r: r.get("startedAt", 0)) if cand else None
elif is_pid(sel):
    chosen = next((r for r in rows if str(r.get("pid")) == sel), None)
else:
    m = [r for r in rows if r.get("name") == sel and r.get("messagingSocketPath")]
    chosen = max(m, key=lambda r: r.get("startedAt", 0)) if m else None
if chosen:
    sys.stdout.write(json.dumps(chosen))
' "$sel"
}

# List Claude sessions on a device in a readable table.
claude_list() {
  local dev="$1"
  device_reachable "$dev" || die "device '$dev' not reachable over ssh"
  _claude_sidecars_on "$dev" | python3 -c '
import sys, json, time
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: rows.append(json.loads(line))
    except Exception: pass
rows.sort(key=lambda r: r.get("startedAt",0), reverse=True)
if not rows:
    print("  (no Claude sessions)"); raise SystemExit
print("  %-22s %-7s %-12s %s" % ("NAME","PID","STATUS","CWD"))
for r in rows:
    print("  %-22s %-7s %-12s %s" % (
        (r.get("name") or "(unnamed)")[:22],
        str(r.get("pid","?")),
        (r.get("status") or "?")[:12],
        r.get("cwd","?")))
'
}

# Path to our own live sidecar (the session we are running inside).
_claude_my_sidecar() {
  local dir; dir="$(comm_sessions_dir)"
  # Prefer the sidecar whose messagingSocketPath matches our env socket.
  local mysock="${CLAUDE_CODE_MESSAGING_SOCKET:-}"
  [ -n "$mysock" ] || return 1
  local f
  for f in "$dir"/*.json; do
    [ -e "$f" ] || continue
    if grep -q "\"messagingSocketPath\":\"$mysock\"" "$f" 2>/dev/null; then
      printf '%s\n' "$f"; return 0
    fi
  done
  return 1
}

_bridge_state_dir() {
  local dev="$1"
  printf '%s/bridges/%s\n' "$COMM_STATE" "$(printf '%s' "$dev" | tr '/@: ' '____')"
}

# Establish the bridge. Usage: claude_bridge <device> [selector]
claude_bridge() {
  comm_need_python
  local dev="$1" sel="${2:-newest}"
  [ -n "$dev" ] || die "usage: communicate claude bridge <device> [name|pid|newest]"
  comm_is_local "$dev" && die "bridging to 'local' is a no-op (local sessions are already peers)"
  device_reachable "$dev" || die "device '$dev' not reachable over ssh"

  local mysock="${CLAUDE_CODE_MESSAGING_SOCKET:-}"
  [ -n "$mysock" ] || die "not inside a Claude session (\$CLAUDE_CODE_MESSAGING_SOCKET unset); bridge from within one"
  local my_sidecar; my_sidecar="$(_claude_my_sidecar)" || die "could not locate my own session sidecar"

  log "selecting session '$sel' on $dev ..."
  local remote_json; remote_json="$(_claude_pick "$dev" "$sel")"
  [ -n "$remote_json" ] || die "no matching Claude session '$sel' on $dev (try: communicate ls $dev)"

  local rpid rname rpath
  rpid="$(printf '%s' "$remote_json"  | json_get pid)"
  rname="$(printf '%s' "$remote_json" | json_get name)"
  rpath="$(printf '%s' "$remote_json" | json_get messagingSocketPath)"
  [ -n "$rpath" ] || die "selected session has no messagingSocketPath"
  ok "target: ${rname:-unnamed} (pid $rpid) @ $rpath"

  local mdir rdir
  mdir="${mysock%/*}"; rdir="${rpath%/*}"
  if [ "$mdir" != "$rdir" ]; then
    warn "socket dirs differ (mine=$mdir theirs=$rdir); messages still flow but delivery receipts may be suppressed"
  fi

  # Ensure socket parent dirs exist on both ends (mode 700 like Claude uses).
  mkdir -p "$rdir" 2>/dev/null; chmod 700 "$rdir" 2>/dev/null || true
  on_device "$dev" "mkdir -p $(comm_shq "$mdir") && chmod 700 $(comm_shq "$mdir")" || true

  # Clear a stale reverse-bind path on the remote iff no real remote session owns it.
  local mybase="${mysock##*/}" mypid="${mybase%.sock}"
  if [ -z "$(_claude_pick "$dev" "$mypid")" ]; then
    on_device "$dev" "rm -f $(comm_shq "$mysock")" 2>/dev/null || true
  fi

  local sd; sd="$(_bridge_state_dir "$dev")"; mkdir -p "$sd"
  # Record everything teardown needs.
  {
    printf 'device=%s\n' "$dev"
    printf 'remote_pid=%s\n' "$rpid"
    printf 'remote_name=%s\n' "$rname"
    printf 'rpath=%s\n' "$rpath"
    printf 'mpath=%s\n' "$mysock"
    printf 'my_sidecar=%s\n' "$my_sidecar"
    printf 'local_planted=%s/%s.json\n' "$(comm_sessions_dir)" "$rpid"
    printf 'remote_planted=~/.claude/sessions/%s.json\n' "$mypid"
  } > "$sd/meta"

  log "planting sidecars + launching supervised tunnel ..."
  # Launch the supervisor detached. It owns the tunnel + periodic re-planting.
  nohup "$COMM_HOME/bin/communicate" __supervise-claude "$dev" >"$sd/supervisor.log" 2>&1 &
  local sup=$!
  echo "$sup" > "$sd/supervisor.pid"
  disown "$sup" 2>/dev/null || true

  # Wait for the forwarded socket + planted local sidecar to appear.
  local i
  for i in $(seq 1 20); do
    if [ -S "$rpath" ] && [ -e "$(comm_sessions_dir)/$rpid.json" ]; then
      ok "bridge up: '$rname' now reachable via SendMessage/ListAgents"
      log "  tear down with: communicate claude unbridge $dev"
      return 0
    fi
    sleep 0.5
  done
  warn "bridge started but socket/sidecar not confirmed yet; check: communicate status  (log: $sd/supervisor.log)"
}

# The supervisor loop (internal). Keeps the tunnel alive and re-plants sidecars.
# Runs in the foreground of a detached process.
_claude_supervise() {
  local dev="$1"
  local sd; sd="$(_bridge_state_dir "$dev")"
  [ -f "$sd/meta" ] || { err "no bridge state for $dev"; return 1; }
  # shellcheck disable=SC1090
  local device remote_pid remote_name rpath mpath my_sidecar local_planted remote_planted
  while IFS='=' read -r k v; do
    case "$k" in
      device) device="$v";; remote_pid) remote_pid="$v";; remote_name) remote_name="$v";;
      rpath) rpath="$v";; mpath) mpath="$v";; my_sidecar) my_sidecar="$v";;
      local_planted) local_planted="$v";; remote_planted) remote_planted="$v";;
    esac
  done < "$sd/meta"

  local mypid="${mpath##*/}"; mypid="${mypid%.sock}"

  _replant() {
    # Local: the remote session's sidecar, so *we* list it.
    _claude_pick "$dev" "$remote_pid" > "$local_planted" 2>/dev/null || \
      read_file_on "$dev" "~/.claude/sessions/$remote_pid.json" > "$local_planted" 2>/dev/null
    # Remote: our own sidecar, so *they* list us (and can name-resolve replies).
    write_file_on "$dev" "~/.claude/sessions/$mypid.json" < "$my_sidecar" 2>/dev/null || true
  }

  trap 'exit 0' TERM INT
  while :; do
    _replant
    # Bidirectional tunnel; mirror each socket to the identical path on the peer.
    ssh "${COMM_SSH_OPTS[@]}" \
        -o StreamLocalBindUnlink=yes -o ExitOnForwardFailure=yes -N \
        -L "$rpath:$rpath" \
        -R "$mpath:$mpath" \
        "$dev" &
    local tpid=$!
    echo "$tpid" > "$sd/tunnel.pid"
    # Re-plant every 5s while the tunnel lives (cheap; self-heals the sweep).
    while kill -0 "$tpid" 2>/dev/null; do
      sleep 5
      [ -e "$local_planted" ] || _replant
      wait "$tpid" 2>/dev/null && break
    done
    wait "$tpid" 2>/dev/null
    # Tunnel died; brief backoff then reconnect (unless we're being torn down).
    [ -f "$sd/stop" ] && break
    sleep 2
  done
}

# Tear down one bridge (or all). Usage: claude_unbridge <device|all>
claude_unbridge() {
  local target="${1:-}"
  [ -n "$target" ] || die "usage: communicate claude unbridge <device|all>"
  local dirs=()
  if [ "$target" = "all" ]; then
    for d in "$COMM_STATE"/bridges/*/; do [ -d "$d" ] && dirs+=("$d"); done
  else
    dirs=("$(_bridge_state_dir "$target")/")
  fi
  [ "${#dirs[@]}" -gt 0 ] || { warn "no active bridges"; return 0; }

  local sd
  for sd in "${dirs[@]}"; do
    [ -f "$sd/meta" ] || continue
    local device remote_pid rpath mpath local_planted
    while IFS='=' read -r k v; do
      case "$k" in device) device="$v";; remote_pid) remote_pid="$v";;
        rpath) rpath="$v";; mpath) mpath="$v";; local_planted) local_planted="$v";; esac
    done < "$sd/meta"
    local mypid="${mpath##*/}"; mypid="${mypid%.sock}"

    log "tearing down bridge to $device ..."
    touch "$sd/stop"
    [ -f "$sd/supervisor.pid" ] && kill "$(cat "$sd/supervisor.pid")" 2>/dev/null || true
    [ -f "$sd/tunnel.pid" ]     && kill "$(cat "$sd/tunnel.pid")"     2>/dev/null || true
    sleep 1
    # Remove the sidecars we planted (never touch real ones).
    [ -n "$local_planted" ] && rm -f "$local_planted" 2>/dev/null || true
    on_device "$device" "rm -f ~/.claude/sessions/$mypid.json" 2>/dev/null || true
    # Remove forwarded socket files (they are ours; the real sessions live under
    # different pids). StreamLocalBindUnlink usually handles the local one.
    [ -S "$rpath" ] && rm -f "$rpath" 2>/dev/null || true
    on_device "$device" "rm -f $(comm_shq "$mpath")" 2>/dev/null || true
    rm -rf "$sd"
    ok "bridge to $device removed"
  done
}
