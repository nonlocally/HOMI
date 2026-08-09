# Open WebUI Orchestrator over the communicate bus — Design

Date: 2026-08-08 · Status: approved (user), hands-off execution authorized
Branch: `openwebui-orchestrator`

## Goal

A single **orchestrator agent** you chat with in Open WebUI. Its hands are a
terminal (open-terminal); its address book is the `communicate` bus. It
discovers which specialist agents exist, decides who should handle a request,
dispatches the work, and relays the reply. First two specialists: a **tidy3d
agent** (FDTD photonics simulation) and a **gds agent** (gdsfactory layout),
both Codex agents in dedicated environments on this machine.

Explicitly NOT a model-picker: there is one chat entry point (the
orchestrator), and routing is the orchestrator's own decision, made by reading
the live agent directory.

## Architecture

```
You ──chat──▶ Open WebUI (127.0.0.1:8080, bare-metal via uv)
                 │  orchestrator model = qwen3:14b (Ollama, 127.0.0.1:11434)
                 │  two tools:
                 ├─▶ dispatch tool (in-process python): list_agents / ask_agent
                 └─▶ open-terminal (bare-metal, 127.0.0.1:8000, API key)
                          │ full user shell on this host
                          ▼
                 communicate  (agents / directory / codex ask / route)
                          │
        ┌─────────────────┴──────────────────┐
        ▼                                    ▼
  tidy3d-agent (codex peer)            gds-agent (codex peer)
  ~/agents/tidy3d  venv: tidy3d        ~/agents/gds  venv: gdsfactory
  FDTD via Flexcompute cloud           layout generation, fully local
```

All services bind 127.0.0.1 only. The orchestrator does no engineering itself;
heavy execution happens inside the specialists' Codex sandboxes
(workspace-write, confined to their project dirs).

## Decisions taken (with the fork they resolve)

1. **Specialist brains = Codex via communicate** (not a local model, not a new
   API account). `communicate codex ask` is synchronous request/response —
   the only communicate verb that returns the reply to a non-Claude caller —
   so it is the dispatch verb. `route` stays for fire-and-forget notification
   of Claude sessions.
2. **Orchestrator brain = qwen3:14b on Ollama.** Local, free; routing +
   command composition is a low enough bar for a 14B model. Swappable later
   (30b-a3b, or an API model) without touching anything else.
3. **open-terminal bare-metal on the host, not Docker.** The terminal's whole
   purpose here is reaching the host-side communicate bus; a container cannot
   without shipping host creds into it (which the operator's rules forbid).
   Accepted consequence: the orchestrator has a user-level shell. Mitigations:
   localhost-only binding, API-key auth, local-only model, dangerous work
   delegated to Codex sandboxes.
4. **Dispatch tool alongside the terminal** (approach 1 + 3 hybrid): a narrow,
   structured path (`list_agents` / `ask_agent`) for the common case — far more
   reliable for a 14B tool-caller — with the terminal for everything else.
5. **The repo's `registry/` is the single source of truth for capability.**
   (It landed in PRs #2/#3 after this design started; it replaces the
   `registry.toml` from the draft design.) Specialist entries follow the
   existing schema (`registry/<peer-name>.md`, YAML frontmatter + prose) plus
   one added key, `workdir:`, which the dispatch tool needs to run
   `codex ask --dir`. The frontmatter parser already passes unknown keys
   through, and `workdir` is honest metadata about the agent.
6. **One memory per specialist.** The dispatch tool uses codex thread
   `peer-<name>` — the same thread name the `codex peer` daemon uses — so a
   specialist has a single continuous conversation whether reached from
   Open WebUI or from a Claude session via SendMessage.

## Components

### Infrastructure (installed by `openwebui/setup.sh`, idempotent)

- **Ollama** via brew, `brew services start ollama`, `ollama pull qwen3:14b`.
- **Open WebUI** via `uv tool install --python 3.12 open-webui`; served
  bare-metal (`open-webui serve`, port 8080) so the dispatch tool can
  subprocess `communicate` on the host. Managed with nohup + pidfile under
  `~/.local/state/communicate/openwebui/`.
- **open-terminal** via `uv tool install open-terminal`; run
  `--host 127.0.0.1 --port 8000 --api-key <generated>` (key generated once,
  stored 0600 in `~/.local/state/communicate/secrets/open-terminal.key`).
  Registered in Open WebUI as an external tool server per its official docs.

### Specialist environments

- `~/agents/tidy3d/`: uv venv with `tidy3d`; Flexcompute API key configured
  from `~/.local/state/communicate/secrets/tidy3d.key` (already stashed 0600;
  also mirrored to Infisical if a scope fits). `AGENTS.md` charter: role,
  conventions, output layout, and the rule that long FDTD runs are SUBMITTED
  (return task IDs) rather than awaited.
- `~/agents/gds/`: uv venv with `gdsfactory`. `AGENTS.md` charter: role,
  output layout (`out/*.gds`), verification habits (DRC-ish sanity checks).
- Both stood up as peers: `communicate codex peer local <name> --dir <dir>
  --auto` → they appear in `communicate agents` and are messageable from
  Claude sessions.
- Both get `registry/<name>.md` entries (kind: codex, device:
  aadarshs-mac-air-2, workdir, prose description used for routing).

### Dispatch tool (`openwebui/dispatch_tool.py`)

Open WebUI Tool class, ~60–100 lines, subprocesses the `communicate` CLI:

- `list_agents()` → runs `communicate directory --json`, merges the live
  router table (`communicate agents`), returns a compact roster: name, kind,
  live?, dispatchable?, one-line capability summary (first prose paragraph of
  the registry entry).
- `ask_agent(name, message)` → looks the name up in `directory --json`;
  requires `kind: codex` + reachable device + `workdir`. Runs
  `communicate codex ask <device> --dir <workdir> --thread peer-<name>
  --auto -- <message>` with a 300 s default timeout. When the registry
  `device` equals this host's own name (tailnet hostname or `hostname -s`),
  the tool substitutes `local` so dispatch never SSHes to itself. Returns stdout, or the
  stderr tail on failure so the model can react. Non-codex agents return a
  clear "listed but not dispatchable from this surface (v1)" message.

### Orchestrator model (Open WebUI workspace model)

qwen3:14b + system prompt (`openwebui/orchestrator.md`): discover before
deciding; dispatch with self-contained briefs (the specialist lacks chat
context); relay replies with artifact paths verbatim; use the terminal for
inspection (`ls`/`cat` under `~/agents/*`), status checks (`communicate
status`), and anything the narrow tool can't do; never paste secrets; keep
commands non-interactive. Low temperature; native function calling.

## Data flow (canonical)

"simulate a 220nm SOI directional coupler, 500nm gap — coupling ratio?" →
orchestrator → `list_agents` → picks `tidy3d-agent` → `ask_agent` with a
self-contained brief → `codex exec` (resume thread `peer-tidy3d-agent`) writes
and runs the tidy3d script in `~/agents/tidy3d` → submits to Flexcompute if
it's a real solve, returns task id / results → stdout → orchestrator relays.
Follow-ups reuse the same thread, so specialist context persists.

## Error handling

- `ask_agent`: 300 s timeout; stderr tail returned as text; specialist-down →
  message suggesting `communicate status`. Codex resume rolloff already
  retried fresh inside `communicate`.
- Tools degrade independently (terminal down ≠ dispatch down).
- tidy3d without a valid key must fail fast with a clear message, not hang.
- Long solves: submit-and-return-task-id policy in the tidy3d charter.

## Security

- 127.0.0.1 bindings everywhere; nothing Tailscale-served in v1.
- open-terminal API key generated, 0600, registered in Open WebUI config.
- Open WebUI admin account created programmatically during hands-off setup;
  generated credentials stored 0600 in
  `~/.local/state/communicate/secrets/openwebui-admin.txt` for the operator.
- Flexcompute key only in `~/.tidy3d` config + the 0600 stash; never in the
  repo, prompts, or registry.
- Bare-metal terminal = user shell, accepted deliberately (see decision 3).

## Testing (bottom-up smoke chain)

1. `communicate codex ask local --dir ~/agents/gds -- "print gdsfactory version"`
   and the tidy3d equivalent (import + version; no cloud call).
2. Dispatch tool standalone (`python3 -c` invoke of both methods).
3. Orchestrator through the Open WebUI API: "which agents are available?" must
   produce a `list_agents` call and a roster answer.
4. Full round trip: "have the gds agent generate a 10 µm ring resonator GDS,
   report the path" → file exists on disk.
5. tidy3d graceful-failure check if the key turns out invalid.

## Out of scope (v1)

Claude-session specialists with async reply capture; specialists on remote
mesh devices; Tailscale-serving the UI; PR-triggered wakes surfacing in chat;
Docker/egress-firewalled terminal.

## Repo layout added by this work

```
openwebui/
  README.md          setup + operations + where the secrets live
  dispatch_tool.py   Open WebUI Tool: list_agents / ask_agent
  orchestrator.md    orchestrator system prompt
  setup.sh           idempotent installer/launcher for the whole stack
agents/
  tidy3d/AGENTS.md   specialist charters (installed to ~/agents/<name>/)
  gds/AGENTS.md
registry/
  tidy3d-agent.md    capability entries (schema + workdir key)
  gds-agent.md
```
