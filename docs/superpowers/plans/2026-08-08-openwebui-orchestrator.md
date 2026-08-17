# Open WebUI Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One orchestrator agent in Open WebUI (qwen3:14b, local) that discovers specialists via the communicate registry, dispatches work to tidy3d/gds Codex agents, and has an open-terminal as its host shell.

**Architecture:** Open WebUI (bare-metal, uv) + Ollama serve the orchestrator model; a dispatch Tool (python, in-process) shells out to `communicate` for discovery (`directory --json`) and synchronous dispatch (`codex ask`); open-terminal (bare-metal, localhost, API-key) is registered via Open WebUI's native Integrations → Open Terminal; specialists are Codex peers with dedicated uv venvs under `~/agents/`.

**Tech Stack:** Ollama + qwen3:14b, Open WebUI (uv tool, py3.12), open-terminal (uv tool), communicate CLI (this repo), codex CLI, uv venvs (tidy3d, gdsfactory).

## Global Constraints

- Every service binds `127.0.0.1` only — never `0.0.0.0`.
- Commits: NO AI-attribution trailers (operator rule); use `--no-gpg-sign` tonight (1Password signing unavailable while operator sleeps).
- Secrets only in `~/.local/state/communicate/secrets/*` (0600) or tool-native config (`~/.tidy3d`); never in the repo, registry, or prompts. tidy3d key already at `~/.local/state/communicate/secrets/tidy3d.key`.
- Runtime `communicate` path during this branch's life: `/Users/aadarwal/src/aadarwal/communicate-owui/bin/communicate` (the worktree — it has the new registry entries; flip to the main checkout after merge).
- Specialist workdirs: `/Users/aadarwal/agents/tidy3d`, `/Users/aadarwal/agents/gds`.
- Python envs via `uv` exclusively.
- State/pids/logs under `~/.local/state/communicate/openwebui/` (gitignored area, not the repo).

---

### Task 1: Specialist environments, charters, registry entries

**Files:**
- Create: `agents/tidy3d/AGENTS.md`, `agents/gds/AGENTS.md` (in repo; installed by copy)
- Create: `registry/tidy3d-agent.md`, `registry/gds-agent.md`
- Create (filesystem, not repo): `~/agents/tidy3d/`, `~/agents/gds/` with `.venv`s

**Interfaces:**
- Produces: peer names `tidy3d-agent`, `gds-agent`; workdirs above; venvs with `tidy3d` / `gdsfactory` importable via `uv run --python .venv`; `~/.tidy3d` config holding the API key.

- [ ] **Step 1: Create workdirs + venvs + installs (background-friendly)**

```bash
mkdir -p ~/agents/tidy3d ~/agents/gds
cd ~/agents/tidy3d && uv venv --python 3.12 && uv pip install --python .venv tidy3d
cd ~/agents/gds   && uv venv --python 3.12 && uv pip install --python .venv gdsfactory
```

- [ ] **Step 2: Configure the tidy3d key (validates against Flexcompute)**

```bash
cd ~/agents/tidy3d && .venv/bin/tidy3d configure --apikey "$(cat ~/.local/state/communicate/secrets/tidy3d.key)"
```
Expected: "Configured successfully" (writes `~/.tidy3d/config`). If validation fails, record the failure verbatim for the handoff — do NOT block the rest of the build (spec: graceful-failure path).

- [ ] **Step 3: Write `agents/gds/AGENTS.md`** (repo copy; install with `cp` to `~/agents/gds/AGENTS.md`)

```markdown
# gds-agent

You are the GDS layout specialist. You run inside `~/agents/gds` with a uv venv
(`.venv`) that has **gdsfactory** installed. Always use `.venv/bin/python`.

## Conventions
- Scripts in `scripts/`, generated layouts in `out/` (create both as needed).
- Name outputs `out/<component>-<key-params>.gds`; after writing a GDS, print
  its absolute path and the top-cell bounding box (from `Component.bbox()`).
- Sanity-check every layout before reporting: non-empty component, expected
  port count, ports on grid. Report the checks you ran.
- You may be asked follow-ups in the same thread; keep scripts re-runnable.

## Boundaries
- Work only under this directory. Never touch `~/.ssh`, keychains, or other
  agents' directories. No network calls are needed for layout work.
```

- [ ] **Step 4: Write `agents/tidy3d/AGENTS.md`** (repo copy; install to `~/agents/tidy3d/AGENTS.md`)

```markdown
# tidy3d-agent

You are the FDTD simulation specialist. You run inside `~/agents/tidy3d` with a
uv venv (`.venv`) that has **tidy3d** installed. Always use `.venv/bin/python`.
The Flexcompute API key is already configured in `~/.tidy3d`.

## Conventions
- Scripts in `scripts/`, results in `results/` (create as needed).
- Build + validate simulations locally first (`Simulation` construction and
  `sim.validate_pre_upload()` are offline). Estimate cost with
  `web.estimate_cost(...)` before running.
- **Never block on a long solve**: for anything beyond a trivial test run,
  submit with `web.upload(...)` + `web.start(...)`, print the `task_id`, and
  return. Status checks are a separate ask (`web.monitor`/`web.get_info`).
- Always report: what was simulated, resolution/runtime settings, task id or
  local result path, and headline numbers.

## Boundaries
- Work only under this directory. The API key stays in `~/.tidy3d` — never
  print it, never copy it elsewhere. If the key is invalid, say so plainly
  and stop; do not retry with made-up credentials.
```

- [ ] **Step 5: Install charters + verify venvs**

```bash
cd /Users/aadarwal/src/aadarwal/communicate-owui
cp agents/gds/AGENTS.md ~/agents/gds/AGENTS.md
cp agents/tidy3d/AGENTS.md ~/agents/tidy3d/AGENTS.md
~/agents/gds/.venv/bin/python -c "import gdsfactory; print(gdsfactory.__version__)"
~/agents/tidy3d/.venv/bin/python -c "import tidy3d; print(tidy3d.__version__)"
```
Expected: two version strings, no tracebacks.

- [ ] **Step 6: Write `registry/gds-agent.md`**

```markdown
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
```

- [ ] **Step 7: Write `registry/tidy3d-agent.md`**

```markdown
---
name: tidy3d-agent
kind: codex
model: codex-default
device: aadarshs-mac-air-2
operator: aadarwal
updated: 2026-08-08
workdir: /Users/aadarwal/agents/tidy3d
reach:
  - "communicate codex ask local --dir /Users/aadarwal/agents/tidy3d --thread peer-tidy3d-agent"
  - "codex peer 'tidy3d-agent' — SendMessage from any bridged Claude session"
availability: 24/7 while aadarshs-mac-air-2 is awake; solves run in Flexcompute cloud
---

# tidy3d-agent

Codex specialist for **FDTD simulation with tidy3d** (Flexcompute cloud):
builds and validates Simulation objects locally, estimates cost, submits
solves, and post-processes results (S-parameters, field monitors, coupling
ratios). Long solves are submitted, not awaited — you get a task id.

## What to ask me for
- "Set up and submit an FDTD for <device/geometry>, report the task id."
- Cost estimates before running; result post-processing for finished tasks.
- NOTE: check <their-agent>'s library FIRST for
  stored fields/surrogates before paying for a fresh solve.
```

- [ ] **Step 8: Commit**

```bash
cd /Users/aadarwal/src/aadarwal/communicate-owui
git add agents/ registry/tidy3d-agent.md registry/gds-agent.md
git commit --no-gpg-sign -m "Add tidy3d/gds specialist charters and registry entries"
```

---

### Task 2: Stand up the Codex peers and verify the bus view

**Files:** none in repo (runtime state under `~/.local/state/communicate/`)

**Interfaces:**
- Consumes: Task 1 workdirs/charters.
- Produces: live peers `tidy3d-agent`, `gds-agent` visible in `communicate agents` and `communicate directory` (LIVE=yes); codex threads named `peer-tidy3d-agent` / `peer-gds-agent`.

- [ ] **Step 1: Start both peers (worktree CLI, --auto for workspace-write)**

```bash
COMM=/Users/aadarwal/src/aadarwal/communicate-owui/bin/communicate
"$COMM" codex peer local tidy3d-agent --dir /Users/aadarwal/agents/tidy3d --auto
"$COMM" codex peer local gds-agent   --dir /Users/aadarwal/agents/gds   --auto
```
Expected: two `ok codex peer '<name>' is live` lines.

- [ ] **Step 2: Verify discovery (both surfaces)**

```bash
"$COMM" agents            # both names present, TYPE=codex
"$COMM" directory         # both entries present, LIVE=yes
```

- [ ] **Step 3: Smoke each specialist through the bus (synchronous)**

```bash
"$COMM" codex ask local --dir /Users/aadarwal/agents/gds --thread peer-gds-agent -- \
  "Reply with one line: 'gds-agent ready, gdsfactory <version>' using .venv/bin/python to read the version."
"$COMM" codex ask local --dir /Users/aadarwal/agents/tidy3d --thread peer-tidy3d-agent -- \
  "Reply with one line: 'tidy3d-agent ready, tidy3d <version>' using .venv/bin/python. Do not submit anything."
```
Expected: each returns its ready-line. (These also seed the shared threads.)

---

### Task 3: The dispatch tool

**Files:**
- Create: `openwebui/dispatch_tool.py`
- Test: run standalone via `python3 - <<'EOF' ... EOF` (no pytest in this repo; tests are executable smoke steps)

**Interfaces:**
- Consumes: `communicate directory --json` (fields: name, kind, device, workdir, file, live), `communicate codex ask`.
- Produces: Open WebUI Tool exposing `list_agents() -> str` and `ask_agent(name: str, message: str) -> str`. Valves: `communicate_path` (default worktree bin), `timeout_seconds` (default 300).

- [ ] **Step 1: Write `openwebui/dispatch_tool.py`**

```python
"""
title: Agent Dispatch
author: aadarwal
description: Discover agents on the communicate bus and dispatch work to codex specialists (synchronous request/response).
version: 0.1.0
"""

import json
import os
import socket
import subprocess

from pydantic import BaseModel, Field

REPO_ROOT_HINT = "/Users/aadarwal/src/aadarwal/communicate-owui"


def _self_names():
    short = socket.gethostname().split(".")[0].lower()
    return {"local", "localhost", short}


class Tools:
    class Valves(BaseModel):
        communicate_path: str = Field(
            default=os.path.join(REPO_ROOT_HINT, "bin", "communicate"),
            description="Absolute path to the communicate CLI",
        )
        timeout_seconds: int = Field(
            default=300, description="Max seconds to wait for a specialist reply"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _run(self, args, timeout):
        return subprocess.run(
            [self.valves.communicate_path] + args,
            capture_output=True, text=True, timeout=timeout,
        )

    def _directory(self):
        out = self._run(["directory", "--json"], 30)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[-500:] or "communicate directory failed")
        return json.loads(out.stdout)

    def list_agents(self) -> str:
        """List every agent in the communicate registry: name, kind, live/reachable now, dispatchable from here, and what it is for. Call this before choosing where to send work."""
        try:
            rows = self._directory()
        except Exception as e:
            return f"ERROR listing agents: {e}"
        if not rows:
            return "No agents in the registry."
        lines = []
        for r in rows:
            name = r.get("name", "?")
            kind = r.get("kind", "?")
            live = "LIVE" if r.get("live") else "offline"
            dispatchable = kind == "codex" and bool(r.get("workdir"))
            summary = ""
            f = r.get("file")
            if f:
                path = os.path.join(os.path.dirname(os.path.dirname(self.valves.communicate_path)), f)
                try:
                    body = open(path, encoding="utf-8").read().split("---", 2)[-1]
                    paras = [p.strip().replace("\n", " ") for p in body.split("\n\n")
                             if p.strip() and not p.strip().startswith("#")]
                    summary = (paras[0][:300]) if paras else ""
                except OSError:
                    pass
            lines.append(
                f"- {name} [{kind}, {live}, "
                f"{'dispatchable via ask_agent' if dispatchable else 'NOT dispatchable from this surface'}]"
                + (f": {summary}" if summary else "")
            )
        return "\n".join(lines)

    def ask_agent(self, name: str, message: str) -> str:
        """Send a self-contained task brief to a codex specialist agent by name and return its reply. The specialist has its own persistent memory but does NOT see this chat - include all needed context in the message."""
        try:
            rows = self._directory()
        except Exception as e:
            return f"ERROR: could not read the agent directory: {e}"
        entry = next((r for r in rows if r.get("name") == name), None)
        if entry is None:
            return f"ERROR: no agent named '{name}'. Call list_agents for the roster."
        if entry.get("kind") != "codex" or not entry.get("workdir"):
            return (f"'{name}' is listed but not dispatchable from this surface (v1): "
                    f"kind={entry.get('kind')}. It may be reachable from a Claude session instead.")
        device = (entry.get("device") or "local").lower()
        if device in _self_names():
            device = "local"
        args = ["codex", "ask", device, "--dir", entry["workdir"],
                "--thread", f"peer-{name}", "--auto", "--", message]
        try:
            out = self._run(args, self.valves.timeout_seconds)
        except subprocess.TimeoutExpired:
            return (f"TIMEOUT: '{name}' did not reply within {self.valves.timeout_seconds}s. "
                    "For long solves the specialist should submit and return a task id - "
                    "consider re-asking with that instruction.")
        if out.returncode != 0 and not out.stdout.strip():
            return (f"ERROR from '{name}': {out.stderr.strip()[-800:] or 'no output'}\n"
                    "Check `communicate status` in the terminal.")
        return out.stdout.strip()
```

- [ ] **Step 2: Standalone smoke test (real bus, gds agent)**

```bash
cd /Users/aadarwal/src/aadarwal/communicate-owui && python3 - <<'EOF'
import sys; sys.path.insert(0, "openwebui")
from dispatch_tool import Tools
t = Tools()
roster = t.list_agents()
print(roster)
assert "gds-agent" in roster and "tidy3d-agent" in roster, "roster missing specialists"
assert "dispatchable via ask_agent" in roster, "no dispatchable agents"
r = t.ask_agent("gds-agent", "Reply with exactly: DISPATCH-OK")
print("reply:", r[:200])
assert "DISPATCH-OK" in r, "dispatch round-trip failed"
bad = t.ask_agent("nope-agent", "hi")
assert bad.startswith("ERROR: no agent named"), bad
print("ALL PASS")
EOF
```
Expected: roster lines + `ALL PASS`.

- [ ] **Step 3: Commit**

```bash
git add openwebui/dispatch_tool.py
git commit --no-gpg-sign -m "Add Open WebUI dispatch tool (list_agents/ask_agent over communicate)"
```

---

### Task 4: Orchestrator prompt, setup.sh, service bring-up

**Files:**
- Create: `openwebui/orchestrator.md`
- Create: `openwebui/setup.sh` (executable)
- Create: `openwebui/README.md`

**Interfaces:**
- Consumes: brew ollama service; uv.
- Produces: Open WebUI at `http://127.0.0.1:8080`, open-terminal at `http://127.0.0.1:8000` (key in `~/.local/state/communicate/secrets/open-terminal.key`), `qwen3:14b` pulled; pidfiles+logs under `~/.local/state/communicate/openwebui/`.

- [ ] **Step 1: Write `openwebui/orchestrator.md`**

```markdown
You are the ORCHESTRATOR. You never do engineering work yourself - you route.

Your surfaces:
- Tool `list_agents`: the roster of specialist agents (who exists, who is
  live, who is dispatchable, what each is for).
- Tool `ask_agent(name, message)`: send ONE self-contained task brief to a
  codex specialist and get its reply. The specialist has persistent memory of
  its own past tasks but CANNOT see this chat - always include every needed
  parameter, unit, and file-path expectation in the brief.
- The Terminal (Open Terminal integration): a real shell on this host. Use it
  to inspect results (`ls`/`cat` under ~/agents/*/), check the bus
  (`communicate status`, `communicate agents`), and for anything the narrow
  tools cannot do. Commands must be non-interactive; never run editors,
  `sudo`, or anything that prompts.

Doctrine, in order:
1. DISCOVER: call list_agents before your first dispatch in a conversation
   (and again if something seems off).
2. DECIDE: pick the specialist whose capability entry matches the request.
   Layout/GDS work -> gds-agent. FDTD/simulation/S-parameters -> tidy3d-agent.
   If a stored result may exist (the FDTD librarian's library), say so and
   prefer checking before paying for a new solve.
3. DISPATCH: write a precise, self-contained brief. One task per ask_agent
   call. For simulations, instruct: validate locally, estimate cost, submit,
   return the task id - never wait for long solves.
4. RELAY: report the specialist's answer faithfully. Quote file paths and
   task ids VERBATIM. If the reply contains an error, show it and propose the
   next step - do not invent results.

Rules:
- Never paste secrets or API keys into chat, briefs, or the terminal.
- If a specialist is offline or errors, check `communicate status` in the
  terminal and tell the user what you found.
- Keep answers short: what was dispatched, to whom, what came back.
```

- [ ] **Step 2: Write `openwebui/setup.sh`** — idempotent bring-up with `status`/`stop` verbs

```bash
#!/usr/bin/env bash
# openwebui/setup.sh — bring up the orchestrator stack on this host.
#   setup.sh          install anything missing, start everything, print status
#   setup.sh status   show what is running
#   setup.sh stop     stop open-webui + open-terminal (leaves ollama service)
set -o pipefail
STATE="$HOME/.local/state/communicate/openwebui"
SECRETS="$HOME/.local/state/communicate/secrets"
OWUI_PORT=8080; OT_PORT=8000
mkdir -p "$STATE" "$SECRETS"; chmod 700 "$SECRETS"

note() { printf '\033[36msetup\033[0m %s\n' "$*" >&2; }
up()   { curl -sf -o /dev/null -m 3 "$1"; }

pidfile_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_bg() { # name, pidfile, logfile, cmd...
  local name="$1" pf="$2" lf="$3"; shift 3
  pidfile_alive "$pf" && { note "$name already running (pid $(cat "$pf"))"; return 0; }
  nohup "$@" >"$lf" 2>&1 & echo $! > "$pf"; disown 2>/dev/null || true
  note "$name started (pid $(cat "$pf"), log $lf)"
}

case "${1:-start}" in
  stop)
    for s in open-webui open-terminal; do
      pidfile_alive "$STATE/$s.pid" && kill "$(cat "$STATE/$s.pid")" && note "stopped $s"
      rm -f "$STATE/$s.pid"
    done; exit 0;;
  status)
    for s in open-webui open-terminal; do
      pidfile_alive "$STATE/$s.pid" && note "$s: up (pid $(cat "$STATE/$s.pid"))" || note "$s: down"
    done
    up "http://127.0.0.1:11434/api/tags" && note "ollama: up" || note "ollama: down"
    exit 0;;
esac

# --- ollama + model ---
command -v ollama >/dev/null || brew install ollama
brew services list | grep -q '^ollama.*started' || brew services start ollama
for i in $(seq 1 30); do up "http://127.0.0.1:11434/api/tags" && break; sleep 1; done
ollama list | grep -q '^qwen3:14b' || { note "pulling qwen3:14b (large)"; ollama pull qwen3:14b; }

# --- open-terminal (bare metal, localhost, api key) ---
KEYF="$SECRETS/open-terminal.key"
[ -f "$KEYF" ] || { openssl rand -hex 24 > "$KEYF"; chmod 600 "$KEYF"; }
command -v open-terminal >/dev/null || uv tool install open-terminal
start_bg open-terminal "$STATE/open-terminal.pid" "$STATE/open-terminal.log" \
  open-terminal run --host 127.0.0.1 --port "$OT_PORT" --api-key "$(cat "$KEYF")"

# --- open webui (bare metal, localhost) ---
command -v open-webui >/dev/null || uv tool install --python 3.12 open-webui
start_bg open-webui "$STATE/open-webui.pid" "$STATE/open-webui.log" \
  env OLLAMA_BASE_URL="http://127.0.0.1:11434" WEBUI_URL="http://127.0.0.1:$OWUI_PORT" \
  open-webui serve --host 127.0.0.1 --port "$OWUI_PORT"

for i in $(seq 1 60); do up "http://127.0.0.1:$OWUI_PORT/health" && break; sleep 2; done
up "http://127.0.0.1:$OWUI_PORT/health" && note "open-webui: ready on :$OWUI_PORT" || note "open-webui: NOT healthy yet — check $STATE/open-webui.log"
up "http://127.0.0.1:$OT_PORT/docs" && note "open-terminal: ready on :$OT_PORT" || note "open-terminal: NOT healthy — check $STATE/open-terminal.log"
```

- [ ] **Step 3: Run it and verify all three services**

```bash
chmod +x openwebui/setup.sh && openwebui/setup.sh
openwebui/setup.sh status
curl -s http://127.0.0.1:8080/health
curl -s -H "Authorization: Bearer $(cat ~/.local/state/communicate/secrets/open-terminal.key)" http://127.0.0.1:8000/files/cwd
```
Expected: health returns `{"status":true}` (or similar), files/cwd returns JSON (auth works). qwen3:14b in `ollama list`.

- [ ] **Step 4: Write `openwebui/README.md`** — setup, operations, secret locations, how to flip `communicate_path` after merge, how to enable Tailscale serving later. Content: summarize this plan's runtime facts (ports, pidfiles, key files, valve defaults, the two specialists, orchestrator model name) in ≤60 lines.

- [ ] **Step 5: Commit**

```bash
git add openwebui/orchestrator.md openwebui/setup.sh openwebui/README.md
git commit --no-gpg-sign -m "Add orchestrator prompt, stack setup script, and ops README"
```

---

### Task 5: Headless Open WebUI configuration

**Files:** none in repo (Open WebUI DB state). Record generated admin creds at `~/.local/state/communicate/secrets/openwebui-admin.txt` (0600).

**Interfaces:**
- Consumes: running services (Task 4), `openwebui/dispatch_tool.py` (Task 3), `openwebui/orchestrator.md` (Task 4).
- Produces: admin account; Tool id `agent_dispatch`; model id `orchestrator` (base `qwen3:14b`, system prompt, toolIds=[agent_dispatch], native function calling, num_ctx 16384); Open Terminal integration enabled (URL `http://127.0.0.1:8000` + key).

- [ ] **Step 1: Create admin via first-signup API; store creds**

```bash
PASS="$(openssl rand -base64 18)"
umask 077
printf 'url: http://127.0.0.1:8080\nemail: aadarsh@uchicago.edu\npassword: %s\n' "$PASS" \
  > ~/.local/state/communicate/secrets/openwebui-admin.txt
TOKEN=$(curl -s http://127.0.0.1:8080/api/v1/auths/signup \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"Aadarsh\",\"email\":\"aadarsh@uchicago.edu\",\"password\":\"$PASS\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
echo "token acquired: ${TOKEN:0:12}..."
```
Expected: token prints. (First signup on a fresh install is auto-admin.) If the endpoint shape differs in the installed version, fall back to Playwright: open `http://127.0.0.1:8080`, complete the "Get started" admin form with the same creds, then extract the token from localStorage.

- [ ] **Step 2: Create the dispatch tool via API**

```bash
python3 - <<'EOF'
import json, os, urllib.request
tok = os.environ["TOKEN"]; code = open("openwebui/dispatch_tool.py").read()
body = {"id": "agent_dispatch", "name": "Agent Dispatch",
        "content": code,
        "meta": {"description": "Discover and dispatch to communicate-bus specialists"}}
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/tools/create",
    data=json.dumps(body).encode(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
print(urllib.request.urlopen(req).status)
EOF
```
Expected: 200. Fallback: Workspace → Tools → “+” in the UI via Playwright, paste the same code, id `agent_dispatch`.

- [ ] **Step 3: Create the orchestrator model via API**

```bash
python3 - <<'EOF'
import json, os, urllib.request
tok = os.environ["TOKEN"]; system = open("openwebui/orchestrator.md").read()
body = {"id": "orchestrator", "name": "Orchestrator", "base_model_id": "qwen3:14b",
        "params": {"system": system, "temperature": 0.2, "num_ctx": 16384,
                    "function_calling": "native"},
        "meta": {"description": "Routes requests to communicate-bus specialist agents",
                  "toolIds": ["agent_dispatch"]}}
req = urllib.request.Request("http://127.0.0.1:8080/api/v1/models/create",
    data=json.dumps(body).encode(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
print(urllib.request.urlopen(req).status)
EOF
```
Expected: 200. Fallback: Workspace → Models → “+” via Playwright with identical fields.

- [ ] **Step 4: Enable the Open Terminal integration** — Admin Settings → Integrations → Open Terminal, URL `http://127.0.0.1:8000`, API key from `~/.local/state/communicate/secrets/open-terminal.key`, enabled. Try the config API first (`GET /api/v1/configs/export` with the Bearer token to find the integration's config keys, then POST the updated blob back to its import/update endpoint); if the schema isn't obvious, do it via Playwright UI (admin login with stored creds). Verify: the integration shows enabled and the terminal panel connects.

- [ ] **Step 5: Visual verification via Playwright** — log in, confirm: model `Orchestrator` exists and lists tool `Agent Dispatch`; Integrations shows Open Terminal enabled/connected. Screenshot to `~/.local/state/communicate/openwebui/verify.png`.

---

### Task 6: End-to-end tests through the orchestrator

**Files:** none (runtime verification; failures feed fixes back into Tasks 3-5).

- [ ] **Step 1: Roster question via chat-completions API**

```bash
TOKEN=... # from Task 5
curl -s http://127.0.0.1:8080/api/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"orchestrator","messages":[{"role":"user","content":"Which agents are available right now, and what is each for?"}]}'
```
Expected: reply names tidy3d-agent, gds-agent (and registry Claude agents as non-dispatchable). This proves list_agents fires from the model.

- [ ] **Step 2: Full GDS round trip**

```bash
curl -s http://127.0.0.1:8080/api/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"orchestrator","messages":[{"role":"user","content":"Have the gds agent generate a 10 micron radius ring resonator GDS and tell me the exact output file path."}]}'
```
Then verify the reported path: `ls -la <path>` — file exists, nonzero size, and appears under `~/agents/gds/out/`.

- [ ] **Step 3: tidy3d validation-only round trip (no paid solve)**

Ask via the same API: "Ask the tidy3d agent to build and validate (offline only, do NOT submit) a minimal 220nm SOI straight waveguide simulation and report validation status and estimated grid size." Expected: validation summary back; no task submitted. If the key was invalid in Task 1, expected instead: a plain statement that the key is invalid — verify the failure is graceful, not a hang.

- [ ] **Step 4: Record results** — append a `## Verification` section with actual outputs (roster excerpt, GDS path, tidy3d status) to `openwebui/README.md`; commit `--no-gpg-sign -m "Record E2E verification results"`.

---

### Task 7: Push branch, open PR, write handoff

- [ ] **Step 1: Push + PR (no AI attribution in the body)**

```bash
cd /Users/aadarwal/src/aadarwal/communicate-owui
git push -u origin openwebui-orchestrator
gh pr create --title "Open WebUI orchestrator over the communicate bus" \
  --body "One orchestrator chat (qwen3:14b local) that discovers registry agents and dispatches to tidy3d/gds codex specialists; open-terminal as its host shell. See docs/superpowers/specs/2026-08-08-openwebui-orchestrator-design.md."
```

- [ ] **Step 2: Handoff note for the operator** — write `~/agents/HANDOFF.md`: what's running (URLs, ports), where credentials live (openwebui-admin.txt, open-terminal.key, tidy3d config + validation result), how to try it (open http://127.0.0.1:8080, pick Orchestrator, example prompts), what was NOT done / deviations (unsigned commits, admin account created programmatically, communicate_path points at worktree until merge), and the kill switch (`openwebui/setup.sh stop`, `communicate down`).

## Self-Review Notes

- Spec coverage: registry entries (T1), peers (T2), dispatch tool (T3), infra+prompt (T4), headless config (T5), all five spec tests (T2/T3/T6), security items (key handling T1/T4/T5, localhost everywhere), handoff (T7). Out-of-scope items untouched.
- Known uncertainty, planned fallbacks: Open WebUI API shapes (signup/tools/models/configs) drift between versions — every API step has an explicit Playwright UI fallback with identical field values; the executor verifies each response and switches on failure.
- Type consistency: tool id `agent_dispatch`, model id `orchestrator`, thread names `peer-<name>`, valve `communicate_path` — used identically across T3/T5/T6.
