# HOMI — the combined communication package

This package is HOMI's installable unit: the `homi` command, the compatible
`communicate` command, the MCP server, the Claude Code/Codex plugin, and the
durable daemon, with production Node dependencies included in the release
archive. The npm name `@aadarwal/communicate`, the plugin identity
`communicate@communicate`, and the state directory are kept for compatibility
with earlier Communicate installations. Version 0.3.0 is distributed as a release
archive; the npm name is not a claim that 0.3.0 is published to npm.

## Install

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup --claude --codex
./homi-0.3.0/bin/homi doctor
export PATH="$HOME/.local/share/communicate/bin:$PATH"
```

Requires macOS or Linux, Node.js 20+, Python 3.9+, and Bash. tmux is needed for
seats; an authenticated Claude Code or Codex CLI for agent sessions. `setup`
stages an immutable copy of the release at
`~/.local/share/communicate/0.3.0-<manifest-hash>/`, points `current` at it, and
registers the plugin with the clients you named. Start a fresh client session
afterwards. The full flag reference, service, update, rollback, uninstall,
and troubleshooting are in [docs/INSTALL.md](../../docs/INSTALL.md).

```sh
homi setup --claude                             # or --codex; --no-clients for CLI only
homi setup --service                             # per-user launchd or systemd --user service
homi setup --dry-run                             # show every change without making it
/path/to/new-release/bin/homi update             # activate a newer release
homi rollback                                    # retained previous release
homi uninstall --claude                         # restore this client's previous registration
homi uninstall --purge                          # remove owned integrations/payloads; state preserved
```

## Commands

```sh
homi start | status | agents                     # daemon and durable roster
homi claim NAME                                  # a durable identity with a mailbox
homi send NAME 'message' --from NAME             # stored mail
homi ask NAME 'question' --timeout SEC           # blocks for a correlated reply
homi reply TOKEN 'answer' --from NAME            # answer a received question
homi wait NAME --timeout SEC --json              # wait for mail arriving after this call starts
homi inbox NAME                                  # stored JSONL; reading does not acknowledge it
homi spawn NAME --cli claude|codex --cwd DIR     # run a model as an identity in a seat
homi seat ls | read | send | state | interrupt | kill
homi bus register | agents | send | reply | receipt | dashboard | connect | use
homi native agents | route | ask | whereis       # native session routes, local and SSH
homi profile preview | install | status | uninstall
```

Default verbs address durable identities. `homi bus` addresses registered
sessions on the configured broker with membership checks and receipts.
`homi native` addresses local sockets, existing-session queues, and SSH routes.
The three address spaces are explicit, so a failed lookup never targets another
agent. A stored mailbox message, a bus receipt, a native submission, and a
correlated reply keep their distinct meanings; none says a model finished the
task. See [docs/CLI.md](../../docs/CLI.md).

## Plugin and MCP

The plugin carries skills, slash commands, and MCP tools. Native and bus tools
keep their existing names and order; durable tools use `homi_` names. `homi serve`
runs the MCP server on stdio. Setup writes the client's projection of the plugin
under the data directory's `integrations/` folder, leaving the release itself
unchanged; its MCP descriptor names the installed entry point and carries the
selected data, state, and client configuration paths explicitly, even when a
client filters the child environment. It never falls back to a source checkout
or an npm registry.

## Local, self-hosted, and hosted buses

Without configuration, bus commands use a local loopback broker; no account or
invitation is needed. A configured broker is used as configured. To join a
shared bus, its owner issues a scoped invitation:

```sh
homi bus connect INVITE_CODE --device peer-device
homi bus register --bus project
homi bus dashboard --open
```

`homi bus use local` selects the local broker; `homi bus use https://HOST`
selects an enrolled remote one. Installing this package grants no access to any
hosted service; its administrator controls browser access, device enrollment,
and membership. Owner-side networking and invitations are in
[docs/BUSES.md](../../docs/BUSES.md).

## Development

```sh
npm --prefix packages/communicate ci
npm --prefix packages/communicate run vendor
npm --prefix packages/communicate test
scripts/test-communicate-dist.sh
python3 scripts/build-release.py
```

The package tests install a packed artifact into temporary directories and
exercise the MCP server, durable ask/reply, copied plugin caches, immutable
updates, rollback, ownership, service boundaries (with fixtures, never the
host's service manager), install recovery, and client restoration. Real
per-user service qualification and real provider exchanges are separate,
opt-in checks described in [docs/RELEASING.md](../../docs/RELEASING.md).

MIT license; bundled third-party notices are retained.
