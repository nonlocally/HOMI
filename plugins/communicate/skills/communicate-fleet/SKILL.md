---
name: communicate-fleet
description: Reaching agents on other devices — ssh substrate, remote session listing, and bridging a remote Claude session in as a native local peer. Use when asked to talk to an agent on another machine/device, list sessions on a remote host, bridge/unbridge a session, or check/tear down communicate's active plumbing.
---

# communicate-fleet — other devices

Devices are addressed as `local`, a Tailscale hostname (e.g.
`aadarshs-mac-mini-2`), or `user@host`. Transport is plain ssh — your keys are
the trust model.

## Check the substrate first

```sh
communicate link <device>    # tailscale sees it? ssh works? codex there? how many Claude sessions?
communicate ls  [device]     # local: sessions + bridges · remote: its Claude session table
```

## Bridge a remote Claude session in

```sh
communicate claude bridge <device> [name|pid|newest]
communicate claude unbridge <device|all>
```

What it does: picks the remote sidecar, mirrors the two sockets across an
`ssh -L`/`ssh -R` pair INTO IDENTICAL PATHS on each side, and plants each
side's sidecar on the other. Because a forwarded unix socket is an ordinary
local file, every native guard passes — the remote session simply appears in
`ListAgents`/`communicate agents` (type `claude*`) and you message it normally;
replies come back the same way. Works across Anthropic accounts (it is
filesystem + socket, not cloud).

**Constraint:** run `bridge` from INSIDE a Claude Code session — it needs your
`$CLAUDE_CODE_MESSAGING_SOCKET` as the return address. A supervisor re-plants
sidecars every few seconds and survives transient ssh drops.

## Codex on other devices

All three Codex lanes take the device argument directly (`communicate codex
queue <device> …`, `ask`, `peer`) — no bridge needed; they run over ssh
per-command.

## See and tear down plumbing

```sh
communicate status   # active bridges (up/down), codex peers
communicate down     # stop wakes + unbridge all + unpeer all; removes every planted artifact
```

`down` is deliberately complete: sidecars it planted, tunnels, daemons — gone.
(It does not touch the homi plane, which is not part of this install.)

## Safety

Bridging widens "who can message this agent" from you-locally to whoever can
reach that socket over the ssh login. Only bridge to ends you trust; the
receiving side's inbound gate (see the `communicate` skill) still applies.
