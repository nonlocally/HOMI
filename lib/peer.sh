# shellcheck shell=bash
# communicate :: peer — present a Codex agent as a NATIVE Claude peer, and a raw
# peer-socket sender. This is the glue that makes "any agent = a name + a
# socket": we stand up a unix socket that speaks the cc-socks wire protocol,
# plant a sidecar so Claude's discovery lists it, and back it with `codex exec`.

CC_PEER_PY="${CC_PEER_PY:-$COMM_HOME/lib/cc_peer.py}"

# Resolve a peer NAME (or socket path) to a socket path using LOCAL sidecars.
_peer_resolve() {
  local target="$1"
  if [ -S "$target" ]; then printf '%s\n' "$target"; return 0; fi
  local dir; dir="$(comm_sessions_dir)"
  python3 -c '
import sys, json, glob, os
name = sys.argv[1]; d = sys.argv[2]
best = None
for f in glob.glob(os.path.join(d, "*.json")):
    try: r = json.load(open(f))
    except Exception: continue
    if r.get("name") == name and r.get("messagingSocketPath"):
        if best is None or r.get("startedAt",0) > best.get("startedAt",0):
            best = r
if best: sys.stdout.write(best["messagingSocketPath"])
' "$target" "$dir"
}

_peer_state_dir() { printf '%s/peers/%s\n' "$COMM_STATE" "$1"; }

# Present a remote (or local) Codex as a native Claude peer.
# Usage: codex_peer <device> [name] [--dir D] [--auto]
codex_peer() {
  comm_need_python
  local dev="$1"; shift || true
  [ -n "$dev" ] || die "usage: communicate codex peer <device> [name] [--dir D] [--auto]"
  local devsan; devsan="$(printf '%s' "$dev" | tr '/@: ' '____')"
  local name="codex-$devsan"
  local dir="" auto=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) dir="$2"; shift 2;;
      --auto) auto=1; shift;;
      --*) die "unknown option $1";;
      *) name="$1"; shift;;
    esac
  done
  device_reachable "$dev" || die "device '$dev' not reachable over ssh"
  [ -n "$(codex_probe "$dev")" ] || die "codex is not installed on '$dev'"

  local sdir; sdir="$(comm_socket_dir)"; comm_ensure_socket_dir "$sdir"
  # A synthetic, collision-checked id for the socket filename. (The sidecar is
  # named by the daemon's own live pid, so the discovery sweep can't reap it.)
  local id sock
  while :; do
    id=$(( 900000 + RANDOM % 90000 )); sock="$sdir/$id.sock"
    [ -e "$sock" ] || break
  done

  local -a dargs=(serve --socket "$sock" --device "$dev" --name "$name"
                  --communicate "$COMM_HOME/bin/communicate" --sessions-dir "$(comm_sessions_dir)")
  [ -n "$dir" ] && dargs+=(--dir "$dir")
  [ "$auto" -eq 1 ] && dargs+=(--auto)

  local sd; sd="$(_peer_state_dir "$devsan-$id")"; mkdir -p "$sd"
  log "starting codex peer '$name' -> codex@$dev ..."
  nohup python3 "$CC_PEER_PY" "${dargs[@]}" >"$sd/daemon.log" 2>&1 &
  local dp=$!; echo "$dp" > "$sd/daemon.pid"; disown "$dp" 2>/dev/null || true

  # The daemon binds the socket and plants sessions/<its-pid>.json itself.
  local sidecar="$(comm_sessions_dir)/$dp.json"
  local i
  for i in $(seq 1 20); do [ -S "$sock" ] && [ -e "$sidecar" ] && break; sleep 0.25; done
  { [ -S "$sock" ] && [ -e "$sidecar" ]; } || { err "daemon failed to come up (see $sd/daemon.log)"; kill "$dp" 2>/dev/null; return 1; }
  {
    printf 'device=%s\n' "$dev"; printf 'name=%s\n' "$name"
    printf 'socket=%s\n' "$sock"; printf 'sidecar=%s\n' "$sidecar"; printf 'id=%s\n' "$id"
  } > "$sd/meta"
  ok "codex peer '$name' is live — SendMessage/ListAgents can now reach it"
  log "  messages route: Claude -> '$name' -> codex exec @$dev -> reply"
  log "  tear down with: communicate codex unpeer $dev"
}

# Internal: exec the daemon (kept so `communicate __codex-peer-serve` works too).
_codex_peer_serve() { exec python3 "$CC_PEER_PY" serve "$@"; }

# Tear down codex peer(s). Usage: codex_unpeer <device|all>
codex_unpeer() {
  local target="${1:-all}"
  local devsan; devsan="$(printf '%s' "$target" | tr '/@: ' '____')"
  local sd found=0
  for sd in "$COMM_STATE"/peers/*/; do
    [ -f "$sd/meta" ] || continue
    local device name socket sidecar
    while IFS='=' read -r k v; do case "$k" in device) device="$v";; name) name="$v";; socket) socket="$v";; sidecar) sidecar="$v";; esac; done < "$sd/meta"
    if [ "$target" = "all" ] || [ "$device" = "$target" ] || [ "$(printf '%s' "$device" | tr '/@: ' '____')" = "$devsan" ]; then
      found=1
      log "removing codex peer '$name' ($device) ..."
      [ -f "$sd/daemon.pid" ] && kill "$(cat "$sd/daemon.pid")" 2>/dev/null || true
      [ -n "$sidecar" ] && rm -f "$sidecar" 2>/dev/null || true
      [ -n "$socket" ] && rm -f "$socket" 2>/dev/null || true
      rm -rf "$sd"
      ok "codex peer '$name' removed"
    fi
  done
  [ "$found" -eq 0 ] && warn "no codex peers matched '$target'"
  return 0
}

# Raw-inject a message into a peer socket, by NAME or socket path.
# Usage: peer_send <name|socket> [--as NAME] <message...>
peer_send() {
  comm_need_python
  local target="$1"; shift || true
  [ -n "$target" ] || die "usage: communicate send <peer-name|socket> [--as NAME] <message>"
  local as="communicate"
  local -a msg=()
  while [ $# -gt 0 ]; do
    case "$1" in --as) as="$2"; shift 2;; --) shift; msg+=("$@"); break;; *) msg+=("$1"); shift;; esac
  done
  [ "${#msg[@]}" -gt 0 ] || die "empty message"
  local sock; sock="$(_peer_resolve "$target")"
  [ -n "$sock" ] || die "no peer named '$target' (try: communicate agents)"
  [ -S "$sock" ] || die "peer '$target' socket not live: $sock"
  local from="${CLAUDE_CODE_MESSAGING_SOCKET:-$(comm_socket_dir)/communicate-cli.sock}"
  local text; text="$(printf '%s ' "${msg[@]}")"; text="${text% }"
  python3 "$CC_PEER_PY" send --to "$sock" --text "$text" --from "$from" --name "$as"
  ok "delivered to '$target'"
}
