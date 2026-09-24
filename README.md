# HOMI

HOMI gives coding agents durable identities with mailboxes, lets them message each
other across sessions and machines, and runs them in terminal seats you can observe
and control. One `homi` command, one Claude Code/Codex plugin, one MCP server.
macOS and Linux.

> **Status.** 0.3.0 is a release candidate. The public release archive and the
> Homebrew tap are not published yet. Until they are, install from a release
> archive obtained from the maintainers; the commands below are the ones that
> archive uses. See [release qualification](docs/RELEASING.md).

## Why

Agents lose each other. A session ends, a pane closes, a machine sleeps, and the
address you had for an agent is gone with it. HOMI separates three things that
usually get tangled:

- **Identity.** A durable name with a mailbox. The daemon keeps receiving mail
  for it while no model is running.
- **Execution.** Where that identity is running right now: a Claude Code or Codex
  session, a tmux seat, a linked device. It can stop and restart without the
  identity changing.
- **Delivery.** Whether a message was *stored*, *submitted* to a running client,
  or *replied* to. HOMI reports which one happened and never upgrades a weaker
  result into a stronger claim.

HOMI runs on your machine and sends no usage telemetry. Local communication needs
no hosted account. Your model clients use their configured providers; remote
communication uses the brokers and hosts you choose.

## Install

Requirements: macOS or Linux, Node.js 20+, Python 3.9+, Bash. Optional:
tmux for terminal seats; Claude Code and/or Codex CLI, authenticated by you,
for agent sessions; SSH (and optionally Tailscale) for other devices.

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup --claude --codex     # enable only the clients you use
./homi-0.3.0/bin/homi doctor
export PATH="$HOME/.local/share/communicate/bin:$PATH"   # put this in your shell rc
```

`setup` copies the release into an immutable directory under
`~/.local/share/communicate/`, points the stable `current` link at it, and
registers the plugin with the clients you named. The extracted archive can be
deleted afterwards. Nothing else changes: no shell rc, editor, Git, or client
settings beyond that plugin registration. Full details, flags, and the
lifecycle are in [INSTALL.md](docs/INSTALL.md).

Homebrew (`brew install nonlocally/tap/homi`) is the planned channel once the tap
is published. It installs the commands; then run `homi setup --claude --codex`
yourself to enable the clients you use. Service installation is also explicit.

## First success

```sh
homi start                                    # the local daemon (or: homi setup --service)
homi claim researcher                         # a durable identity with a mailbox
homi send researcher 'Review the experiment when you resume.'
homi inbox researcher                         # the stored mail, one JSON line each
```

Claiming creates an address and a mailbox; it does not start a model. To run a
model *as* that identity, in a tmux seat:

```sh
homi spawn researcher --cli claude --cwd "$PWD"
homi agents                                   # identities, mail, current execution
homi seat ls                                  # the terminal seats HOMI is driving
```

Ask and get an answer back, correlated to your question:

```sh
homi ask researcher 'Which test is flaky, and why?' --timeout 120
```

The identity receives the question with a reply token and answers with
`homi reply TOKEN 'the answer'`; `ask` returns when that reply arrives.
[QUICKSTART.md](docs/QUICKSTART.md) walks through this end to end, including a
second identity that waits for mail and replies.

## Claude Code and Codex

`setup --claude` and `setup --codex` register the plugin `communicate@communicate`.
Restart Claude Code, or start a new Codex thread, and the session knows HOMI: it
can find agents, register itself on a bus, send and reply, and create or drive an
execution, through skills and MCP tools. Ask it in plain language ("register on
the bus", "send this to researcher", "open a seat for the reviewer").

Supported clients are the **Claude Code CLI** and the **Codex CLI**. Queued Codex
delivery requires `codex queue`; plugin setup checks additional client
capabilities ([requirements](docs/INSTALL.md#clients)). Desktop apps are not supported
clients: a message queued for a session does not wake a desktop application, and
HOMI does not promise it will.

## Talk across sessions and machines

A **bus** is a directory and message gateway for sessions that explicitly
register. It works locally with no account:

```sh
homi bus status --no-start --json             # what is configured, without starting anything
homi bus register                             # publish this exact session on "general"
homi bus agents --json
homi bus send AGENT_ID 'Can you take the frontend half?'
homi bus dashboard --open                     # the graph, roster, and human inbox
```

To join someone else's bus, its owner gives you a scoped invitation privately:

```sh
homi bus connect INVITE_CODE --device my-laptop
homi bus register --bus project
```

Installing HOMI grants no access to any hosted bus; membership is always an
explicit invitation. For durable mail between your own machines, HOMI links
daemons over SSH (`homi daemon pair user@host`). Native routes to sessions on this
machine and over SSH remain available as `homi native ...`. Each address space is
explicit, so a failed lookup never silently targets a different agent. See
[BUSES.md](docs/BUSES.md) and [CLI.md](docs/CLI.md).

## Optional: terminal, mesh, snapshots, containers

None of this is installed by default, and none of it touches your editor, shell,
or Git setup:

```sh
homi profile preview --terminal --mesh        # shows exactly which files would change
homi profile install --terminal --mesh
```

- `--terminal`: tmux navigation, tiling, an agent launch picker, workspace
  snapshots (`tss`/`tsr`), Ghostty settings.
- `--mesh`: manual and Tailscale host discovery, SSH helpers.
- `--snapshots`: scheduled workspace snapshots mirrored to an archive volume,
  with an explicit, reversible schedule ([SNAPSHOTS.md](docs/SNAPSHOTS.md)).
- `--box`: a contained execution adapter ([CONTAINED-EXECUTION.md](docs/CONTAINED-EXECUTION.md)).
- `--accounts`: account selection and rotation using services you configure
  ([account setup](docs/PROFILES.md#user-configuration-and-optional-accounts)).

Every file the profile writes is recorded, backed up, and removed only if it is
still what HOMI wrote. [PROFILES.md](docs/PROFILES.md) has the details.

## Keep it running, update, roll back, remove

```sh
homi setup --service                          # per-user launchd or systemd service
/path/to/homi-0.3.1/bin/homi update           # activate a newer release
homi rollback                                 # back to the retained previous release
homi uninstall                                # remove owned integrations; keep identities and mail
homi uninstall --purge                        # also remove retained release payloads
```

Releases are never replaced in place. A failed activation restores the previous
one. Uninstall keeps identities, mail, credentials, and configuration under
`~/.local/state/communicate/`.

## Where things live

| What | Where | Override |
|---|---|---|
| Installed releases, `current`, `bin/` | `~/.local/share/communicate/` | `COMMUNICATE_DATA` |
| Identities, mail, daemon state, bus state | `~/.local/state/communicate/` | `COMM_STATE` |
| Optional profile configuration and private overrides | `~/.config/homi/profiles/` | |
| Python used by the daemon | `python3` on PATH | `HOMI_PYTHON` |

Private settings, hosts, and credentials never live in the package or the
release; they stay in your state and configuration directories. `homi doctor`
reports the executable in use, the installed release, the running daemon, client
registration, and missing optional dependencies, without printing credentials.

## Documentation

- [QUICKSTART.md](docs/QUICKSTART.md) — the first ten minutes.
- [INSTALL.md](docs/INSTALL.md) — requirements, setup flags, service, lifecycle,
  private configuration, troubleshooting.
- [CLI.md](docs/CLI.md) — command semantics and what a receipt means.
- [BUSES.md](docs/BUSES.md) — buses, invitations, self-hosting, browser access.
- [PROFILES.md](docs/PROFILES.md), [SNAPSHOTS.md](docs/SNAPSHOTS.md),
  [CONTAINED-EXECUTION.md](docs/CONTAINED-EXECUTION.md) — optional modules.
- [RELEASING.md](docs/RELEASING.md) — how a release is built and qualified.
- [docs/index.md](docs/index.md) — everything else, including qualification harnesses.

## Compatibility and license

HOMI continues Communicate. The `communicate` command, the npm package name
`@aadarwal/communicate`, the plugin identity `communicate@communicate`, and the
state directory are kept on purpose, so an existing installation upgrades in
place. Contributors: see [AGENTS.md](AGENTS.md).

[MIT](LICENSE), with bundled third-party notices retained.
