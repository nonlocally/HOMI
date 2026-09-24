# shellcheck shell=bash
# communicate homi — the homi: durable identities, mailboxes, links.
# Thin verb layer over lib/homi.py (daemon + control-socket client).

HOMI_PY="$COMM_HOME/lib/homi.py"

homi_state_dir() { printf '%s/homi' "$COMM_STATE"; }

homi_start() {
  comm_need_python
  if python3 "$HOMI_PY" call status >/dev/null 2>&1; then
    die "homi already running (communicate homi status)"
  fi
  mkdir -p "$(homi_state_dir)"
  nohup python3 "$HOMI_PY" daemon >> "$(homi_state_dir)/daemon.log" 2>&1 &
  disown 2>/dev/null || true
  local i
  for i in $(seq 1 50); do
    python3 "$HOMI_PY" call status >/dev/null 2>&1 && { ok "homi up"; return 0; }
    sleep 0.1
  done
  die "homi failed to start — see $(homi_state_dir)/daemon.log"
}

homi_cmd() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    start)     homi_start "$@";;
    __daemon)  exec python3 "$HOMI_PY" daemon;;
    stop|status|agents|wait|claim|release|describe|send|ask|reply|group|notify|seat|spawn|restart|fan|consult|model|link|unlink|grant|ungrant|grants|card|user|init|pair|connect|board|federate|statepath|premove|depart|arrive|move) python3 "$HOMI_PY" call "$sub" "$@";;
    inbox)     homi_inbox "$@";;
    install)   homi_install "$@";;
    uninstall) homi_uninstall "$@";;
    adopt)     homi_adopt "$@";;
    retitle)   python3 "$HOMI_PY" retitle "$@";;
    *) die "usage: communicate homi {start|stop|status|agents|init|user|claim [--boxed]|release|describe|send|ask|reply|wait|group|notify|inbox|seat|spawn|restart|fan|consult|move|link|unlink|grant|ungrant|grants|card|pair|connect|board|federate|install|uninstall|adopt|retitle} ...";;
  esac
}

# Live pane adoption and remote-device adoption share the kernel CLI.
# /rename uses the existing seat driver and verifies the selected session.
homi_adopt() {
  python3 "$HOMI_PY" call adopt "$@"
}

# Persistence belongs to the package installer's ownership ledger. Keep the
# historical verbs as explicit refusals: their old fixed-label implementation
# could replace another installation's service, even with an isolated HOME.
# In particular, daemon-only uninstall must never silently remove client
# integrations through the combined `homi uninstall` command.
homi_install() {
  printf '%s\n' "communicate: legacy daemon install is disabled; use 'homi setup --no-clients --service' for ownership-aware persistence (preview with --dry-run)" >&2
  return 1
}

homi_uninstall() {
  printf '%s\n' "communicate: legacy daemon uninstall is disabled; inspect 'homi doctor', then preview 'homi uninstall --dry-run'. Managed uninstall also detaches owned client integrations; it preserves identity state. Existing unowned legacy units require explicit migration." >&2
  return 1
}

# Files-as-API: the inbox is a plain JSONL file; reading it needs no daemon.
homi_inbox() {
  local name="${1:-}"; [ -n "$name" ] || die "usage: communicate homi inbox <name>"
  local f; f="$(homi_state_dir)/mail/$name/inbox.jsonl"
  [ -f "$f" ] || die "no mailbox for '$name' ($f)"
  cat "$f"
}
