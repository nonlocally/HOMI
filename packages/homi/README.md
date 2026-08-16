# @aadarwal/homi

Durable agent identity, mailboxes, and store-and-forward messaging for AI agents
— shipped as an MCP server and a CLI. **Two faces, one kernel:** the CLI is the
complete, tested definition of the fabric; the MCP server is a thin projection —
every tool call is one JSON line to a local daemon, with zero fabric logic in the
Node layer.

## What it gives an agent

- **A durable name.** `claim <name>` gives you a stable socket, a sweep-proof
  roster entry, and a mailbox that outlives the process behind it. An address
  does not die with a session.
- **Store-and-forward mail.** `send <name>` reaches an agent whether or not it is
  running right now; when it wakes, held mail is delivered as a turn. Cross-device
  addressing (`name@device`) rides acked, deduped, backoff'd links.
- **Ask/reply, groups, notify.** `ask` blocks for a correlated reply; `group_send`
  fans to many; `notify` summons the human with a durable reason.
- **Seats** — the interactive escape hatch. Drive terminal surfaces you cannot
  mailbox (cluster shells, REPLs, TUIs) with a state classifier and a verified
  send discipline; seats can live on another device (opt-in per link).
- **spawn** — claim an identity, launch an agent in a seat, bind them: one agent
  reachable by mail AND watchable in a seat.

## Install

```sh
npx @aadarwal/homi setup --claim <your-name>     # sets up the per-device daemon
```

Then add it to your agent:

```sh
claude mcp add homi -- npx -y @aadarwal/homi serve
codex  mcp add homi -- npx -y @aadarwal/homi serve
```

`homi doctor` checks the install; `homi serve` is the MCP stdio server an MCP
client spawns. Requires `python3` ≥ 3.9 (the daemon is stdlib-only — no pip) and,
for seats, `tmux`.

## How it fits together

```
  MCP client (Claude Code / Codex)
        │  stdio, MCP
   homi serve  ── one JSON line ──▶  homi daemon (python, per device)
        ▲                                  │  durable mailboxes, links, seats
        └───────── same kernel ────────────┘
  communicate homi <verb>  (the CLI face)
```

A repo-managed `communicate homi` and an npm-managed `@aadarwal/homi` resolve the
SAME state root, socket, and daemon — they are two faces of one per-device daemon,
never two daemons.

## Tools

`agents_list`, `whoami`, `send`, `ask`, `inbox_read`, `wait_for_message`,
`claim`, `release`, `group_send`, `notify`, `seat_ls`/`seat_spawn`/`seat_send`/
`seat_read`/`seat_state`/`seat_wait`, `spawn`, `status`.

Messaging is the default plane; seats are the explicit interactive escape hatch.

## License

MIT
