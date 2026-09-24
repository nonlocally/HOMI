# HOMI — the combined communication package

This package is HOMI's installable unit: the `homi` command, the compatible
`communicate` command, the MCP server, the Claude Code/Codex plugin, and the
durable daemon, with production Node dependencies included in the release
archive. The npm name `@aadarwal/communicate`, the plugin identity
`communicate@communicate`, and the state directory are kept for compatibility
with earlier Communicate installations. Version 0.3.0 uses a release archive;
the npm name is not a claim that 0.3.0 is published to npm. Download the archive
and checksum from the [HOMI 0.3.0 release](https://github.com/nonlocally/HOMI/releases/tag/v0.3.0),
or install with [Homebrew](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/INSTALL.md#homebrew).
See [RELEASING.md](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/RELEASING.md)
for the validation process.

## Install

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup                   # guided in an interactive terminal
./homi-0.3.0/bin/homi doctor
export PATH="$HOME/.local/share/communicate/bin:$PATH"
```

Requires macOS or Linux, Node.js 20+, and Bash to launch the archive. Guided setup
checks Python and lets you select clients, terminal/mesh tools, optional Ghostty
on macOS, and service setup. It offers missing selected software and asks you to
review the plan before applying it. Provider login is a separate explicit choice;
existing provider clients are not implicitly upgraded. A server needs no GUI.
Selecting either client also checks tmux for agent seats, without requiring the
optional terminal profile or changing your interactive shell.

For an explicit selection, preview the plan:

```sh
homi setup --install-missing --claude --codex --terminal --mesh --dry-run
```

Replace `--dry-run` with `--yes` to apply it.
Use `--no-clients` for CLI-only operation. Existing `setup --claude` and
`profile install` commands remain configuration-only. Third-party packages are
not removed by HOMI's rollback or uninstall.

`setup` stages an immutable copy of the release at
`~/.local/share/communicate/0.3.0-<manifest-hash>/`, points `current` at it, and
registers the plugin with the clients you named. Start a fresh client session
afterwards. Keep a copy of the archive for recovery and purge if an installed
payload becomes damaged. The full flag reference, service, update, rollback, uninstall,
and troubleshooting are in [docs/INSTALL.md](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/INSTALL.md).

After setup and provider login, open a fresh Claude Code CLI or Codex CLI session
and ask for the work: *"Create a Claude agent called researcher, investigate the
flaky test in this project, and bring me its answer."* Use the provider you
configured. The agent handles readiness, identity creation, messaging and replies
through its installed HOMI tools; normal use does not require typing those CLI
commands. The optional terminal shortcuts work from zsh or Bash without changing
the interactive shell.

```sh
homi setup --guided                             # choose clients and optional dependencies
homi setup --claude                             # register an existing client only
homi setup --service                             # per-user launchd or systemd --user service
homi setup --dry-run                             # show every change without making it
/path/to/new-release/bin/homi update             # activate a newer release
homi rollback                                    # retained previous release
homi uninstall --claude                         # restore this client's previous registration
homi uninstall --purge                          # remove owned integrations/payloads; state preserved
```

## CLI reference

These are the operations the agent can perform for you, also available directly
for scripts and debugging:

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
task. See [docs/CLI.md](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/CLI.md).

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
[docs/BUSES.md](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/BUSES.md).

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
opt-in checks described in [docs/RELEASING.md](https://github.com/nonlocally/HOMI/blob/v0.3.0/docs/RELEASING.md).

MIT license; bundled third-party notices are retained.
