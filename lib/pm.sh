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
    start)     pm_start "$@";;
    __daemon)  exec python3 "$PM_PY" daemon;;
    stop|status|claim|release|send|link|unlink) python3 "$PM_PY" call "$sub" "$@";;
    inbox)     pm_inbox "$@";;
    install)   pm_install "$@";;
    uninstall) pm_uninstall "$@";;
    *) die "usage: communicate pm {start|stop|status [--json]|claim|release|send|inbox|link|unlink|install|uninstall} ...";;
  esac
}

# ---- persistence: launchd (macOS) / systemd --user (linux) -------------------
#
# The postmaster is infrastructure: RunAtLoad + KeepAlive mean it survives
# reboots, sleeps, and kill -9. `pm stop` on a managed daemon just gets it
# respawned — that is the point; use `pm uninstall` to actually remove it.
# The device name and state paths are resolved AT INSTALL TIME and baked into
# the unit, so a launchd run (minimal PATH, no tailscale) keeps the same
# identity as an interactive one.

pm_install() {
  comm_need_python
  local label="${PM_LABEL:-com.communicate.postmaster}"
  local state; state="$(pm_state_dir)"
  mkdir -p "$state"
  local selfdev="${PM_SELF:-$(python3 "$PM_PY" selfname)}"
  [ -n "$selfdev" ] || die "could not resolve a device name"
  case "$(uname -s)" in
    Darwin) _pm_install_launchd "$label" "$state" "$selfdev";;
    Linux)  _pm_install_systemd "$state" "$selfdev";;
    *) die "unsupported platform: $(uname -s)";;
  esac
}

_pm_install_launchd() {
  local label="$1" state="$2" selfdev="$3"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  # A foreground daemon would fight the managed one for the singleton lock.
  python3 "$PM_PY" call stop >/dev/null 2>&1 || true
  sleep 0.5
  mkdir -p "$HOME/Library/LaunchAgents"
  local extra=""
  [ -n "${PM_SOCK_DIR:-}" ] && extra="$extra
    <key>PM_SOCK_DIR</key><string>$PM_SOCK_DIR</string>"
  [ -n "${PM_SESSIONS_DIR:-}" ] && extra="$extra
    <key>PM_SESSIONS_DIR</key><string>$PM_SESSIONS_DIR</string>"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$COMM_HOME/bin/communicate</string>
    <string>pm</string>
    <string>__daemon</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>COMM_STATE</key><string>${COMM_STATE:-$HOME/.local/state/communicate}</string>
    <key>PM_SELF</key><string>$selfdev</string>$extra
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
    python3 "$PM_PY" call status >/dev/null 2>&1 && { ok "postmaster installed ($label, device=$selfdev)"; return 0; }
    sleep 0.2
  done
  die "installed but not answering — see $state/launchd.log"
}

_pm_install_systemd() {
  local state="$1" selfdev="$2"
  local unitdir="$HOME/.config/systemd/user"
  mkdir -p "$unitdir"
  cat > "$unitdir/communicate-postmaster.service" <<EOF
[Unit]
Description=communicate postmaster (agent fabric)

[Service]
ExecStart=$COMM_HOME/bin/communicate pm __daemon
Environment=COMM_STATE=${COMM_STATE:-%h/.local/state/communicate}
Environment=PM_SELF=$selfdev
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
  python3 "$PM_PY" call stop >/dev/null 2>&1 || true
  systemctl --user daemon-reload || die "systemctl daemon-reload failed"
  systemctl --user enable --now communicate-postmaster.service \
    || die "systemctl enable --now failed"
  ok "postmaster installed (systemd --user, device=$selfdev)"
}

pm_uninstall() {
  local label="${PM_LABEL:-com.communicate.postmaster}"
  case "$(uname -s)" in
    Darwin)
      launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
      rm -f "$HOME/Library/LaunchAgents/$label.plist"
      ok "postmaster uninstalled ($label)";;
    Linux)
      systemctl --user disable --now communicate-postmaster.service >/dev/null 2>&1 || true
      rm -f "$HOME/.config/systemd/user/communicate-postmaster.service"
      systemctl --user daemon-reload >/dev/null 2>&1 || true
      ok "postmaster uninstalled (systemd --user)";;
    *) die "unsupported platform: $(uname -s)";;
  esac
}

# Files-as-API: the inbox is a plain JSONL file; reading it needs no daemon.
pm_inbox() {
  local name="${1:-}"; [ -n "$name" ] || die "usage: communicate pm inbox <name>"
  local f; f="$(pm_state_dir)/mail/$name/inbox.jsonl"
  [ -f "$f" ] || die "no mailbox for '$name' ($f)"
  cat "$f"
}
