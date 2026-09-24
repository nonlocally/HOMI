# HOMI

HOMI gives coding agents durable identities with mailboxes, lets them message each
other across sessions and machines, and runs them in terminal seats you can observe
and control. One `homi` command, one Claude Code/Codex plugin, one MCP server.
macOS and Linux.

Download [HOMI 0.3.0](https://github.com/nonlocally/HOMI/releases/tag/v0.3.0)
or install with [Homebrew](docs/INSTALL.md#homebrew).

## Why

Agents lose each other. A session ends, a pane closes, a machine sleeps, and the
address you had for an agent is gone with it. HOMI separates three things that
usually get tangled:

- **Identity.** A durable name with a mailbox. The daemon keeps receiving mail
  for it while no model is running.
- **Execution.** Where that identity is running right now: a Claude Code or Codex
  session, a tmux seat, a linked device. It can stop and restart without the
  identity changing.
- **Delivery.** Whether a message was *stored*, *submitted* to a client,
  or *replied* to. A delivery receipt alone does not prove an answer; use an
  explicit reply token to correlate a response with its question.

HOMI runs on your machine and sends no usage telemetry. Local communication needs
no hosted account. Your model clients use their configured providers; remote
communication uses the brokers and hosts you choose.

## Install

Start with macOS or Linux, Node.js 20+, and Bash to launch the archive.
Guided setup checks the remaining requirements and offers to install missing
tools for the features you choose: Claude Code, Codex, terminal helpers, mesh,
and, on macOS, Ghostty. You can keep a server installation CLI-only.

Download the archive and its checksum, then verify and extract it:

```sh
curl -fLO https://github.com/nonlocally/HOMI/releases/download/v0.3.0/homi-0.3.0.tar.gz
curl -fLO https://github.com/nonlocally/HOMI/releases/download/v0.3.0/homi-0.3.0.tar.gz.sha256
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
./homi-0.3.0/bin/homi setup                    # guided when run in a terminal
./homi-0.3.0/bin/homi doctor
export PATH="$HOME/.local/share/communicate/bin:$PATH"   # put this in your shell rc
```

Choose the clients and optional terminal features you want, review the package
and configuration plan, then confirm. Missing selected dependencies can be
installed for you; existing provider clients are kept. Provider login is a
separate choice. No desktop software is selected automatically.
Selecting Claude Code or Codex also checks tmux for agent executions, even if
you decline terminal configuration. Installing tmux does not change your shell.

For a repeatable selection, preview first, then apply explicitly:

```sh
homi setup --install-missing --claude --codex --terminal --mesh --dry-run
homi setup --install-missing --claude --codex --terminal --mesh --yes
# On macOS, add --ghostty to also select Ghostty and its configured Nerd Font.
```

`setup` copies the release into an immutable directory under
`~/.local/share/communicate/`, points the stable `current` link at it, and
registers the plugin with the clients you named. Keep a copy of the archive for
recovery. Selecting terminal or mesh profiles adds managed configuration includes;
unrelated settings and private overrides are preserved. Explicit
`setup --claude`, `setup --codex`, and `profile install` remain configuration-only
and do not download missing software. Full requirements, platform recipes,
flags, and lifecycle are in [INSTALL.md](docs/INSTALL.md).

Alternatively, `brew install nonlocally/tap/homi` installs the commands and core
dependencies. Then run `homi setup` to choose your clients and optional tools.
Service installation is also explicit.

## First success

Complete setup, sign in through the client you chose, then open a fresh **Claude
Code CLI** or **Codex CLI** session in your project. Ask it in plain language:

> Create a Claude agent called researcher, ask it to investigate the flaky test
> in this project, and bring me its answer.

Use Codex instead if that is the provider you installed and authenticated. The
agent uses the loaded HOMI instructions and tools to check readiness, create the
identity and execution, send the task, and collect a correlated reply. You do not
need to type identity or messaging commands for normal use. If a required tool
or permission is missing, the agent should explain what is needed before continuing.

[QUICKSTART.md](docs/QUICKSTART.md) covers this workflow. Its optional CLI reference
shows the underlying operations and how to inspect delivery when debugging.

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

Ask your agent to register this session on the configured bus, list the agents
it can reach, or send a task to a named peer. A **bus** is the session directory
and message gateway; local use needs no hosted account. The graph, roster, and
human inbox are available when you ask to open the bus dashboard.

Joining someone else's bus requires a scoped invitation from its owner. Installing
HOMI grants no access to a hosted bus. For durable mail between your own machines,
ask the agent to link the specified daemons over SSH; that is a separate,
explicit operation. Native routes to local and remote sessions remain available.
Each address space is explicit, so a failed lookup never silently targets a
different agent. See [BUSES.md](docs/BUSES.md) and the [CLI reference](docs/CLI.md).

## Optional: terminal, mesh, snapshots, containers

Profiles are optional. Preview the managed includes and configuration files
before adding terminal, shell, or mesh helpers:

```sh
homi profile preview --terminal --mesh        # shows exactly which files would change
homi profile install --terminal --mesh
```

- `--terminal`: tmux navigation, tiling, an agent launch picker, `cx`/`cxx` and
  `cdx`/`cdxx` commands, and Ghostty settings. Shortcuts work from zsh or Bash;
  Bash runs their implementation without replacing your interactive shell.
- `--mesh`: manual and Tailscale host discovery, SSH helpers.
- `--snapshots`: opt-in workspace save/restore (`tss`/`tsr`) and snapshots mirrored
  to an archive volume, with a separately selected, reversible schedule
  ([SNAPSHOTS.md](docs/SNAPSHOTS.md)).
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
