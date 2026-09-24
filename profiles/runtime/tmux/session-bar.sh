#!/usr/bin/env bash
# Adapted from Anu 80d3c86; see profiles/NOTICE.md.
# Generates the tmux session bar for status-format[0].
#
# status-format is global, so the active session must remain a tmux format
# expression. If this script resolves the active session eagerly, whichever
# client last ran a session hook paints every client's session row.

_fmt_literal() {
  local value="$1"
  local escaped_hash="##"
  local escaped_comma="#,"
  local escaped_brace="#}"
  value=${value//\#/$escaped_hash}
  value=${value//,/$escaped_comma}
  value=${value//\}/$escaped_brace}
  printf '%s' "$value"
}

format=""
i=0

while IFS= read -r session; do
  letter=$(printf "\\$(printf '%03o' $((65 + i)))")
  label="$(_fmt_literal "${letter}:${session}")"
  compare="$(_fmt_literal "$session")"
  active="#[fg=#121212#,bg=#e68e0d#,bold] ${label} #[bg=default] "
  inactive="#[fg=#5f9ea0] ${label}  "
  format+="#{?#{||:#{==:#{session_name},${compare}},#{==:#{session_group},${compare}}},${active},${inactive}}"
  i=$((i + 1))
done < <(tmux list-sessions -F '#S' 2>/dev/null | grep -v '^_stash$' | grep -v '~')

tmux set -g status-format[0] "$format"
