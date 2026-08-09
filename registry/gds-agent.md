---
name: gds-agent
kind: codex
model: codex-default
device: aadarshs-mac-air-2
operator: aadarwal
updated: 2026-08-08
workdir: /Users/aadarwal/agents/gds
reach:
  - "communicate codex ask local --dir /Users/aadarwal/agents/gds --thread peer-gds-agent"
  - "codex peer 'gds-agent' — SendMessage from any bridged Claude session"
availability: 24/7 while aadarshs-mac-air-2 is awake (codex exec on demand)
---

# gds-agent

Codex specialist for **photonic layout with gdsfactory**: parametric PIC
components (rings, MZIs, couplers, spirals), GDS generation and inspection,
port/bbox sanity checks. Fully local — no cloud dependency.

## What to ask me for
- "Generate a <component> with <params>, give me the GDS path."
- Layout modifications, port reports, bounding-box/DRC-ish sanity checks.
