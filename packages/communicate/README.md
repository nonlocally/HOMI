# HOMI — combined communication package

HOMI gives agents durable identities and mailboxes, connects existing Claude and
Codex sessions, and controls terminal seats. This package includes the existing
native delivery, bus, durable mailbox/link, and seat implementations in one
artifact. The npm name `@aadarwal/communicate`, plugin identity
`communicate@communicate`, and state directory remain compatible with earlier
installations.

## Install a reviewed release

Get the release archive and checksum from
[nonlocally/HOMI releases](https://github.com/nonlocally/HOMI/releases). After
checking the checksum and extracting `homi-0.3.0.tar.gz`:

```sh
./homi-0.3.0/bin/homi setup
./homi-0.3.0/bin/homi doctor
```

The archive includes its Node dependencies. It requires macOS or Linux, Node.js
20+, Python 3.9+, and Bash. Seat operations additionally need tmux; provider
launches need the relevant authenticated agent CLI. SSH and Tailscale are
optional, capability-specific dependencies. Installation does not install model
clients, obtain their credentials, or grant access to a hosted service.

`setup` registers available Claude/Codex clients and stages an immutable copy at
`~/.local/share/communicate/0.3.0-<manifest-hash>/`. The stable `current` symlink
selects that release. Add `~/.local/share/communicate/bin` to PATH for the `homi`
and compatibility `communicate` commands. Start a fresh client session after
changing the plugin so its loaded instructions and tools match the installation.

```sh
homi setup --claude                # only this client
homi setup --codex                 # only this client
homi setup --no-clients            # CLI installation only
homi setup --service               # also install the per-user daemon service
homi setup --dry-run               # preview without changing the installation
```

The service uses launchd on macOS or systemd --user on Linux. An existing managed
service is refreshed on update unless `--no-service` is supplied. Setup preserves
its device name when a daemon already answers. `homi doctor` reports the invoked
package, installed payload and source commit, actual running daemon source,
client registration, and optional dependencies without starting a daemon.

## One interface, explicit destinations

```sh
homi start
homi claim reviewer
homi send reviewer --from operator -- 'Please review this change'
homi inbox reviewer
homi agents --json
homi bus register
homi bus agents --json
homi bus send TARGET -- 'A message to an existing registered session'
homi native agents
homi profile preview --terminal --mesh
```

Default verbs address durable HOMI identities. `homi bus` addresses registrations
of exact existing Claude/Codex sessions and keeps bus membership checks and
receipts. `homi native` retains local socket, existing-session queue, and SSH
commands. `homi profile` manages optional workstation configuration. A durable
mailbox acceptance, a bus receipt, a native delivery, and a correlated answer
retain their distinct meanings; none implies that a model completed the task.

The plugin includes skills, slash commands, and MCP tools. Existing native/bus
MCP names and their order remain unchanged; durable tools use `homi_` names.
`homi serve` runs this combined MCP interface. The packaged plugin resolves its
own release or the stable installed release, including when an agent client
copies it into a cache. It does not fall back to an old checkout or npm registry.

## Local, self-hosted, and hosted communication

Local operation needs no invitation or hosted account. A configured broker is
used as configured; without configuration, bus commands use a local loopback
broker. Durable cross-device mail uses HOMI's existing device links. Bus sharing
uses its existing scoped enrollment and exact-session adapters.

For a shared bus, its owner creates a scoped invitation. The participant redeems
it and explicitly registers the session they want to expose:

```sh
homi bus connect INVITE_CODE --device peer-device
homi bus register --bus project
homi bus dashboard --open
```

Use `homi bus use local` to select the local broker or `homi bus use https://HOST`
to select a previously enrolled broker. Owning the source or installing HOMI
does not grant access to any existing hosted service; its administrator controls
browser access, device enrollment, and private bus membership. See the repository
[bus documentation](https://github.com/nonlocally/HOMI/blob/main/docs/BUSES.md)
for owner networking and invitation details.

## Update, rollback, and uninstall

Run `update` from the newly downloaded, verified release:

```sh
/path/to/new-release/bin/homi update
homi rollback --dry-run
homi rollback
homi uninstall --claude            # remove this owned client integration
homi uninstall                    # remove owned integrations and executable links
homi uninstall --purge            # also remove retained managed release payloads
```

A failed activation restores the previous `current` pointer. Releases are never
replaced in place; rollback selects the retained previous payload. Installer
ownership checks preserve client registrations, executables, and service files
that someone changed after installation. Previous service definitions and
Claude settings are backed up. Uninstall and purge always preserve identities,
mail, credentials, and runtime configuration under
`${COMM_STATE:-~/.local/state/communicate}`. They do not rewrite external model
state or restart already-open agent sessions.

## Development and compatibility

The maintained public artifact is the GitHub release; the historical npm names
are not a claim that version 0.3.0 has been published to npm. A checkout can build
an npm-compatible archive or the self-contained release without changing live
installations:

```sh
npm --prefix packages/communicate ci
npm --prefix packages/communicate run vendor
npm --prefix packages/communicate test
scripts/test-communicate-dist.sh
```

The distribution checks install a packed artifact into temporary directories and
exercise bus MCP, durable MCP with concurrent ask/reply, copied plugin caches,
immutable updates, rollback, ownership, and preserved state. Service-manager
failure checks use fixtures; real per-user service qualification is separate.
Phone, board, talk, and cockpit applications are not part of this payload. Bus
browser UI, graph assets, and the human inbox remain included. MIT license.
