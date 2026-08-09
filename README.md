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
lib/cc_peer.py      the cc-socks wire protocol (serve + send)
docs/MECHANISM.md   how Claude Code peer messaging actually works
scripts/            end-to-end tests
```

## Status

Tier 1 (Claude↔Claude bridge) and Tier 3 (Codex-as-peer) are verified
end-to-end; Tier 2 (`codex ask`) including `resume` continuity is verified
locally. See `scripts/` for the tests.
