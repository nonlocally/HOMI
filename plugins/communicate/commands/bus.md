---
description: Show registered buses and agents, or open the bus dashboard.
allowed-tools: Bash
---
Run `communicate bus list --json` and `communicate bus agents --json`, then
summarize registered memberships and actual availability. Queueable Codex
threads are not necessarily live. If asked to open the interface, run
`communicate bus dashboard --open`. See the communicate-bus skill for
registration, selected buses, and secure invitations. Keep authenticated
dashboard URLs and invite codes private.

Perform the requested discovery or opening yourself; return its result rather
than asking the user to run these commands.
