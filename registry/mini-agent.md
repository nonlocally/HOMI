---
name: mini-agent
kind: codex
model: codex-cli
device: aadarshs-mac-mini-2
operator: aadarwal
updated: 2026-08-09
workdir: /Users/aadarwal/agents/mini
reach:
  - "communicate codex ask aadarshs-mac-mini-2 --dir /Users/aadarwal/agents/mini --thread peer-mini-agent"
  - "codex peer 'mini-agent' — SendMessage from any bridged Claude session"
availability: 24/7 while aadarshs-mac-mini-2 is awake (codex exec over ssh)
---

# mini-agent

A general **Codex assistant on aadarshs-mac-mini-2** — a real model on the other
Mac, reachable from the orchestrator over Tailscale + SSH. Ask it to generate
or reason about anything; it answers directly. Confined to `~/agents/mini`.

This is the cross-device counterpart to the local codex specialists: same
`codex exec` dispatch, just on the mini. Use it to prove (and use) real
generative work on another device — e.g. "ask the mini agent for a random
sentence" returns an actual sentence, not an echo.

## What to ask me for
- Any general text/generation/reasoning task you want run on the mac mini.
- A quick cross-device liveness check that actually exercises a real model.
