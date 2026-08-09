# shellcheck shell=bash
# communicate :: wake — a heartbeat loop built on the router. Periodically send a
# message to an agent by name: to keep a long-running agent working, to poll one
# for status, or as a liveness ping. Because a message to an idle/completed
# session resumes it, this doubles as a wake-up.

_wake_state_dir() { printf '%s/wakes/%s\n' "$COMM_STATE" "$(printf '%s' "$1" | tr '/@: ' '____')"; }

# wake_start <name> [--every SEC] [--message MSG] [--times N]
wake_start() {
  local name="$1"; shift || true
  [ -n "$name" ] || die "usage: communicate wake <name> [--every SEC] [--message MSG] [--times N]"
  local every=300 times=0
  local message="(wake) automated heartbeat from communicate — continue your task or reply with status."
  while [ $# -gt 0 ]; do
    case "$1" in
      --every) every="$2"; shift 2;;
      --message|--msg) message="$2"; shift 2;;
      --times) times="$2"; shift 2;;
      *) die "unknown option $1";;
    esac
  done
  # Confirm the target resolves right now (fail fast with a helpful message).
  router_whereis "$name" >/dev/null 2>&1 || die "no agent named '$name' to wake (see: communicate agents)"

  local sd; sd="$(_wake_state_dir "$name")"; mkdir -p "$sd"; rm -f "$sd/stop"
  { printf 'name=%s\n' "$name"; printf 'every=%s\n' "$every"
    printf 'times=%s\n' "$times"; printf 'message=%s\n' "$message"; } > "$sd/meta"
  echo 0 > "$sd/count"

  nohup "$COMM_HOME/bin/communicate" __wake-loop "$name" >"$sd/wake.log" 2>&1 &
  local lp=$!; echo "$lp" > "$sd/loop.pid"; disown "$lp" 2>/dev/null || true
  ok "waking '$name' every ${every}s$([ "$times" -gt 0 ] && echo ", $times time(s)")"
  log "  stop with: communicate wake stop $name"
}

# Internal loop.
_wake_loop() {
  local name="$1"; local sd; sd="$(_wake_state_dir "$name")"
  [ -f "$sd/meta" ] || return 1
  local every times message
  while IFS='=' read -r k v; do case "$k" in every) every="$v";; times) times="$v";; message) message="$v";; esac; done < "$sd/meta"
  trap 'exit 0' TERM INT
  local n=0
  while :; do
    [ -f "$sd/stop" ] && break
    sleep "$every"
    [ -f "$sd/stop" ] && break
    if router_route "$name" -- "$message" >/dev/null 2>&1; then
      n=$((n+1)); echo "$n" > "$sd/count"
    else
      warn "wake '$name': target unreachable this tick (will retry)"
    fi
    [ "$times" -gt 0 ] && [ "$n" -ge "$times" ] && break
  done
  rm -f "$sd/loop.pid"
}

# wake_stop <name|all>
wake_stop() {
  local target="${1:-all}" sd found=0
  for sd in "$COMM_STATE"/wakes/*/; do
    [ -f "$sd/meta" ] || continue
    local nm; nm="$(sed -n 's/^name=//p' "$sd/meta")"
    if [ "$target" = all ] || [ "$nm" = "$target" ]; then
      found=1; touch "$sd/stop"
      [ -f "$sd/loop.pid" ] && kill "$(cat "$sd/loop.pid")" 2>/dev/null || true
      rm -rf "$sd"; ok "stopped waking '$nm'"
    fi
  done
  [ "$found" -eq 0 ] && warn "no wake loop matched '$target'"
  return 0
}

wake_ls() {
  local sd any=0
  for sd in "$COMM_STATE"/wakes/*/; do
    [ -f "$sd/meta" ] || continue; any=1
    local nm ev; nm="$(sed -n 's/^name=//p' "$sd/meta")"; ev="$(sed -n 's/^every=//p' "$sd/meta")"
    local up=down; [ -f "$sd/loop.pid" ] && kill -0 "$(cat "$sd/loop.pid")" 2>/dev/null && up=up
    printf '  wake %-24s every=%-6s ticks=%-4s [%s]\n' "$nm" "$ev" "$(cat "$sd/count" 2>/dev/null || echo 0)" "$up" >&2
  done
  [ "$any" -eq 0 ] && warn "no active wake loops"
  return 0
}

wake_cmd() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    stop) wake_stop "$@";;
    ls|list) wake_ls;;
    ""|-h|--help) die "usage: communicate wake <name> [--every SEC] [--message MSG] [--times N] | wake stop <name|all> | wake ls";;
    *) wake_start "$sub" "$@";;
  esac
}
