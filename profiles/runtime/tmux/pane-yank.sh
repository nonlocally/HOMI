#!/usr/bin/env bash
# Adapted from Anu 80d3c86; see profiles/NOTICE.md.
# pane-yank.sh — copy a tmux pane's id to the clipboard, to hand to an agent so
# it can message that pane (e.g. `tmux send-keys -t <id> ...` or `swarm send`).
# The inverse of "go figure out which pane that other agent is living in".
#
#   pane-yank.sh current <pane_id>   copy the focused pane's id
#   pane-yank.sh pick                fzf-pick any pane across all sessions
#
# We copy the pane_id (e.g. %5) rather than session:window.pane because
# `renumber-windows on` makes index-based targets shift when windows close;
# the pane_id is stable for the life of the pane and is a valid `-t` target.
set -uo pipefail

_clip() {
  if   command -v pbcopy  >/dev/null 2>&1; then pbcopy
  elif command -v wl-copy >/dev/null 2>&1; then wl-copy
  elif command -v xclip   >/dev/null 2>&1; then xclip -selection clipboard
  else cat >/dev/null
  fi
}

_yank() { # $1 = pane id (e.g. %5)
  local id="$1" loc cmd
  loc=$(tmux display-message -p -t "$id" '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null)
  cmd=$(tmux display-message -p -t "$id" '#{pane_current_command}' 2>/dev/null)
  printf '%s' "$id" | _clip
  tmux display-message "yanked $id  ($loc · $cmd) -> clipboard"
}

case "${1:-current}" in
  current)
    [ -n "${2:-}" ] || { tmux display-message "pane-yank: no pane id"; exit 1; }
    _yank "$2"
    ;;
  pick)
    sel=$(tmux list-panes -a \
      -F '#{pane_id}  #{session_name}:#{window_index}.#{pane_index}  #{pane_current_command}  #{pane_title}' \
      | fzf --reverse --header 'yank pane id -> clipboard (Enter to copy)') || exit 0
    [ -n "${sel:-}" ] && _yank "${sel%% *}"
    ;;
  *)
    echo "usage: pane-yank.sh current <pane_id> | pick" >&2
    exit 1
    ;;
esac
