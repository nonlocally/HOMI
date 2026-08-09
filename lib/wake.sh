# shellcheck shell=bash
# communicate :: wake — trigger an agent. A message to an agent is already a
# wake (an inbound peer message resumes an idle/finished session), so wake is not
# really about a clock — it is about WHAT triggers the message. Two triggers:
#
#   timer   fire every N seconds (heartbeat / keep-working / poll)
#   on-pr   fire when a repo gets a NEW pull request (the event you actually want)
#
# Both end the same way: route a message to an agent by name. New triggers slot
# in by adding a mode + a poller.

_wake_state_dir() { printf '%s/wakes/%s\n' "$COMM_STATE" "$(printf '%s' "$1" | tr '/@: ' '____')"; }

# Interruptible sleep: return 1 as soon as the stop-file appears. Avoids bash
# deferring a trapped signal until a long `sleep` returns, so stop is prompt.
_nap() {
  local sd="$1" secs="$2" e=0
  while [ "$e" -lt "$secs" ]; do
    [ -f "$sd/stop" ] && return 1
    sleep 1; e=$((e+1))
  done
  return 0
}

# --- pull-request poller (uses gh; still pure local/ssh, no extra transport) ---
_gh_prs() {  # -> "number\ttitle\tauthor\turl" per open PR
  gh pr list --repo "$1" --state open --json number,title,author,url --limit 50 2>/dev/null | python3 -c '
import sys, json
try: data = json.load(sys.stdin)
except Exception: sys.exit(1)
for p in data:
    a = (p.get("author") or {}).get("login", "?")
    print("%s\t%s\t%s\t%s" % (p["number"], (p.get("title") or "").replace("\t", " "), a, p.get("url", "")))
'
}
_gh_pr_numbers() { _gh_prs "$1" | cut -f1; }

# wake_start <name> [--every SEC] [--message MSG] [--times N] [--on-pr REPO] [--catchup]
wake_start() {
  local name="$1"; shift || true
  [ -n "$name" ] || die "usage: communicate wake <name> [--every SEC] [--message MSG] [--times N] [--on-pr REPO]"
  local every="" times=0 message="" repo="" catchup=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --every) every="$2"; shift 2;;
      --message|--msg) message="$2"; shift 2;;
      --times) times="$2"; shift 2;;
      --on-pr) repo="$2"; shift 2;;
      --catchup) catchup=1; shift;;
      *) die "unknown option $1";;
    esac
  done
  router_whereis "$name" >/dev/null 2>&1 || die "no agent named '$name' to wake (see: communicate agents)"

  local mode="timer"
  if [ -n "$repo" ]; then
    mode="pr"
    command -v gh >/dev/null 2>&1 || die "--on-pr needs the 'gh' CLI (GitHub) installed and authenticated"
    gh pr list --repo "$repo" --state open --limit 1 >/dev/null 2>&1 || die "cannot read PRs for '$repo' (gh auth / repo access?)"
    [ -z "$every" ] && every=60
  else
    [ -z "$every" ] && every=300
    [ -z "$message" ] && message="(wake) automated heartbeat from communicate — continue your task or reply with status."
  fi

  # Replace any existing wake loop for this name (avoid orphaned loops).
  local sd; sd="$(_wake_state_dir "$name")"
  if [ -f "$sd/loop.pid" ] && kill -0 "$(cat "$sd/loop.pid")" 2>/dev/null; then
    warn "existing wake for '$name' — replacing it"; wake_stop "$name" >/dev/null 2>&1 || true
  fi
  mkdir -p "$sd"; rm -f "$sd/stop"; echo 0 > "$sd/count"
  {
    printf 'name=%s\n' "$name"; printf 'mode=%s\n' "$mode"; printf 'every=%s\n' "$every"
    printf 'times=%s\n' "$times"; printf 'message=%s\n' "$message"
    printf 'repo=%s\n' "$repo"; printf 'catchup=%s\n' "$catchup"
  } > "$sd/meta"

  nohup "$COMM_HOME/bin/communicate" __wake-loop "$name" >"$sd/wake.log" 2>&1 &
  local lp=$!; echo "$lp" > "$sd/loop.pid"; disown "$lp" 2>/dev/null || true
  if [ "$mode" = pr ]; then
    ok "watching $repo for new PRs (every ${every}s) -> waking '$name'"
  else
    ok "waking '$name' every ${every}s$([ "$times" -gt 0 ] && echo ", $times time(s)")"
  fi
  log "  stop with: communicate wake stop $name"
}

_wake_loop() {
  local name="$1"; local sd; sd="$(_wake_state_dir "$name")"
  [ -f "$sd/meta" ] || return 1
  local mode every times message repo catchup
  while IFS='=' read -r k v; do case "$k" in
    mode) mode="$v";; every) every="$v";; times) times="$v";; message) message="$v";;
    repo) repo="$v";; catchup) catchup="$v";; esac; done < "$sd/meta"
  trap 'exit 0' TERM INT
  local n=0 fails=0; local MAXFAIL=10

  if [ "$mode" = pr ]; then
    # Seed the seen-set so we only fire on PRs opened AFTER we start (unless --catchup).
    if [ ! -f "$sd/seen" ]; then
      if [ "$catchup" = 1 ]; then : > "$sd/seen"; else _gh_pr_numbers "$repo" > "$sd/seen" 2>/dev/null || : > "$sd/seen"; fi
    fi
    while :; do
      _nap "$sd" "$every" || break
      local prs
      if ! prs="$(_gh_prs "$repo" 2>/dev/null)"; then
        fails=$((fails+1)); [ "$fails" -ge "$MAXFAIL" ] && { warn "wake '$name': gh failing repeatedly, exiting"; break; }; continue
      fi
      fails=0
      local num title author url
      while IFS=$'\t' read -r num title author url; do
        [ -n "$num" ] || continue
        grep -qx "$num" "$sd/seen" 2>/dev/null && continue
        local m="New PR #$num in $repo: $title — by $author"$'\n'"$url"
        [ -n "$message" ] && m="$message"$'\n'"$m"
        if router_route "$name" -- "$m" >/dev/null 2>&1; then
          echo "$num" >> "$sd/seen"; n=$((n+1)); echo "$n" > "$sd/count"
        else
          warn "wake '$name': could not deliver PR #$num (will retry next poll)"
        fi
      done <<< "$prs"
    done
  else
    while :; do
      _nap "$sd" "$every" || break
      if router_route "$name" -- "$message" >/dev/null 2>&1; then
        n=$((n+1)); echo "$n" > "$sd/count"; fails=0
      else
        fails=$((fails+1)); warn "wake '$name': target unreachable ($fails/$MAXFAIL)"
        [ "$fails" -ge "$MAXFAIL" ] && { warn "wake '$name': target gone, exiting"; break; }
      fi
      [ "$times" -gt 0 ] && [ "$n" -ge "$times" ] && break
    done
  fi
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
      [ -f "$sd/loop.pid" ] && comm_kill_hard "$(cat "$sd/loop.pid")"
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
    local nm md ev rp; nm="$(sed -n 's/^name=//p' "$sd/meta")"; md="$(sed -n 's/^mode=//p' "$sd/meta")"
    ev="$(sed -n 's/^every=//p' "$sd/meta")"; rp="$(sed -n 's/^repo=//p' "$sd/meta")"
    local up=down; [ -f "$sd/loop.pid" ] && kill -0 "$(cat "$sd/loop.pid")" 2>/dev/null && up=up
    printf '  wake %-22s %-6s %-22s every=%-5s fired=%-4s [%s]\n' \
      "$nm" "$md" "${rp:-–}" "$ev" "$(cat "$sd/count" 2>/dev/null || echo 0)" "$up" >&2
  done
  [ "$any" -eq 0 ] && warn "no active wake loops"
  return 0
}

wake_cmd() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    stop) wake_stop "$@";;
    ls|list) wake_ls;;
    ""|-h|--help) die "usage: communicate wake <name> [--every SEC] [--message MSG] [--times N] [--on-pr REPO] [--catchup] | wake stop <name|all> | wake ls";;
    *) wake_start "$sub" "$@";;
  esac
}
