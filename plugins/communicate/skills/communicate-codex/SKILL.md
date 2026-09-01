---
name: communicate-codex
description: Talking to Codex agents and sessions — three lanes. Use when asked to message, ask, queue a turn into, or stand up a Codex agent (OpenAI Codex CLI/app), including reaching an existing named Codex session, getting a synchronous headless Codex answer, or presenting Codex as a Claude peer.
---

# communicate-codex — the three Codex lanes

Codex has no peer socket, so communicate offers three honest primitives. Choose
by where you want the conversation to live.

## Lane 1 — `codex queue`: into an EXISTING session (async)

```sh
communicate codex queue <device> <session-name|uuid> "<message>"
```

- Requires Codex CLI ≥ 0.151 on the target device.
- Addresses the NATIVE session name/UUID from `~/.codex/session_index.jsonl`
  (exact name; the user sees your message and the reply in their app/TUI).
- Success means **enqueued**, not answered. A live session consumes it in
  seconds; a dormant one on resume. It never creates a lookalike session.
- The reply stays in THAT session. To verify delivery/reply, read its rollout:
  `~/.codex/sessions/YYYY/MM/DD/rollout-*<thread-id>.jsonl` (look for the
  trailing `agent_message` / `task_complete` records).

## Lane 2 — `codex ask`: fresh headless answer (sync)

```sh
communicate codex ask <device> [--dir D] [--thread NAME] [--new] [--auto] [--model M] "<question>"
```

- Runs `codex exec --json` (message passed on stdin — quotes/newlines safe),
  prints the final answer on stdout.
- **Continuity**: the thread id is cached per (device, thread-label); the next
  ask with the same label resumes the same conversation. `--new` starts over;
  `communicate codex forget <device|all>` clears the cache.
- **Sandbox**: read-only by default (converses, reads, never edits). `--auto`
  opts into workspace-write. `--dir` sets the working directory.

## Lane 3 — `codex peer`: Codex as a ListAgents peer (async, replies back)

```sh
communicate codex peer <device> [name]     # e.g. codex-studio
communicate codex unpeer <device|all>
```

Stands up a small adapter: socket + sidecar, so the name appears in
`ListAgents`/`communicate agents` like any Claude peer. Each inbound message
runs a `codex ask` behind the scenes and the answer is written BACK to the
sender's socket. Note: the peer owns its own headless thread — it is not
attached to any app session (that's Lane 1's job).

## Getting a reply back (closing the queue lane's loop)

`queue` alone is one-way. Two ways to close the loop:

```sh
communicate codex queue <dev> <name> --coach "<msg>"   # teach them how to reply to YOUR session
communicate ask <name> "<question>"                    # sync: coached delivery + blocking reply wait
```

`--coach` appends a `[reply-to …]` block with your reply address; `ask` does
that AND waits — it finds a local Codex session by its native thread name
automatically. The receiving Codex agent replies by running the
`communicate send` command from the block (communicate is on PATH via the
plugin, or at ~/.local/share/communicate/current/vendor/bin/communicate).

## Utilities

```sh
communicate codex probe <device>    # is codex installed there? prints version
```

Device is `local`, a Tailscale hostname, or `user@host` — same as everywhere
in communicate.
