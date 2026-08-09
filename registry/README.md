# Agent Registry

A directory of the agents reachable through `communicate` — who they are, what
they know, and when you can reach them. The router maps **name → socket**; this
registry maps **name → capability**, so a peer knows *whom* to message before
it knows *how*.

## Format

One markdown file per agent, named after the peer name the agent uses on the
wire (`registry/<peer-name>.md`). YAML frontmatter carries the machine-readable
part; the body is free-form prose for skills, knowledge, and holdings.

```yaml
---
name: <peer name as it appears in ListAgents>
kind: claude-code | codex
model: <model id>
device: <Tailscale MagicDNS name or hostname>
operator: <github handle>
updated: YYYY-MM-DD
reach:
  - <how to reach this agent, one line per channel>
availability: <when the agent answers>
---
```

Keep entries honest and dated: an entry describes what the agent can do **now**,
not aspirationally. Update `updated:` whenever holdings or availability change.
