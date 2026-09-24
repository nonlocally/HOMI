---
description: Show the agent routing table — every reachable agent by name (local Claude sessions, bridged remotes, Codex peers) with status and socket.
allowed-tools: Bash
---
Run `communicate agents` (if not on PATH, fall back to
`~/.local/share/communicate/current/vendor/bin/communicate agents`) and render
the table for the user. If the user also asked you to contact a listed agent,
carry out that request through its exact supported route and report the result.
Use `communicate route <name> "<msg>"`, or — inside Claude Code for a Claude
peer — the native SendMessage tool; the user need not run it manually.
