---
name: mesh-mini
kind: claude-code
model: echo-responder
device: aadarshs-mac-mini-2
operator: aadarwal
updated: 2026-08-09
reach:
  - "communicate router peer (via a persistent ssh tunnel from scripts/mesh-demo-agent.sh)"
availability: while scripts/mesh-demo-agent.sh keeps it up
---

# mesh-mini

A lightweight **echo agent** running on `aadarshs-mac-mini-2`, reachable through
the communicate router as a native peer over Tailscale + SSH. It exists to
demonstrate cross-device relay: the orchestrator sends it a message and captures
its reply — proving the transport a full remote Claude session would use.

It is NOT a full model; it echoes a fixed acknowledgement plus your message. To
point this same path at a real Claude session on another device, bridge that
session in with the orchestrator identity (see openwebui/README.md).

## What to ask me for
- "Say hi to the mesh-mini agent on the mac mini" — a round-trip liveness check
  across the tailnet.
