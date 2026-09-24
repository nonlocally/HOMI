---
description: Show the agent routing table — every reachable agent by name (local Claude sessions, bridged remotes, Codex peers) with status and socket.
allowed-tools: Bash
---
For registered bus agents, first inspect `communicate bus status --no-start --json`
and follow `communicate-bus` using the configured or explicitly requested hub.
An explicit `https://bus.nonlocally.org` request needs enrollment there; never
substitute a local roster. Use `communicate bus agents --json` on that hub.

For native sessions on this machine or existing SSH bridges:
Run `communicate agents` (if not on PATH, fall back to
`~/.local/share/communicate/current/vendor/bin/communicate agents`) and render
the table for the user. If the user also asked you to contact a listed agent,
carry out that request through its exact supported route and report the result.
Use `communicate route <name> "<msg>"`, or — inside Claude Code for a Claude
peer — the native SendMessage tool; the user need not run it manually.
