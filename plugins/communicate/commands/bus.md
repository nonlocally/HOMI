---
description: Show registered buses and agents, or open the bus dashboard.
allowed-tools: Bash
---
First run `communicate bus status --no-start --json`. Use its configured hub
unless the user selected another. For an explicit `https://bus.nonlocally.org`
request, inspect it with `communicate bus --hub https://bus.nonlocally.org status --no-start --json`;
if unconfigured, obtain a scoped invitation through the `communicate-bus` flow.
Never silently replace a shared hub with a local broker. With no configured
or specified hub, follow the user's local/shared intent or clarify it.
Keep the selected hub on the following commands with global `--hub URL`, or
select its existing connection with `communicate bus use URL`.

Run `communicate bus list --json` and `communicate bus agents --json`, then
summarize registered memberships and actual availability. Queueable Codex
threads are not necessarily live. If asked to open the interface, run
`communicate bus dashboard --open`. See the communicate-bus skill for
registration, selected buses, and secure invitations. Keep authenticated
dashboard URLs and invite codes private.

Perform the requested discovery or opening yourself; return its result rather
than asking the user to run these commands.
