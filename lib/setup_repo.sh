# shellcheck shell=bash
# communicate :: setup-repo — register THIS checkout as the plugin for
# Claude Code and Codex. The repo-haver's install: no npm, no vendor, no
# frozen copy; `git pull` is the upgrade. The npm package's `setup` remains
# the no-repo/npx path (frozen payload + MCP deps).

_sr_settings() { printf '%s/.claude/settings.json' "$HOME"; }

# Merge (or remove) the two Claude keys. python3 owns the JSON: backup first,
# merge-not-clobber, refuse an unparsable file.
_sr_claude() {
  local mode="$1" dry="$2" market_root="$COMM_HOME/plugins"
  python3 - "$mode" "$dry" "$market_root" "$(_sr_settings)" <<'PY'
import json, os, shutil, sys, time
mode, dry, market_root, sp = sys.argv[1:5]
dry = dry == "1"
s = {}
if os.path.exists(sp):
    try:
        s = json.load(open(sp))
    except Exception:
        sys.stderr.write(f"refusing to touch unparsable {sp} - fix it first\n"); sys.exit(1)
prev = (s.get("extraKnownMarketplaces") or {}).get("communicate", {}).get("source", {}).get("path")
if mode == "install":
    s.setdefault("extraKnownMarketplaces", {})["communicate"] = {
        "source": {"source": "directory", "path": market_root}}
    s.setdefault("enabledPlugins", {})["communicate@communicate"] = True
    why = f"point marketplace 'communicate' at {market_root} + enable communicate@communicate"
else:
    (s.get("extraKnownMarketplaces") or {}).pop("communicate", None)
    (s.get("enabledPlugins") or {}).pop("communicate@communicate", None)
    why = "remove marketplace 'communicate' + communicate@communicate"
if dry:
    print(f"[dry-run] would {why} in {sp}"); sys.exit(0)
os.makedirs(os.path.dirname(sp), exist_ok=True)
if os.path.exists(sp):
    shutil.copy2(sp, f"{sp}.communicate-backup-{int(time.time()*1000)}")
json.dump(s, open(sp, "w"), indent=2); open(sp, "a").write("\n")
print(f"{why} in {sp} (backup written)")
if prev and mode == "install" and prev != market_root:
    print(f"note: marketplace previously pointed at {prev} - switched to this checkout")
PY
}

_sr_codex() {
  local mode="$1" dry="$2"
  local -a cmds
  if [ "$mode" = install ]; then
    cmds=("plugin marketplace add $COMM_HOME" "plugin add communicate@communicate")
  else
    cmds=("plugin remove communicate@communicate" "plugin marketplace remove communicate")
  fi
  if ! command -v codex >/dev/null 2>&1; then
    log "codex CLI not found — run these once it is installed:"
    local c; for c in "${cmds[@]}"; do printf '  codex %s\n' "$c" >&2; done
    return 0
  fi
  local c
  for c in "${cmds[@]}"; do
    if [ "$dry" = 1 ]; then log "[dry-run] would run: codex $c"; continue; fi
    # shellcheck disable=SC2086
    if codex $c >/dev/null 2>&1; then log "codex $c — ok"
    else log "codex $c — failed (may already be ${mode}ed); run manually if needed: codex $c"; fi
  done
  [ "$mode" = install ] && [ "$dry" != 1 ] && log "Codex: start a NEW thread to see the plugin."
  return 0
}

setup_repo() {
  local claude=0 codex=0 dry=0 uninstall=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --claude) claude=1;;
      --codex) codex=1;;
      --dry-run) dry=1;;
      --uninstall) uninstall=1;;
      *) die "usage: communicate setup-repo [--claude] [--codex] [--dry-run] [--uninstall]";;
    esac; shift
  done
  [ "$claude" = 0 ] && [ "$codex" = 0 ] && claude=1 codex=1
  comm_need_python
  [ -f "$COMM_HOME/plugins/.claude-plugin/marketplace.json" ] || \
    die "no plugin payload at $COMM_HOME/plugins — is this a communicate checkout?"
  local mode=install; [ "$uninstall" = 1 ] && mode=uninstall
  [ "$claude" = 1 ] && { _sr_claude "$mode" "$dry" || die "claude registration failed"; }
  [ "$codex" = 1 ] && _sr_codex "$mode" "$dry"
  if [ "$mode" = install ] && [ "$dry" != 1 ]; then
    ok "this checkout is registered — new Claude sessions load skills + /agents + bin; git pull = upgrade"
    log "MCP face note: the plugin's .mcp.json launches 'npx -y @aadarwal/communicate serve',"
    log "which activates once the package is published (until then, sessions may report that"
    log "server as unavailable — everything else works; the npm-mode setup wires MCP locally)."
  fi
}
