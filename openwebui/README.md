# Open WebUI orchestrator — operations

One orchestrator chat (local qwen3:14b) that discovers agents on the
communicate bus and dispatches work to codex specialists. Design:
`docs/superpowers/specs/2026-08-08-openwebui-orchestrator-design.md`.

## Runtime map

| Piece | Where | Notes |
|---|---|---|
| Open WebUI | http://127.0.0.1:8080 | bare-metal (`uv tool`), admin creds in secrets dir |
| open-terminal | http://127.0.0.1:8000 | bare-metal, API key required (401 otherwise) |
| Ollama | http://127.0.0.1:11434 | brew service, model `qwen3:14b` |
| Orchestrator model | Open WebUI model id `orchestrator` | base qwen3:14b + `orchestrator.md` prompt + tool `agent_dispatch` |
| Dispatch tool | Open WebUI tool id `agent_dispatch` | source: `openwebui/dispatch_tool.py` |
| Specialists | `~/agents/tidy3d`, `~/agents/gds` | codex peers `tidy3d-agent` / `gds-agent`, threads `peer-<name>` |
| Registry | `registry/*.md` | capability entries incl. `workdir:` used for dispatch |

## Secrets (all 0600, never in the repo)

- `~/.local/state/communicate/secrets/openwebui-admin.txt` — Open WebUI admin login
- `~/.local/state/communicate/secrets/open-terminal.key` — terminal API key
- `~/.local/state/communicate/secrets/tidy3d.key` + `~/.tidy3d` — Flexcompute

## Operate

```sh
openwebui/setup.sh          # idempotent bring-up (installs anything missing)
openwebui/setup.sh status   # what's running
openwebui/setup.sh stop     # stop open-webui + open-terminal
```

Logs + pidfiles: `~/.local/state/communicate/openwebui/`.
Peers: `communicate status` / restart with
`communicate codex peer local <name> --dir ~/agents/<dir> --auto`.

## After merging this branch

The dispatch tool's `communicate_path` valve defaults to the worktree
(`.../communicate-owui/bin/communicate`) because the registry entries live on
this branch. After merge, update the valve (Workspace → Tools → Agent
Dispatch → Valves) to `/Users/aadarwal/src/aadarwal/communicate/bin/communicate`.

## Adding a specialist

1. Create `~/agents/<name>` with a `.venv` + `AGENTS.md` charter.
2. Add `registry/<peer-name>.md` (frontmatter must include `kind: codex`,
   `device:`, `workdir:`).
3. `communicate codex peer local <peer-name> --dir <workdir> --auto`.
4. Nothing else — the orchestrator discovers it on its next `list_agents`.

## API callers: tool execution contract

Model-attached tools execute server-side only on the **UI/chat** path. The
bare OpenAI-compatible endpoint (`/api/chat/completions`) follows the OpenAI
contract instead: pass `"tool_ids": ["agent_dispatch"]` in the request and
handle the returned `tool_calls` yourself (execute, append a `tool` message,
re-call). Without `tool_ids`, the model gets no tool specs at all — a small
local model will then *roleplay* tool results, convincingly and wrongly.

## Verification (2026-08-08, hands-off run)

- Roster question in the UI → real `list_agents` execution ("Explored
  list_agents" block): gds-agent + tidy3d-agent LIVE/dispatchable; the
  maintainer and peer-agent correctly shown as
  not dispatchable. No hallucinated agents.
- "Have the gds agent generate a 10 micron radius ring resonator GDS" →
  dispatch → codex → gdsfactory → file on disk, path relayed verbatim:
  `/Users/aadarwal/agents/gds/out/ring-resonator-r10um.gds` (12,224 bytes).
- tidy3d offline-validation ask → `scripts/minimal_220nm_soi_waveguide.py`,
  validation **Passed**, estimated grid 231,525 cells (~27.5 nm), nothing
  submitted to the cloud. Flexcompute key validated at configure time.

## Cross-device relay (v0.2 — remote Claude agents)

The orchestrator has its own peer identity: a persistent mailbox daemon
(`cc_peer.py mailbox`) listening on `/tmp/cc-socks/orchestrator.sock` with a
maintained sidecar named `orchestrator`. `ask_agent` uses it as a return
address, so a **Claude-kind** registry agent is now dispatchable too:

1. resolve the agent via `communicate whereis`; if it isn't reachable locally,
   auto-bridge it (`communicate claude bridge <device> <session>`) using the
   orchestrator socket as `$CLAUDE_CODE_MESSAGING_SOCKET`;
2. `communicate send <socket> --as orchestrator` (from = the orchestrator
   socket, reverse-forwarded to the remote so replies route home);
3. poll `mailbox.jsonl` for the reply, unwrap the cross-session envelope,
   return it.

A remote **interactive** Claude only answers when it takes a turn (or approves
the held peer message), so replies are best-effort for human-driven sessions.

**Demo agent.** `scripts/mesh-demo-agent.sh up [device] [name]` stands up a
lightweight always-on echo agent (`mesh-mini`, registry entry included) on a
remote device and wires the two-way tunnel, so the relay is demonstrable
every time regardless of any human. Verified 2026-08-09: in the UI, "Say hi to
the mesh-mini agent on the mac mini" → `list_agents` + `ask_agent` →
`hi back from mesh-mini on aadarshs-mac-mini-2` captured in chat
(screenshot: `~/.local/state/communicate/openwebui/mesh-demo.png`). Tear down
with `scripts/mesh-demo-agent.sh down`.

Note: only one reverse-forward can own `orchestrator.sock` on a given remote at
a time — don't run the mesh-demo tunnel and an auto-bridge to the same device
simultaneously.

## Sharing beyond this machine (not enabled)

v1 binds everything to localhost. To reach the UI from another tailnet
device later: `tailscale serve 8080` (and keep open-terminal localhost-only).
