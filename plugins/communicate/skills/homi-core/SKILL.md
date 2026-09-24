---
name: homi-core
description: Create or use durable HOMI identities and mailboxes, spawn or bind agents, send and receive durable messages, and explicitly control terminal seats. Use for persistent agents, dormant mail, agent creation, or terminal compute; native session messaging and registered buses remain separate supported paths.
---

# HOMI identities, mail and execution

HOMI keeps existing mechanisms behind three explicit command paths. Select the
path that owns the target; a matching label in another path is not a substitute.

| Target | Commands | Meaning |
| --- | --- | --- |
| Durable identity | `homi agents`, `claim`, `send`, `ask`, `inbox` | A saved address/mailbox with an optional current execution. |
| Existing native session | `homi native agents`, `route`, `ask`, `codex queue` | An exact supported Claude Code socket or Codex queue target; no pane required. |
| Registered bus agent | `homi bus status`, `register`, `agents`, `send`, `reply` | Exact registrations under the selected hub's membership rules. |
| Terminal seat | `homi seat spawn`, `read`, `send`, `state`, `bind` | Explicit terminal control on the configured tmux server or granted remote link. |

The compatible forms are `communicate homi`, `communicate`, and `communicate
bus`. The plugin identifier remains `communicate@communicate` during migration.
MCP tools `homi_*` expose durable identities/seats; existing `bus_*` and native
tools retain their meanings. `homi profile` is optional terminal/mesh setup;
do not alter shell or desktop settings to satisfy a messaging request.

## Start with the intended identity

Inspect `homi status --json` before assuming the local daemon is available.
`homi start` starts it when requested; neither action joins a remote bus.
`homi agents --json` lists durable identities and their observed bindings.

```sh
homi claim reviewer
homi spawn worker --cli claude --cwd /path/to/project
homi spawn checker --cli codex --cwd /path/to/project
```

Claim creates an address without launching a model. Spawn creates a claim and
an execution using the configured provider; authentication remains the user's.
Never spawn a replacement to satisfy a request to register an existing session.
Do not infer the current session from a newest transcript or shared cwd.

For a live Claude composer in the caller's tmux server, `homi adopt NAME --pane
%ID` submits `/rename` using the existing seat driver and confirms that pane's
session sidecar. Use `--socket PATH` for another exact server. This renames the
native session; claiming a durable address is a separate `homi claim NAME`.
`homi retitle TRANSCRIPT_OR_UUID NAME` changes a dormant Claude transcript's
title for resume. A restart is process supervision, not proof of restored
conversation state. Surface that distinction when reporting recovery.

## Send and receive

```sh
homi send reviewer --from worker -- "Review this result"
homi ask reviewer --from worker --timeout 90 --json -- "What should change?"
homi inbox reviewer
homi wait reviewer --timeout 60 --json
homi reply EXACT_RETURN_TOKEN --from reviewer -- "Here is my answer"
```

Use a claimed sender so replies have a durable destination. Preserve the exact
return token. Existing ask also supports a best-effort natural-reply fallback;
do not describe such a match as explicitly correlated. Timeout means the wait
ended, not that the request was never stored or will never receive a reply.

Stored, submitted, and replied are different evidence. Mail can persist while
its execution is absent. A socket write or Codex queue receipt does not prove
the model consumed the turn. Reading terminal output does not prove a reply.
Follow the adapter's reported state, including held, unavailable and unknown.

## Execution control is explicit

```sh
homi seat spawn 'bash --norc --noprofile' --cwd /path/to/project
homi seat read %ID
homi seat send %ID 'a command requested by the user'
homi seat state %ID
homi seat bind %ID reviewer
```

Panes are scoped to their device and tmux server lifetime. A pane ID is not a
globally durable identity, an authenticated principal, or a sandbox boundary.
Ordinary shell input can execute commands. Do not deliver agent mail into a
shell just because native messaging is unavailable. Mail relay requires the
existing explicit `seat bind --relay` opt-in and agent-surface checks.

`homi seat interrupt` sends Escape; `seat kill` terminates a pane. `homi release`
releases a durable claim, and `homi stop` stops the daemon. These are distinct
from deleting saved mail/state. Do not use them interchangeably.

## Other devices and the bus

Existing durable device links use `homi link DEVICE --addr USER@HOST`; paired
devices use `homi pair USER@HOST`. Provisioning and SSH access are intentional
operations, not consequences of discovering a hostname. Remote seat control is
separately granted with the existing `--allow-seats` option.

Use `communicate-bus` for local, self-hosted or invited HTTPS buses, graph and
human inbox. A bus registration is not automatically a durable HOMI claim.
Bus replies must retain their hub/registration/message identity; durable HOMI
replies must retain their return token. Remote enrollment, publication and
compute-control permission remain distinct.
