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

**npx (Claude Code + Codex plugin, CLI, MCP):**

```sh
npx -y @aadarwal/communicate setup     # both ecosystems; --claude / --codex to narrow
```

Stabilizes the payload to `~/.local/share/communicate/`, adds two keys to
`~/.claude/settings.json` (backup written first), and registers the Codex
plugin via the `codex` CLI. New Claude sessions and Codex threads then have the
skills, the `/agents` command, `communicate` on PATH, and the MCP tools.
`npx -y @aadarwal/communicate doctor` verifies; `setup --uninstall` reverses.
The package ships the communicate layer only (no homi plane).

**From source (repo-havers — full CLI including homi):**

```sh
git clone git@github.com:aadarwal/communicate.git
export PATH="$PWD/communicate/bin:$PATH"   # or symlink bin/communicate onto your PATH
communicate setup-repo                      # register THIS checkout as the Claude+Codex plugin
```

`setup-repo` needs no npm and makes no copies: it points both ecosystems at the
checkout itself, so `git pull` IS the upgrade. Reverse with
`communicate setup-repo --uninstall`. (The MCP face rides the published npm
package; until it is published, use the npx-mode setup for MCP.)

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

communicate codex  queue <device> <session-name|uuid> <message>  # persistent async enqueue
communicate codex  ask   <device> [--dir D] [--thread N] [--new] [--auto] <message>
communicate codex  peer  <device> [name]     # remote Codex -> native Claude peer
communicate codex  unpeer <device|all>

communicate send <peer-name|socket> [--as NAME] <message>   # raw inject into a peer socket
communicate ask  <name> [--timeout SEC] <message>           # sync ask: coached delivery + blocking reply
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
# Enqueue a turn for an existing Codex session. Its reply stays there.
communicate codex queue local say-hi-to-me "Say hello and report your status"

# Run a synchronous headless request/response agent, with remembered continuity.
communicate codex ask aadarshs-mac-studio "summarize the failing test in ./api"
communicate codex peer aadarshs-mac-studio codex-studio   # present it as a peer
# ...now `codex-studio` is in ListAgents; SendMessage runs codex exec over there.
```

`codex queue` is the lane for an existing Codex session (Codex CLI ≥ 0.151). It
uses Codex's own persistent cross-process local queue and addresses the native
session UUID or exact session name; it never creates a lookalike session when
the target is missing. A successful command means **enqueued**, not answered:
a live session consumes it immediately, a dormant one on resume. The reply
stays in that session's UI/history. `codex ask` remains the synchronous lane
and owns its own stored `codex exec` threads.

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
  `homi adopt <name> --pane <id>` drives a live rename; `homi retitle
  <transcript|uuid> <name>` retitles a dormant transcript; `homi spawn` adopts
  automatically through its own seat.

### v2 — two planes, and the fabric creates/moves/contains agents

**Messages** (durable, acked, identity-addressed — the default) and **seats**
(interactive terminal surfaces — the explicit escape hatch for what you cannot
mailbox: cluster shells, REPLs, TUIs).

- **ask / reply / group / notify.** `homi ask <name> "q"` blocks for the
  correlated reply (return token `asker@device~corr`; explicit `homi reply
  <token> "a"`, or a plain message back resolves it best-effort). `group`
  fans one message; `notify` is the durable human-summon lane
  (`HOMI_NOTIFY_CMD` hook). `homi wait <name>` long-polls for new mail.
- **Seats.** `homi seat spawn|send|read|state|wait|respond|bind|kill` — a
  clean-room tmux driver: classifier (dead>approval>busy>booting>idle, spinner
  animation + jittered sampling), deliver-and-verify send (literal stage,
  separate retried Enter, composer-clear check, exit-2 unconfirmed), secret
  redaction on read, fail-closed respond (parses only the real menu block;
  never guesses). Seats live on a dedicated tmux server (`tmux -L homi`).
- **Cross-device seats.** Seat ops ride links as synchronous envelopes whose
  ack carries the result. Opt-in per link: `homi link <dev> --allow-seats`
  (upgrade in place; `--revoke-seats`). `seat spawn --device <dev>` returns
  `<dev>:%N`; every seat verb accepts that form.
- **spawn / fan / consult.** `homi spawn <name> --cli claude` = claim + seat +
  bind + adopt in one verb (workers launch with `crossSessionInbound: accept`
  scoped to their own process — mailbox-driven by construction). `fan` makes N
  + a group; `consult` spawns-or-reuses a private peer and asks it.
- **move.** `homi move <name> <device>` relocates the agent-being — transcript
  (rsync, `$HOME`-translated), mailbox (recomposing merge that never re-delivers
  acted-on mail and never loses an undelivered line), and the claim — with the
  **address alive throughout**: depart atomically flips claim→proxy so mid-move
  mail follows over the link. `--fork --as <new>` copies instead; `--spawn`
  resumes it in a seat on arrival.
- **Boxed agents.** `homi claim <name> --boxed` adopts a sandboxed VM
  (apple/container) as a peer: the container publishes its socket pair into
  homi's paths (`--publish-socket`), homi probes it for measured liveness,
  delivers mail through it, and drains the box's outbox with the boxed
  identity as attribution. `bin/homi-boxed-init` is the in-box shim. Verified
  live under `--network none`: **the box's only egress is homi mail.**
- **Fleets (cross-operator).** `homi federate invite/accept` exchanges
  base64 cards (fleet, addr, ed25519 fleet-key fingerprint, inbound socket
  path) and installs the forward-only key line
  (`restrict,port-forwarding,command="/usr/bin/false"` + marker). Fleet links
  are deny-by-default: `homi grant <fleet> <name>` exposes one identity;
  ungranted/unclaimed → one ambiguous error (no enumeration, no auto-claim);
  foreign senders proxy fleet-qualified (`orchestrator@alice`); sending to a
  fleet auto-grants your return path; ask tokens rewrite at the boundary; the
  control socket demands `control.token` once a fleet link exists (a
  forward-only peer can dial sockets but can never read files).
- **MCP faces.** `packages/homi` (`@aadarwal/homi`): an npx-installable MCP
  server projecting ~18 tools, each one JSON line to the daemon — zero fabric
  logic in Node ("two faces, one kernel"). `npx homi setup` installs the
  vendored stdlib daemon + launchd and prints `claude mcp add` / `codex mcp
  add` lines.

```sh
communicate homi start                # or: homi install  (survives reboots)
communicate homi claim gds-agent      # durable address; appears in ListAgents
communicate homi send gds-agent "…"   # stored durably, delivered as a turn when live
communicate homi ask helper "2+2?" --from me --timeout 60   # blocking correlated ask
communicate homi spawn worker --cli claude --cwd ~/proj     # claim+seat+bind+adopt
communicate homi link mini-1 --addr aadarshs-mac-mini-1 --allow-seats
communicate homi seat spawn 'bash' --device mini-1          # a seat on another device
communicate homi move worker mini-1                         # relocate the agent-being
communicate homi status --json        # routes, measured liveness, queues
```

### v3 — the user layer: @handles

The fabric modelled agents, devices, and fleets — but not the **person**: the
fleet name was an unpersisted echo of `$USER` computed inside the daemon, and
nothing said who owns an agent. v3 completes `fleet` into a claimed identity.

- **`homi init` — the claim ceremony.** Claim a handle once (`@aadarwal`);
  it lands in `user.json` (the only writer is this ceremony — a missing file
  is loud degraded mode, never a silently regenerated identity) and is
  re-derived into every card, status, agents listing, and MCP whoami.
  Precedence: `HOMI_FLEET` env (test hook) > `user.json` > OS username.
  `npx` first-run prompts for it interactively.
- **No passwords — possession of a key is the login.** Your ed25519 key's
  fingerprint is the identity; the handle is the human name bound to it by a
  signature. `homi user` shows who this fabric belongs to.
- **`homi pair <user@host>` — your own second device, one-sided.** Probes ssh
  (prints the exact `ssh-copy-id` fix), stages/upgrades the far kernel from
  this install's own files, reads BOTH device names from the daemons (never
  typed — a mistyped petname queues mail forever), links both directions,
  syncs the handle, and reports a **measured** round trip each way. The new
  `ping` envelope kind is stateless; `homi link <dev> --check` measures any
  link on demand.
- **`homi connect` — another person, by handle, on signed cards.**
  `connect --invite` prints one signed code (card v2: `ssh-keygen -Y` over
  canonical bytes). The acceptor runs `homi connect @you --code '…'`:
  signature verified, the typed handle cross-checked against the card, the
  fingerprint **recomputed from the card's key** (its claimed fingerprint
  string is refused on mismatch), one out-of-band confirm — then the
  forward-only key line and a deny-by-default fleet link whose petname IS the
  peer's handle, so both sides' socket names agree by construction. First
  contact honestly reports "transport pending" and prints the counter-code
  that closes the loop. Reach their agents as `<agent>@<handle>` once they
  `grant` you; a re-key of a bound handle is always refused, never silent.
- **Grants harden with it**: the outbound return-path auto-grant is now TTL'd
  (`HOMI_AUTOGRANT_TTL`, default 7 days, refreshed per send) instead of
  permanent; grants pin the key fingerprint they were made to; foreign proxy
  minting is capped per user (`HOMI_PROXY_CAP`). Trust verbs (`init`, `pair`,
  `connect`, `grant`, `federate`, `link`) remain human-only CLI — never MCP.

- **talk — converse with agents from any device** (a surface on the served
  board, not a separate verb). Every agent row is a link: `/talk/<name>`
  opens a phone-friendly chat over the fabric's own planes — your sends go
  out as `@<handle>` (honest attribution), replies land in YOUR durable
  mailbox, and store→wake means you can message a sleeping agent at night
  and read its answer in the morning. The write boundary is tailnet
  reachability + a Host-header allowlist (DNS-rebinding defense); a
  JSON-only, custom-header (`X-Homi`) requirement plus a per-serve token
  injected into the page defend against cross-origin forgery; behind
  `tailscale serve` the `Tailscale-User-Login` identity is logged per send.
  The conversation is fabric state, not a web session. For the phone:
  `communicate homi board --serve --ts` binds loopback and prints the one
  `tailscale serve` command that gives a real HTTPS origin (iOS secure
  context, installable PWA).

  The talk page is **one merged timeline** (`/api/timeline/<target>`):
  the agent's session is the spine — its prose, tool calls as one-line
  receipts, compactions as dividers, straight from the transcript on disk
  (files-as-API; resolution mirrors delivery: registry scan, probe-parity
  on collision, transcript by session-id) — and the MAIL thread is
  authoritative for your correspondence, interleaved by timestamp. The
  transcript's own copies of that correspondence (your delivered turns,
  the agent's send-replies to you) are dropped unconditionally, which
  kills every duplication case including queued-then-delivered-on-wake.
  Your messages show their honest routing (`delivered live` /
  `queued — delivers on wake`); a sleeping agent shows its LAST session
  under an "asleep" banner; a remote (`name@device`) or never-run agent
  degrades to messages-only. The ✉ toggle filters to correspondence.
  Thinking blocks and machine plumbing never render; foreign senders
  render under their own names, never as "you". At most 1000 transcript
  turns held (below the cap says `truncated`, never pretends);
  `/api/session/<name>` and `/api/conv/<target>` remain as the raw
  planes. **Deployment caveat, stated plainly:** transcripts are the
  most sensitive read on the board; the board assumes a single-operator
  tailnet. Before this device is ever shared into someone else's tailnet,
  move the board behind `tailscale serve` with identity pinning or ACL
  the port — a shared-in peer who can reach the port can read pages, and
  the page carries the token.

- **`homi board` — the fabric's front end.** One self-contained page:
  every device's roster with measured liveness, links with live round-trip
  numbers, queue depths, and the People section (granted names, pinned keys,
  auto return-paths with their expiry). Works from `file://`; `--serve`
  binds the device's Tailscale IP (tailnet-only) and the page upgrades
  itself to live polling. `homi board --open` renders + opens;
  `homi board --serve [port]` serves (default 7421, `HOMI_BOARD_PORT`);
  `--no-remote` skips far-roster ssh fetches; output dir via
  `HOMI_BOARD_DIR` (default `~/.local/state/communicate/board/`).

```sh
communicate homi init --handle aadarwal        # claim yourself, once
communicate homi pair aadarwal@aadarshs-mac-air-2   # enroll YOUR laptop (measured)
communicate homi connect --invite              # hand the code to a collaborator
communicate homi connect @peer --code 'homi1…' # accept theirs (verified, pinned)
communicate homi grant peer gds-agent          # share exactly one agent
communicate homi ask librarian@peer "REQUEST fdtd sweep…"   # cross-user ask
```

Tests (315 checks, all green): `test-homi-core.sh` 49 · `test-homi-ask.sh` 10 ·
`test-homi-link.sh` 24 · `test-homi-seat.sh` 12 · `test-homi-seat-link.sh` 9 ·
`test-homi-spawn.sh` 18 · `test-homi-mcp.sh` 10 · `test-homi-fleet.sh` 22 ·
`test-homi-move.sh` 21 · `test-homi-workspace.sh` 18 · `test-homi-card.sh` 6 ·
`test-homi-user.sh` 33 · `test-homi-pair.sh` 23 · `test-homi-connect.sh` 28 ·
`test-homi-npx.sh` 15 · unit 9 · plist-unit 5 (+ `test-homi-boxed.sh` 14 and
`test-homi-persist.sh` 6, container/launchd).

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

homi v1 is verified: 46/46 core, 23/23 link, 6/6 launchd-persistence checks,
plus two live proofs on real machines. Single-device: a claimed identity
appeared in `ListAgents`, a real `SendMessage` landed in its durable mailbox
with the same `msg_id`, and a CLI `homi send` to a live session's name was
delivered into that session as an attributed cross-session turn.
Cross-device (2026-08-15, aadarshs-mac-mini-2 ↔ aadarshs-mac-air-2, real
`ssh -N -L` links both ways): `homi send air-echo@aadarshs-mac-air-2` arrived
in the far durable inbox with arrival-line attribution (`via`), the reply
addressed `communicate@aadarshs-mac-mini-2` came back over the far side's own
outbound link, and store→wake delivered it into the live originating session
as a turn; both outbound queues drained to zero (acked).

homi v2 is verified — 161/161 across 10 suites, plus live proofs
(2026-08-16, real hardware):

- **Live agent loop** (mini-2): `homi spawn scout --cli claude` came up
  claim+seat+bind+**adopted**; `homi ask scout …` delivered as a turn; the
  agent autonomously ran the reply command; the blocking ask resolved with
  `reply "FABRIC-ALIVE"`, latency 10.09s.
- **Live cross-device seats** (mini-2 → air-2 over the real link): `seat
  spawn --device` returned `aadarshs-mac-air-2:%1`; send+read round-tripped
  (`LIVE_SEAT_E2E_42` + the far hostname), gated by `--allow-seats`.
- **Live agent move** (mini-2 → air-2): transcript rsync'd + named on
  arrival, mailbox merged in order (2 history + 1 straggler), origin flipped
  to proxy — a bare-name send at the origin landed in the far inbox.
- **Live boxed agent** (apple/container, `--network none`): the VM published
  its socket pair; the roster read it `live [boxed-probed]`; mail in (wrapper
  intact) and out (attributed to the boxed identity, spool acked clear); the
  air gap held (`ENETUNREACH`) — the box's only egress was homi mail.
