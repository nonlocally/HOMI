# shellcheck shell=bash
# communicate pm — the postmaster: durable identities, mailboxes, links.
# Thin verb layer over lib/postmaster.py (daemon + control-socket client).

PM_PY="$COMM_HOME/lib/postmaster.py"

pm_state_dir() { printf '%s/pm' "$COMM_STATE"; }

pm_start() {
  comm_need_python
  if python3 "$PM_PY" call status >/dev/null 2>&1; then
    die "postmaster already running (communicate pm status)"
  fi
  mkdir -p "$(pm_state_dir)"
  nohup python3 "$PM_PY" daemon >> "$(pm_state_dir)/daemon.log" 2>&1 &
  disown 2>/dev/null || true
  local i
  for i in $(seq 1 50); do
    python3 "$PM_PY" call status >/dev/null 2>&1 && { ok "postmaster up"; return 0; }
    sleep 0.1
  done
  die "postmaster failed to start — see $(pm_state_dir)/daemon.log"
}

pm_cmd() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    start)    pm_start "$@";;
    __daemon) exec python3 "$PM_PY" daemon;;
    stop|status|claim|release|send|link|unlink) python3 "$PM_PY" call "$sub" "$@";;
    inbox)    pm_inbox "$@";;
    *) die "usage: communicate pm {start|stop|status [--json]|claim|release|send|inbox|link|unlink} ...";;
  esac
}

# Files-as-API: the inbox is a plain JSONL file; reading it needs no daemon.
pm_inbox() {
  local name="${1:-}"; [ -n "$name" ] || die "usage: communicate pm inbox <name>"
  local f; f="$(pm_state_dir)/mail/$name/inbox.jsonl"
  [ -f "$f" ] || die "no mailbox for '$name' ($f)"
  cat "$f"
}
