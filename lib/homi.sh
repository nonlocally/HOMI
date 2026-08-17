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
    stop|status|agents|wait|claim|release|describe|send|ask|reply|group|notify|seat|spawn|restart|fan|consult|link|unlink|grant|ungrant|grants|card|user|init|pair|connect|federate|statepath|premove|depart|arrive|move) python3 "$HOMI_PY" call "$sub" "$@";;
    inbox)     homi_inbox "$@";;
    install)   homi_install "$@";;
    uninstall) homi_uninstall "$@";;
    adopt)     homi_adopt "$@";;
    retitle)   python3 "$HOMI_PY" retitle "$@";;
    *) die "usage: communicate homi {start|stop|status|agents|init|user|claim [--boxed]|release|describe|send|ask|reply|wait|group|notify|inbox|seat|spawn|restart|fan|consult|move|link|unlink|grant|ungrant|grants|card|pair|connect|federate|install|uninstall|adopt|retitle} ...";;
  esac
}

# Rename-sync, write side. A LIVE session's name can only change through its
# composer (/rename is a local command, not message content — a socket-
# delivered "/rename" is just text to the model), so live adoption rides
# anu's `pane send`. Dormant transcripts take the appended custom-title
# record (`pm retitle`, beam's proven method).
homi_adopt() {
  local name="" pane=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pane) pane="${2:-}"; shift 2 || break;;
      *) [ -z "$name" ] && name="$1"; shift;;
    esac
  done
  [ -n "$name" ] || die "usage: communicate homi adopt <name> --pane <paneid>"
  [ -n "$pane" ] || die "adopt needs --pane <paneid> (live rename rides pane send); for a dormant session use: communicate homi retitle <uuid> $name"
  command -v pane >/dev/null 2>&1 || die "the 'pane' bin is not on PATH (anu is required for live adopt)"
  pane send "$pane" "/rename $name" || die "pane send failed"
  local i sess; sess="$(comm_sessions_dir)"
  for i in $(seq 1 12); do
    if grep -l "\"name\":\"$name\"" "$sess"/*.json >/dev/null 2>&1; then
      ok "session in $pane adopted the name '$name' (sidecar confirms)"
      return 0
    fi
    sleep 0.5
  done
  warn "sent /rename but no sidecar shows '$name' yet — check the pane"
  return 1
}

# ---- persistence: launchd (macOS) / systemd --user (linux) -------------------
#
# The homi is infrastructure: RunAtLoad + KeepAlive mean it survives
# reboots, sleeps, and kill -9. `pm stop` on a managed daemon just gets it
# respawned — that is the point; use `pm uninstall` to actually remove it.
# The device name and state paths are resolved AT INSTALL TIME and baked into
# the unit, so a launchd run (minimal PATH, no tailscale) keeps the same
# identity as an interactive one.

homi_install() {
  comm_need_python
  local label="${HOMI_LABEL:-com.communicate.homi}"
  local state; state="$(homi_state_dir)"
  mkdir -p "$state"
  local selfdev="${HOMI_SELF:-$(python3 "$HOMI_PY" selfname)}"
  [ -n "$selfdev" ] || die "could not resolve a device name"
  case "$(uname -s)" in
    Darwin) _homi_install_launchd "$label" "$state" "$selfdev";;
    Linux)  _homi_install_systemd "$state" "$selfdev";;
    *) die "unsupported platform: $(uname -s)";;
  esac
}

_homi_install_launchd() {
  local label="$1" state="$2" selfdev="$3"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  # A foreground daemon would fight the managed one for the singleton lock.
  python3 "$HOMI_PY" call stop >/dev/null 2>&1 || true
  sleep 0.5
  mkdir -p "$HOME/Library/LaunchAgents"
  local extra=""
  [ -n "${HOMI_SOCK_DIR:-}" ] && extra="$extra
    <key>HOMI_SOCK_DIR</key><string>$HOMI_SOCK_DIR</string>"
  [ -n "${HOMI_SESSIONS_DIR:-}" ] && extra="$extra
    <key>HOMI_SESSIONS_DIR</key><string>$HOMI_SESSIONS_DIR</string>"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$COMM_HOME/bin/communicate</string>
    <string>homi</string>
    <string>__daemon</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>COMM_STATE</key><string>${COMM_STATE:-$HOME/.local/state/communicate}</string>
    <key>HOMI_SELF</key><string>$selfdev</string>$extra
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$state/launchd.log</string>
  <key>StandardErrorPath</key><string>$state/launchd.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchctl bootstrap failed"
  local i
  for i in $(seq 1 50); do
    python3 "$HOMI_PY" call status >/dev/null 2>&1 && { ok "homi installed ($label, device=$selfdev)"; return 0; }
    sleep 0.2
  done
  die "installed but not answering — see $state/launchd.log"
}

_homi_install_systemd() {
  local state="$1" selfdev="$2"
  local unitdir="$HOME/.config/systemd/user"
  mkdir -p "$unitdir"
  cat > "$unitdir/communicate-homi.service" <<EOF
[Unit]
Description=communicate homi (agent fabric)

[Service]
ExecStart=$COMM_HOME/bin/communicate homi __daemon
Environment=COMM_STATE=${COMM_STATE:-%h/.local/state/communicate}
Environment=HOMI_SELF=$selfdev
${HOMI_SOCK_DIR:+Environment=HOMI_SOCK_DIR=$HOMI_SOCK_DIR}
${HOMI_SESSIONS_DIR:+Environment=HOMI_SESSIONS_DIR=$HOMI_SESSIONS_DIR}
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
  python3 "$HOMI_PY" call stop >/dev/null 2>&1 || true
  systemctl --user daemon-reload || die "systemctl daemon-reload failed"
  systemctl --user enable --now communicate-homi.service \
    || die "systemctl enable --now failed"
  local i
  for i in $(seq 1 50); do
    python3 "$HOMI_PY" call status >/dev/null 2>&1 && { ok "homi installed (systemd --user, device=$selfdev)"; return 0; }
    sleep 0.2
  done
  die "installed but not answering — check: journalctl --user -u communicate-homi"
}

homi_uninstall() {
  local label="${HOMI_LABEL:-com.communicate.homi}"
  case "$(uname -s)" in
    Darwin)
      launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
      rm -f "$HOME/Library/LaunchAgents/$label.plist"
      ok "homi uninstalled ($label)";;
    Linux)
      systemctl --user disable --now communicate-homi.service >/dev/null 2>&1 || true
      rm -f "$HOME/.config/systemd/user/communicate-homi.service"
      systemctl --user daemon-reload >/dev/null 2>&1 || true
      ok "homi uninstalled (systemd --user)";;
    *) die "unsupported platform: $(uname -s)";;
  esac
}

# Files-as-API: the inbox is a plain JSONL file; reading it needs no daemon.
homi_inbox() {
  local name="${1:-}"; [ -n "$name" ] || die "usage: communicate homi inbox <name>"
  local f; f="$(homi_state_dir)/mail/$name/inbox.jsonl"
  [ -f "$f" ] || die "no mailbox for '$name' ($f)"
  cat "$f"
}
