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
| Roster | `communicate homi agents --json` | the directory: name, measured `state`, seat surface, capability card |

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

## How dispatch works (v0.3 — one road, homi mail)

`list_agents` reads `communicate homi agents --json` and reports each row's
MEASURED `state`: only an agent homi just measured `live` is called
dispatchable now. Everything else on the roster is still *mailable* — homi
stores and forwards — and the tool says exactly that rather than promising a
reply it cannot measure.

`ask_agent` is one call: `communicate homi ask <name> "<brief>" --from
<caller_name> --timeout N --json`. Local specialist or an agent on another
device, it is the same round trip — homi routes it, stores it durably, and
correlates the reply. When nobody answers in time the tool reports that the
message is **held in the agent's mailbox**, so the model does not resend.

Valves: `communicate_path`, `timeout_seconds`, `caller_name` (the homi identity
replies come back to; it is auto-claimed on first ask).

## Adding a specialist

1. Create `~/agents/<name>` with a `.venv` + `AGENTS.md` charter.
2. Give it a homi identity in that directory:
   `communicate homi claim <peer-name> --cwd ~/agents/<name>` — or let
   `communicate homi spawn <peer-name> --cli codex --cwd ~/agents/<name>`
   claim it and launch it in a seat in one verb.
3. `communicate homi describe <peer-name> --what "…" --ask-me-for "…"` so the
   orchestrator can tell what it is for.
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
  maintainer and <their-agent> correctly shown as
  not dispatchable. No hallucinated agents.
- "Have the gds agent generate a 10 micron radius ring resonator GDS" →
  dispatch → codex → gdsfactory → file on disk, path relayed verbatim:
  `/Users/aadarwal/agents/gds/out/ring-resonator-r10um.gds` (12,224 bytes).
- tidy3d offline-validation ask → `scripts/minimal_220nm_soi_waveguide.py`,
  validation **Passed**, estimated grid 231,525 cells (~27.5 nm), nothing
  submitted to the cloud. Flexcompute key validated at configure time.

## Cross-device relay (superseded in v0.3)

v0.2 carried its own relay: a mailbox daemon on `/tmp/cc-socks/orchestrator.sock`,
`communicate whereis` + `claude bridge` to reach a remote session, and a poll of
`mailbox.jsonl` for the reply. All of it is now homi's job — the fabric owns
identity, routing, durability and reply correlation across devices — so
`ask_agent` makes one `communicate homi ask` call and that machinery is gone
from the tool. A remote **interactive** Claude still only answers when it takes
a turn, and homi holds the message until then instead of losing it.

## Real cross-device codex agent (mini-agent)

For genuine generative work on another device, run a **codex** agent there —
same dispatch as the local specialists, just a remote device. `mini-agent`
(`agents/mini/AGENTS.md` charter) is a codex assistant on
`aadarshs-mac-mini-2`. Verified 2026-08-09 over the v0.2 codex path: "ask the
mini agent for a random sentence" → an actual sentence generated on the mini.
Under v0.3 the same agent is reached by claiming a homi identity on that
device and linking the two homis (`communicate homi link`); `ask_agent` then
routes to it by name with no per-kind special case.

Codex PATH note: a standalone codex install lands at `~/.local/bin/codex`,
which is off the PATH of a non-interactive ssh shell — so `communicate link`
used to report "codex: not installed" for such hosts. `lib/codex.sh` now
prepends `$HOME/.local/bin` and the standalone release bin to the remote PATH
for every codex invocation (override with `$COMM_CODEX_PATH`); no remote
dotfile edits needed. Remote `--dir` must be an **absolute** path (a leading
`~` is not expanded through the ssh command quoting).

**Echo transport demo (optional).** `scripts/mesh-demo-agent.sh up [device]
[name]` stands up a lightweight always-on echo responder (`mesh-mini`) plus the
two-way tunnel — a pure transport demo of the Claude reply-capture path when no
real agent is available. Tear down with `scripts/mesh-demo-agent.sh down`. Note:
only one reverse-forward can own `orchestrator.sock` on a remote at a time —
don't run this tunnel and an auto-bridge to the same device at once.

## Sharing beyond this machine (not enabled)

v1 binds everything to localhost. To reach the UI from another tailnet
device later: `tailscale serve 8080` (and keep open-terminal localhost-only).
