---
description: Show the agent routing table — every reachable agent by name (local Claude sessions, bridged remotes, Codex peers) with status and socket.
allowed-tools: Bash
---
Run `communicate agents` (if not on PATH, fall back to
`~/.local/share/communicate/current/vendor/bin/communicate agents`) and render
the table for the user. Then add one line of guidance: any row can be messaged
with `communicate route <name> "<msg>"`, or — from inside Claude Code — by
sending to the name with the native SendMessage tool.
