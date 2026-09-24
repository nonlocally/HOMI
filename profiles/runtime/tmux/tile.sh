#!/usr/bin/env bash
# Adapted from Anu 80d3c86; see profiles/NOTICE.md.
# tile.sh — Tiling window manager for tmux
#
# Turns tmux into an auto-tiling WM. Create panes with one key,
# layouts auto-applied and preserved when panes close.
#
# Usage: tile.sh {new|cycle|retile|close|set <layout>|status}
#
# Layouts: tiled  main-v  main-h  cols  rows
# Per-window state stored in @tile_layout option.

LAYOUTS=(tiled main-v main-h cols rows)

_to_tmux() {
  case "$1" in
    main-v) echo "main-vertical" ;;
    main-h) echo "main-horizontal" ;;
    cols)   echo "even-horizontal" ;;
    rows)   echo "even-vertical" ;;
    *)      echo "tiled" ;;
  esac
}

_get() { tmux display-message -p '#{@tile_layout}' 2>/dev/null; }

_apply() { tmux select-layout "$(_to_tmux "${1:-$(_get)}")" 2>/dev/null; }

case "${1:-new}" in
  new)
    current=$(_get)
    current="${current:-tiled}"
    tmux set-window-option -q @tile_layout "$current"

    # Smart split direction: wide panes split vertical, tall split horizontal
    w=$(tmux display-message -p '#{pane_width}')
    h=$(tmux display-message -p '#{pane_height}')
    if (( w > h * 2 )); then
      tmux split-window -h -c "#{pane_current_path}"
    else
      tmux split-window -v -c "#{pane_current_path}"
    fi

    _apply "$current"
    ;;

  cycle)
    current=$(_get)
    current="${current:-tiled}"
    next="tiled"

    for i in "${!LAYOUTS[@]}"; do
      if [[ "${LAYOUTS[$i]}" == "$current" ]]; then
        next="${LAYOUTS[$(( (i + 1) % ${#LAYOUTS[@]} ))]}"
        break
      fi
    done

    tmux set-window-option -q @tile_layout "$next"
    _apply "$next"

    case "$next" in
      tiled)  icon="▦" ;;
      main-v) icon="◨" ;;
      main-h) icon="⬒" ;;
      cols)   icon="▥" ;;
      rows)   icon="▤" ;;
    esac
    tmux display-message "$icon $next"
    ;;

  retile)
    layout=$(_get)
    [[ -z "$layout" ]] && exit 0
    panes=$(tmux display-message -p '#{window_panes}' 2>/dev/null)
    (( panes > 1 )) && _apply "$layout"
    ;;

  close)
    tmux kill-pane 2>/dev/null
    layout=$(_get)
    [[ -z "$layout" ]] && exit 0
    panes=$(tmux display-message -p '#{window_panes}' 2>/dev/null)
    (( panes > 1 )) && _apply "$layout"
    ;;

  set)
    layout="${2:-tiled}"
    tmux set-window-option -q @tile_layout "$layout"
    _apply "$layout"
    tmux display-message "tile: $layout"
    ;;

  status)
    layout=$(_get)
    if [[ -n "$layout" ]]; then
      panes=$(tmux display-message -p '#{window_panes}')
      tmux display-message "tile: $layout ($panes panes)"
    else
      tmux display-message "tile: off"
    fi
    ;;
esac
