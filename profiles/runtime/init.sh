# HOMI optional profile. No daemons, network calls, prompts, or package installs.
# HOMI_PROFILE_RUNTIME and HOMI_PROFILE_MODULES come from the managed active.sh.
[ -n "${BASH_VERSION:-}" ] || return 0
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  printf 'HOMI workstation needs Bash 4+; use a newer bash for this profile.\n' >&2
  return 0
fi
export HOMI_PROFILE_CONFIG="${HOMI_PROFILE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/homi/profiles}"
export HOMI_PROFILE_STATE="${HOMI_PROFILE_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/homi/workstation}"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
# Account behavior is selected explicitly; native authentication is still the
# default without this module. A watcher requires HOMI_PROFILE_WATCH=1 as well.
case " ${HOMI_PROFILE_MODULES:-} " in
  *" accounts "*)
    export HOMI_ACCOUNT_LAUNCHER="${HOMI_ACCOUNT_LAUNCHER:-$HOME/.local/bin/homi-account}"
    export HOMI_PANE_WATCHER="${HOMI_PANE_WATCHER:-$HOME/.local/bin/homi-account-pane}"
    export ANU_ACCOUNT_BIN="${ANU_ACCOUNT_BIN:-$HOME/.local/bin/homi-account}"
    export ANU_PANE_BIN="${ANU_PANE_BIN:-$HOME/.local/bin/homi-account-pane}"
    ;;
esac
case " ${HOMI_PROFILE_MODULES:-} " in
  *" box "*) . "$HOMI_PROFILE_RUNTIME/fns/box" ;;
esac
# Private configuration, never replaced by profile installation or upgrade.
[ ! -f "$HOMI_PROFILE_CONFIG/local.sh" ] || . "$HOMI_PROFILE_CONFIG/local.sh"
case " ${HOMI_PROFILE_MODULES:-} " in
  *" terminal "*)
    for _homi_module in tmux agentlaunch dynlayout; do
      . "$HOMI_PROFILE_RUNTIME/fns/$_homi_module"
    done
    unset _homi_module
    cx() { homi-agent cx "$@"; }
    cxx() { homi-agent cxx "$@"; }
    cxc() { homi-agent cxc "$@"; }
    cdx() { homi-agent cdx "$@"; }
    cdxx() { homi-agent cdxx "$@"; }
    cdxxs() { homi-agent cdxxs "$@"; }
    tile() { if [ "$#" -eq 0 ]; then homi-workstation tile status; else homi-workstation tile "$@"; fi; }
    ;;
esac
case " ${HOMI_PROFILE_MODULES:-} " in
  *" mesh "*) . "$HOMI_PROFILE_RUNTIME/fns/mesh" ;;
esac
