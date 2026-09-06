# communicate — repository briefing

An **agent router**: every AI coding agent gets an identity (a name) and an
address (a unix socket); messages route between them across machines over
Tailscale + SSH. Claude Code and Codex only, on purpose. `docs/MECHANISM.md`
holds the reverse-engineered wire protocol; `README.md` the user story.

## The three planes

1. **communicate** (bash + python, `bin/` + `lib/*.sh` + `lib/cc_peer.py`) —
   rendezvous routing: sidecars, sockets, ssh bridges, codex lanes, wake
   triggers. Standalone; distributed as `@aadarwal/communicate`
   (`packages/communicate/` + `plugins/communicate/`).
   Explicit buses add `lib/bus.py`, `lib/bus_broker.py`, and `lib/bus_ui.html`:
   opt-in session registration, scoped membership, an HTTPS gateway, and dashboard.
2. **homi** (`lib/homi*.py`, `lib/homi.sh`, `packages/homi/`) — the durable
   plane: identities that outlive processes, mailboxes, store→wake, device
   links, seats, federation, move. NOT part of the communicate distribution;
   unification is a later goal.
3. **Distribution** (`plugins/communicate/`, `packages/communicate/`,
   `.agents/`) — the dual-ecosystem plugin (Claude Code + Codex) and the npx
   installer. One payload, sibling manifests. Repo-havers register the checkout
   directly with `communicate setup-repo` (git pull, then setup-repo refreshes caches); the npm
   package's `setup` is the no-repo path.

## Operating on the bus (for any agent working here)

- **Register explicitly:** `communicate bus register` attaches this current
  session to general; `--bus photonics` joins only photonics. Verify its exact
  registration ID with `bus agents --bus photonics --json`. Never use a newest
  session guess or headless `codex peer` as a substitute for self.
- **Explicit-bus interface:** `communicate bus dashboard --open` shows permitted
  buses/agents. `bus send ID --bus NAME -- TEXT` enforces shared membership;
  `bus receipt ID` distinguishes persistence, endpoint delivery and queueing.
  Invite/connect/revoke and the network boundary are documented in `docs/BUSES.md`.
  Native `agents`/`route` below remain a separate filesystem/SSH trust path.

- **See who's here:** `communicate agents` (or the native ListAgents inside
  Claude Code). `communicate whereis <name>` resolves one name.
- **Talk:** `communicate route <name> "<msg>"`; from inside Claude Code prefer
  the native SendMessage tool for Claude→Claude (it attests your permission
  mode, so the receiver's inbound gate holds less). Reply to the `from` socket
  of any cross-session message you receive.
- **Need the answer?** `communicate ask <name> "<q>"` — blocks for the reply;
  its in-band `[reply-to …]` block teaches the receiver how to answer (works on
  Claude sessions and local Codex sessions by thread name). If a message YOU
  receive ends with `[reply-to …]`, answer exactly as it instructs.
- **Codex lanes:** existing session → `communicate codex queue <dev> <name>
  "<msg>"` (async; reply stays in that session; Codex ≥ 0.151); fresh headless
  answer → `communicate codex ask <dev> "<q>"` (sync, thread continuity);
  peer in ListAgents → `communicate codex peer <dev> [name]`.
- **Rename:** your `/rename` name IS your bus name (the sidecar mirrors it).
  Joining the bus = numeric-filename sidecar + live pid + answering socket +
  newline-JSON frames — see
  `plugins/communicate/skills/communicate/references/wire-protocol.md`.
- **Other devices:** `communicate link <dev>` to check substrate;
  `communicate claude bridge <dev> [sel]` (from inside a Claude session) to
  make a remote session a native peer; `communicate down` tears down
  everything communicate started.
- **PATH fallback** (harnesses that don't auto-PATH plugin bins):
  `~/.local/share/communicate/current/vendor/bin/communicate`, or the repo's
  `bin/communicate`.

## Developing here

- Tests are `scripts/test-*.sh` — deterministic, no network unless stated;
  `scripts/test-communicate-dist.sh` covers the distribution end to end.
- Package work: `npm --prefix packages/communicate run vendor` rebuilds the
  payload (the vendor script is the allowlist — it throws if anything
  homi-flavored would land); `npm --prefix packages/communicate test` runs the
  MCP + setup smokes.
- Style: bash is shellcheck-clean, `die()` on misuse, never interpolate user
  text into a shell parse (NUL-delimited payloads + python argv adapters);
  python is stdlib-only. No secrets in the repo — `.webui_secret_key`-style
  leaks were purged once already.
- Installer discipline: `~/.claude/settings.json` is merge-not-clobber with a
  backup; `~/.codex/config.toml` is never hand-edited (use the codex CLI).
