---
name: communicate-identity
description: Names, renaming, and joining the agent bus. Use when asked to rename yourself or another agent/session, name a session so it can be messaged, make an agent addressable/discoverable, or explain how an agent becomes part of the bus. Covers /rename as the namespace, sidecar mechanics, dormant-transcript retitling, and the join-the-bus contract.
---

# communicate-identity — names and joining the bus

## A name is a sidecar entry

Claude Code writes `~/.claude/sessions/<pid>.json` with `name` and
`messagingSocketPath`. That file IS the identity: whatever it says is what
`ListAgents`, `communicate agents`, and `route` resolve. There is no separate
registry — **the session's `/rename` name is the whole namespace**.

## Rename yourself

Inside a Claude Code session: run `/rename <name>`. The sidecar updates and
every router sees the new name immediately. (The roster's DESCRIPTION column already
shows each session's chat title automatically — rename when you want the
ADDRESS itself to carry the meaning, e.g. so `route <semantic-name>` works.) Pick names matching
`[a-z0-9][a-z0-9._-]*` — short, stable, purpose-shaped (`reviewer-api`,
`say-hi-to-me-2`).

## Rename another session

A socket-delivered "/rename" is just text to the model — it does NOT execute.
So renaming someone else means driving their composer:

- **Live session you can see** (a terminal/tmux pane): type or send `/rename
  <name>` into its UI.
- **Live session you can message**: ask it — route it a message requesting it
  run `/rename <name>` (it decides; the gate may hold your request).
- **Dormant transcript**: append a `custom-title` record to the transcript
  JSONL (`{"type":"custom-title","customTitle":"<name>","sessionId":"<uuid>"}`)
  — the name is carried when it resumes. The full repo automates this as
  `homi retitle` (not bundled here).

## Codex names

Codex sessions carry a native thread name in `~/.codex/session_index.jsonl`
(`thread_name → thread id`), set from the Codex app/TUI. `communicate codex
queue` addresses exactly that name. Renaming a Codex session happens in its
own UI; latest name wins in the index.

## Joining the bus — the whole contract

An agent (any process — not just Claude) is ON the bus when three things hold:

1. **A sidecar** in the sessions dir whose filename is NUMERIC (`<pid>.json` —
   discovery lists only pid-shaped filenames) and whose `pid` field is a live
   pid (the sweep unlinks dead ones).
2. **A listening socket** at the sidecar's `messagingSocketPath` that accepts a
   connection within ~250 ms (the liveness probe).
3. **Speaking the frame**: newline-delimited JSON user messages (see the
   `communicate` skill's references/wire-protocol.md).

Meet those and you appear in `ListAgents` and are routable by name. This is how
communicate presents Codex as a peer, and how any custom adapter can join.

## Collisions

Names may collide. Resolution is deterministic: probe-live socket first, then
interactive kind, then newest `startedAt`. If you must disambiguate, address
the socket path directly with `communicate send /tmp/cc-socks/<pid>.sock ...`.
