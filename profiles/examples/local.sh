# Copy selected settings into ~/.config/homi/profiles/local.sh.
# This file is never installed over user configuration. Shell code here is trusted.
# Native provider authentication is the default. An optional account adapter is a
# single executable that accepts: launch --provider claude|codex [--box] -- ARGS.
# export HOMI_ACCOUNT_LAUNCHER="$HOME/.local/libexec/private-account"
# export HOMI_BOX_LAUNCHER="$HOME/.local/bin/box"
# export HOMI_PANE_WATCHER="$HOME/.local/libexec/private-pane"
# export HOMI_PROFILE_WATCH=1
# Keep existing snapshot data in place during a separately reviewed migration:
# export HOMI_PROFILE_STATE="$HOME/.local/state/homi/workstation"
# User-owned tmux and Ghostty overrides may be placed at:
# ~/.config/homi/profiles/local.tmux.conf
# ~/.config/homi/profiles/local.ghostty.conf
