# communicate

**An agent router.** Give every AI coding agent an identity (a name) and an
address (a unix socket), then route messages between them across machines over
**Tailscale + SSH** — nothing else. Agents supported: **Claude Code** and
**Codex**. That's the whole scope, on purpose.

The core realization: Claude Code already discovers sibling sessions as
messageable *peers* by reading small sidecar files (`~/.claude/sessions/*.json`)
that map a **name → socket path**, then connecting to that socket. That's a
routing table. `communicate` extends it across devices and across agent kinds:

- A **remote Claude session** becomes a native local peer — it shows up in
  `ListAgents` and you talk to it with the built-in `SendMessage`, as if it were
  running on your own machine.
- A **Codex agent** on any device is presented behind the same socket protocol,
  so it *also* shows up as a native peer; messaging it runs `codex exec` on that
  device and streams the answer back.

No cloud relay, no account coupling, no tmux, no vendor bridge. Just
`name → socket`, mounted across an SSH tunnel.

> Sibling design note: this deliberately reuses Claude Code's own peer-messaging
> wire protocol rather than inventing one. See [`docs/MECHANISM.md`](docs/MECHANISM.md)
> for the reverse-engineered details (sidecar schema, socket discovery, the
> liveness sweep, reply routing, and the exact JSON on the wire).

## Why this works (one paragraph)

Claude Code peer discovery is: *read the sidecar JSON files, connect to the
`messagingSocketPath` each names, list whatever answers.* The trust boundary is
the filesystem — a `0700` socket dir owned by you. `communicate` moves that
boundary over a network you control: it `ssh -L`/`-R` forwards the unix sockets
so a remote agent's socket appears at a **local path**, and plants the matching
sidecar so discovery lists it. Because the forwarded socket is an ordinary local
file, every one of Claude's own guards (`abe()` local-only check, the 250 ms
liveness probe, the same-directory reply-routing rule) is satisfied. Codex has
no such socket, so we stand up a tiny adapter that speaks the same protocol and
backs it with `codex exec`.

## Requirements

- `ssh` reachability between devices (Tailscale MagicDNS names work great).
- `python3` (the wire protocol + adapter are Python).
- For the Claude bridge: run it **from inside a Claude Code session** (it needs
  your own `$CLAUDE_CODE_MESSAGING_SOCKET` as the return address).
- For Codex features: `codex` (Codex CLI ≥ 0.147) installed on the target device.

## Install

```sh
git clone git@github.com:aadarwal/communicate.git
export PATH="$PWD/communicate/bin:$PATH"   # or symlink bin/communicate onto your PATH
```

## Try it in 5 minutes

**A. One machine, no SSH — turn Codex into a Claude peer.**
Run this from inside a Claude Code session (so `SendMessage`/`ListAgents` exist):

```sh
communicate codex peer local codex-here     # stand up the peer
# In your Claude session: ListAgents  -> you'll see `codex-here`
#                         SendMessage to `codex-here`: "reply PONG and say what you are"
# The Codex CLI answers back as a cross-session message.
communicate codex unpeer local               # tear it down
```

The first inbound reply may pause on Claude Code's "held message from another
session" prompt — that's the safety gate for a bypass-mode session; approve it.

**B. Prove it end-to-end without a Claude session (uses a throwaway listener):**

```sh
scripts/test-codex-peer.sh     # stands up a peer, sends it a message, checks the codex reply
scripts/test-codex.sh local    # codex ask + resume continuity
```

**C. Two machines — talk to a Claude session on another device over Tailscale:**

```sh
communicate ls aadarshs-mac-mini-2                    # list its Claude sessions
communicate claude bridge aadarshs-mac-mini-2 newest  # bridge the newest one in
communicate agents                                    # it now shows as `claude*`
# In your Claude session: SendMessage to it by name — replies come back.
communicate claude unbridge aadarshs-mac-mini-2
```

**D. The router + triggers (wake).** A message already *is* a wake (an inbound
peer message resumes an idle/finished session), so `wake` isn't about a clock —
it's about what *triggers* the message. A timer is one trigger; a new PR is another:

```sh
communicate agents                            # routing table: every reachable agent
communicate route codex-here "status?"        # route to any agent by name

communicate wake codex-here --every 300       # timer: nudge every 5 min (keep-working / poll)
communicate wake codex-here --on-pr you/repo  # event: notify an agent when the repo gets a NEW PR
communicate wake ls ; communicate wake stop codex-here
```

So "when the repo gets a PR, a Codex reviewer wakes and looks at it" is one line:
`communicate wake codex-reviewer --on-pr you/repo`.

If something looks off: `communicate status` shows active bridges/peers, and
`communicate down` tears everything down cleanly.

## Usage

```
communicate link    <device>                 verify the ssh/Tailscale substrate
communicate ls      [device]                 list agents on a device
communicate status                           show active bridges / codex peers
communicate down                             tear everything down

communicate claude bridge   <device> [name|pid|newest]   # remote Claude -> native peer
communicate claude unbridge <device|all>

communicate codex  ask   <device> [--dir D] [--thread N] [--new] [--auto] <message>
communicate codex  peer  <device> [name]     # remote Codex -> native Claude peer
communicate codex  unpeer <device|all>

communicate send <peer-name|socket> [--as NAME] <message>   # raw inject into a peer socket
```

`<device>` is `local`, a Tailscale hostname (e.g. `aadarshs-mac-mini-2`), or
`user@host`.

### Talk to a Claude session on another machine

```sh
communicate ls aadarshs-mac-mini-2                 # see its Claude sessions
communicate claude bridge aadarshs-mac-mini-2 say-hi-to-me
# ...now `say-hi-to-me` appears in ListAgents; SendMessage to it normally.
communicate claude unbridge aadarshs-mac-mini-2
```

Works **across accounts**: the bridge is filesystem+socket based, so it connects
sessions logged into different Anthropic accounts — something the account-scoped
cloud bridge cannot do.

### Talk to a Codex agent anywhere

```sh
communicate codex ask aadarshs-mac-studio "summarize the failing test in ./api"
communicate codex peer aadarshs-mac-studio codex-studio   # present it as a peer
# ...now `codex-studio` is in ListAgents; SendMessage runs codex exec over there.
```

## homi — durable identity + store-and-forward (v0.3)

Everything above is rendezvous: both ends must be alive at the same moment, and
a "name" is whatever a sidecar file says right now. **homi** (named for the
HOMI-engine lineage; its role is the device's postmaster) is the per-device
daemon that fixes both:

- `communicate homi claim <name>` gives a name a **stable address that
  survives the process behind it**: an always-answerable socket, a sweep-proof
  sidecar (the name stays in `ListAgents`), and a durable mailbox
  (`$COMM_STATE/homi/mail/<name>/inbox.jsonl`) that stores every inbound
  message before anything else is attempted.
- **store→wake.** While a real session owns the name (`/rename` — the sidecar
  mirrors it), homi steps aside and drains held mail into the session as
  protocol turns (a message is a wake). When the session dies, homi takes the
  name back and holds mail durably. The gap between "received" and "acted on"
  is homi's job.
- **Measured liveness.** `homi status` probes sockets — its own included — and
  labels every route `probed`/`reported`. A socket file is not a listener.
- **Device links.** `homi link <dev> --addr user@host` dials **outbound-only**
  (`ssh -N -L`, BatchMode, `StreamLocalBindUnlink`) toward the peer homi's
  per-device inbound socket, so inbound and outbound fail independently.
  Envelopes are acked, queued under `out/<dev>/`, retried with backoff, and
  deduped by `msg_id`. Address remote agents as `<name>@<device>`; remote
  senders appear locally as proxy peers you can reply to by bare name, and a
  reply for an unclaimed name auto-creates its mailbox — local mail never
  bounces.
- **Persistence.** `homi install` puts it under launchd (`KeepAlive` +
  `RunAtLoad`; systemd --user on linux). `communicate down` deliberately does
  **not** stop homi — it is infrastructure; `homi uninstall` removes it.
- **Rename-sync.** The identity name *is* the session's `/rename` name.
  `homi adopt <name> --pane <id>` drives a live rename (via anu's `pane`);
  `homi retitle <transcript|uuid> <name>` retitles a dormant transcript.

```sh
communicate homi start                # or: homi install  (survives reboots)
communicate homi claim gds-agent      # durable address; appears in ListAgents
communicate homi send gds-agent "…"   # stored durably, delivered as a turn when live
communicate homi inbox gds-agent      # the mailbox is a plain JSONL file
communicate homi link mini-1 --addr aadarshs-mac-mini-1   # then: send <name>@mini-1
communicate homi status --json        # routes, measured liveness, queues
```

Tests: `scripts/test-homi-core.sh` (lifecycle → identities → store→wake →
probes), `scripts/test-homi-link.sh` (two homis: envelopes/acks/retry/dedup/
proxy/reply), `scripts/test-homi-persist.sh` (launchd KeepAlive; manual).

## Safety model

- Transport is strictly SSH over a network you own. A forwarded socket is only
  as reachable as the SSH login that carries it.
- The Codex adapter defaults to Codex's **read-only** sandbox — a conversational
  agent that reads and answers but does not modify. Opt into edits with `--auto`.
- Bridging widens *who can message your agent* from "you, locally" to "whoever
  can reach that socket." Only bridge to ends you trust. Claude Code's own
  inbound gate still applies: a bypass-mode session will **hold** a peer message
  for human approval before delivering it.
- `communicate down` removes every planted sidecar, kills every tunnel/daemon,
  and unlinks every forwarded socket. Nothing is left behind.

## Layout

```
bin/communicate     CLI dispatcher
lib/common.sh       device/ssh/logging helpers
lib/claude.sh       Tier 1: bridge a remote Claude session as a native peer
lib/codex.sh        Tier 2: ask a Codex agent over ssh (codex exec, with continuity)
lib/peer.sh         Tier 3: present Codex as a native peer; raw peer sender
lib/router.sh       the agent-router: name -> socket table, whereis, route
lib/wake.sh         heartbeat loop over the router
lib/cc_peer.py      the cc-socks wire protocol (serve / send / recv)
lib/homi.py         the homi daemon: durable identities, mailboxes, store→wake, links
lib/homi.sh         communicate homi verbs (start/claim/send/link/install/adopt/…)
docs/MECHANISM.md   how Claude Code peer messaging actually works
scripts/            end-to-end tests (codex-peer, codex, claude-bridge)
```

## Status

Tier 1 (Claude↔Claude bridge) and Tier 3 (Codex-as-peer) are verified
end-to-end; Tier 2 (`codex ask`) including `resume` continuity is verified
locally. See `scripts/` for the tests.

homi v1 is verified: 44/44 core, 20/20 link, 6/6 launchd-persistence checks,
plus a live proof on a real machine — a claimed identity appeared in
`ListAgents`, a real `SendMessage` landed in its durable mailbox with the same
`msg_id`, and a CLI `homi send` to a live session's name was delivered into
that session as an attributed cross-session turn. Cross-device homi↔homi over
real ssh awaits a second device with a valid tailnet node key.
