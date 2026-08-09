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

## Sharing beyond this machine (not enabled)

v1 binds everything to localhost. To reach the UI from another tailnet
device later: `tailscale serve 8080` (and keep open-terminal localhost-only).
