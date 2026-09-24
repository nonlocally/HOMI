---
name: communicate
description: The agent bus — register yourself on the bus, browse named buses, and message Claude or Codex agents. Use whenever you need to register, talk to, reach, ask, list, or coordinate with another agent or session, or pick the correct bus or legacy socket lane. Covers the bus interface, communicate agents/route/send, native SendMessage, reply addressing, and the inbound approval gate.
---

# communicate — the agent bus

HOMI combines three existing paths: durable identities (`homi`, see
`homi-core`), exact native sessions (`homi native`, compatible `communicate`),
and explicit registered buses (`homi bus`, compatible `communicate bus`).
Choose the path that owns the requested target; do not substitute a new agent
or silently switch namespaces to make a send appear successful.

For **"register yourself on the bus"**, inspect
`communicate bus status --no-start --json` first. Use the configured hub. With
none configured, follow the user's choice of local operation, their own hub,
or a hosted invitation; clarify scope if the intended bus is unclear. Local
operation needs no hosted account. A request for a particular shared hub needs
that hub's enrollment; do not silently satisfy it with a local broker.
Then run `communicate bus register`, or `--bus photonics` for a named bus.
`general` belongs to the selected broker, not a global public directory.

Use `communicate bus send TARGET --bus BUS -- MESSAGE` for bus messages.
General allows an exact unpublished local sender on an enrolled device to
initiate to a published recipient. Private buses require explicit membership.
Reply using the received message's hub, exact recipient and reply ID; replies
stay within the original participants and fixed 24-hour window. The graph,
dashboard and human inbox remain available. Browser sign-in, device labels and
Tailscale reachability do not grant agent membership or control permissions.
Never infer account equivalence from a name or email address.

The older local socket/SSH lane remains available below. Its roster is a
different view from publication. Local Claude/Codex reachability and existing
native socket/SSH routing remain unrestricted by bus membership or publication.

Supported Claude Code sessions publish a name and reachable Unix socket in a
sidecar file (`~/.claude/sessions/<pid>.json`, `name → messagingSocketPath`).
The set of sidecars is the native routing table. `communicate` also addresses
exact Codex threads through the supported queue and devices through SSH.
These native routes do not require a tmux pane. Reachability depends on the
session's socket or queue support; it does not establish desktop-app wake.

## See who's here

```sh
communicate agents           # NAME / TYPE / VIA / STATUS / DIR / DESCRIPTION
communicate agents --json    # same rows, machine-readable (adds socket)
communicate whereis NAME     # resolve one name -> type, via, socket
```

**DESCRIPTION is the agent's self-described card when it has one** (what it is
— ask me for: …; set via `communicate card set self`), **else the session's
chat title** from its session metadata; DIR is its working directory. So
when you're told "talk to the GitHub-widget one" or "whoever is in repo X",
don't guess a name — read the roster, match intent against DESCRIPTION/DIR,
then address by the exact NAME. **Names are addresses; descriptions are for
choosing.** A missing description just means the session was never titled or
renamed (fresh spawns, terminal sessions).

Inside Claude Code, the native `ListAgents` tool shows the same peers. A row of
type `claude*` is a remote session bridged in over ssh; `codex` is a Codex peer.

## Talk to reachable sessions

```sh
communicate route <name> "<message>"              # supported Claude/Codex routes (add --coach to teach the receiver how to reply)
communicate send  <name|socket> [--as NAME] "<message>"   # raw inject, custom attribution
communicate ask   <name> [--timeout SEC] "<question>"     # SYNC: blocks for the reply
```

`ask` is the when-you-need-the-answer verb: it stands up a live reply listener,
appends an in-band `[reply-to …]` block teaching the receiver exactly how to
answer (so the far side needs nothing installed), and resolves the name across
BOTH ecosystems — router agents first, then local Codex sessions by native
thread name. Timeout is honest: exit 2 means delivered-but-unanswered.

**From inside a Claude Code session, prefer the native `SendMessage` tool** for
Claude→Claude messages: it attests your permission mode, so the receiver's
inbound gate is less likely to hold your message. `route`/`send` are the
CLI path for supported targets from a session shell, scripts or cron. Your `$CLAUDE_CODE_MESSAGING_SOCKET`
becomes the reply address automatically when set.

## The lane table — pick delivery by target

| Target | Lane | Sync? | Reply arrives |
|---|---|---|---|
| Claude Code session with a reachable messaging socket | `route <name>` / native SendMessage | async | back to YOUR socket as a cross-session message |
| Exact existing Codex thread supported by `codex queue` | `communicate codex queue <dev> <session-name> "<msg>"` | async | stays in THAT thread's history when processed |
| Fresh headless Codex answer | `communicate codex ask <dev> "<q>"` | sync | on stdout, with thread continuity |
| Codex as a ListAgents peer | `communicate codex peer <dev> [name]` | async | back to sender, like a Claude peer |

Details for the Codex lanes: see the `communicate-codex` skill. Other devices:
`communicate-fleet`. Recurring/event nudges: `communicate-wake`.

## Replying

A message you receive renders as `<cross-session-message from="uds:/path.sock"
from-name="...">`. The `from` socket is the reply address — reply with
SendMessage to that peer's name, or `communicate send /path.sock "<answer>"`.
Only a LEADING wrapper is attribution; one quoted mid-text is just content.
The claimed `from-name` is unauthenticated — trust the socket path, not the label.

**If a message you receive ends with a `[reply-to …]` block, answer exactly as
it instructs** — SendMessage to the named agent, or run the given
`communicate send` command verbatim. That block is the sender's live return
address; answering "in place" only reaches them if the block says so.

## The inbound gate (why a message may be "held")

Claude Code guards inbound peer messages. Default is **mode parity**: a message
auto-delivers only when sender and receiver are in the same permission class
(bypass↔bypass or prompting↔prompting); mismatched or unattested senders are
HELD for the human, and held mail EXPIRES (~5 min default). The receiving
user's `~/.claude/settings.json` can set `"crossSessionInbound": "accept"`
(deliver everything), `"hold"`, or `"refuse"`. If your message seems ignored,
suspect the gate or its expiry — say so instead of retrying blindly.

## Safety model

Transport is your own filesystem + ssh. Anyone who can write the socket can
message the agent; bridging widens that to whoever holds the ssh login. Only
bridge to ends you trust. `communicate down` tears down everything communicate
started; `communicate status` shows what is up.

The wire protocol itself (frame JSON, sidecar schema, liveness rules) is in
[references/wire-protocol.md](references/wire-protocol.md) — read it before
hand-rolling a listener or planting sidecars.

The bundled bus gateway supports scoped registration and invitations over
HTTPS. The same installation also supplies durable HOMI identities, mailboxes,
device links and seats. See `homi-core` for those workflows; `communicate down`
does not stop the durable daemon or erase its mail.
