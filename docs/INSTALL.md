# Installation

> **Status.** 0.3.0 is a release candidate. The public release archive and the
> Homebrew tap are not published yet. Install from a release archive obtained
> from the maintainers; the same archive and commands become the public channel
> when publication completes.

## Requirements

| Needed for | Requirement |
|---|---|
| Everything | macOS or Linux; Node.js 20 or later; Python 3.9 or later; Bash |
| Terminal seats and spawned executions | tmux |
| Agent sessions | Claude Code CLI and/or Codex CLI, installed and authenticated by you; see [Clients](#clients) |
| Other devices | SSH; Tailscale optional |
| Optional profiles | Bash 4+, fzf, jq for the terminal and mesh modules; see [PROFILES.md](PROFILES.md) |

Setup does not install a model client, obtain credentials, or enroll you in a
hosted service.

## Get the archive

The release is `homi-VERSION.tar.gz` with a `.sha256` beside it. Verify before
extracting:

```sh
shasum -a 256 -c homi-0.3.0.tar.gz.sha256
tar -xzf homi-0.3.0.tar.gz
```

The archive contains the CLI, the MCP server, the plugin, the daemon, and its
production Node dependencies. It needs no Git checkout, npm account, or registry
access. An archive copied to a machine over SSH installs exactly like a
downloaded one.

## Run setup

```sh
./homi-0.3.0/bin/homi setup --claude --codex
```

| Flag | Effect |
|---|---|
| `--claude`, `--codex` | Register the plugin with that client. With neither flag, both are attempted when their CLIs are present. |
| `--no-clients` | Install the CLI only; register clients later with `homi setup --claude` or `--codex`. |
| `--service` | Also install the per-user daemon service (launchd or systemd `--user`). |
| `--no-service` | Leave an existing managed service untouched during an update. |
| `--service-inherit=NAME` | Add an allowed variable to the service environment, such as `CLAUDE_CONFIG_DIR` or `CODEX_HOME`. Repeat for each variable. |
| `--dry-run` | Print every path, setting, and command setup would touch, and change nothing. |

What setup does, in order: verifies the archive against its manifest; copies the
release to `~/.local/share/communicate/<version>-<hash>/`, which is never modified
again; points the stable link `~/.local/share/communicate/current` at it; creates
`~/.local/share/communicate/bin/homi` and `bin/communicate`; registers the plugin
with the clients you named through their own CLIs; optionally installs the
service. If activation fails, setup attempts to restore the previous state and
reports any recovery still needed.

Then:

```sh
export PATH="$HOME/.local/share/communicate/bin:$PATH"   # add to ~/.bashrc, ~/.zshrc, or equivalent
homi doctor
```

`doctor` reports the executable in use, the installed release and its source
commit, the running daemon and whether it runs the installed release, client
registration and the cached plugin version, the managed service, and optional
dependencies. It never starts a daemon and never prints credentials.

## Clients

After `setup --claude`, restart Claude Code. After `setup --codex`, start a new
Codex thread. A running session keeps the plugin it loaded at start.

The plugin is `communicate@communicate` from the marketplace `communicate`, served
from the installed release. Setup records what was registered before it ran
(installed, enabled, marketplace source, and for Codex the plugin's user
configuration) and restores exactly that on uninstall. If you edit those client
settings after setup, later setup, rollback, or uninstall refuse to replace them
until you reconcile the change; they are never silently overwritten. Client
plugin registration is verified with Claude Code's `claude plugin` commands and
Codex CLI 0.156.1's `codex plugin` commands.

Codex queued delivery requires `codex queue`. Plugin setup also requires the
client's versioned configuration API so existing settings can be restored safely;
setup checks these capabilities before replacing a registration. Installation and
restoration are verified with Codex 0.156.1; queued input is also verified with
0.153.4. These are separate from full model and platform qualification, recorded
in [PROVIDER-QUALIFICATION.md](PROVIDER-QUALIFICATION.md).

Avoid concurrent edits to client configuration while setup, rollback, or removal
is running. Changes already present are checked before replacement; client CLI
commands do not provide a transaction spanning all registration changes.

## The daemon

`homi start` runs an unmanaged daemon for the current login. `homi setup --service`
installs a managed one:

- macOS: `~/Library/LaunchAgents/com.communicate.homi.plist`
- Linux: `~/.config/systemd/user/communicate-homi.service`

The service runs the installed release with an explicit environment: your home,
the state directory, the device name, and a PATH containing the selected Python
and standard executable directories. Use `--service-inherit=PATH` to pass your
current PATH explicitly. An installation in
an isolated home gets a scoped service name, so a qualification install can
never replace your real service. A previous definition under the same name is
backed up before replacement. Only one daemon owns a state root at a time.

## Update, roll back, uninstall

```sh
/path/to/homi-0.3.1/bin/homi update     # from the newly verified archive
homi rollback --dry-run
homi rollback
homi uninstall --claude                 # remove only this client's registration
homi uninstall                          # remove owned integrations, service, executable links
homi uninstall --purge                  # also delete retained release payloads
```

Rules that hold throughout:

- A release directory is never changed in place; `update` stages the new one
  and switches `current`. `rollback` switches back to the retained previous
  release and re-registers clients on it.
- Setup only ever replaces what it still owns. A client registration, executable
  link, or service file that someone else changed is kept, and setup says so.
- Uninstall preserves identities, mail, credentials, and configuration under
  `~/.local/state/communicate/`. `--purge` removes release payloads only, and
  refuses while any owned integration remains.
- An interrupted run leaves `~/.local/share/communicate/install.lock` with the
  owner's PID and start time. `doctor` shows it. After confirming that process is
  gone, rename that exact directory out of the way and retry; HOMI never removes
  another process's lock.

Upgrading from Communicate 0.1.x or 0.2.x keeps the original payload. Use the
new release's `bin/homi` for lifecycle commands; the old package has no `homi`
entry point.

## Private configuration

Nothing personal ships in the package. Where your settings go:

| Purpose | Location |
|---|---|
| Installed releases, `current`, `bin/` | `~/.local/share/communicate/` (`COMMUNICATE_DATA`) |
| Identities, mail, daemon and bus state, device links | `~/.local/state/communicate/` (`COMM_STATE`) |
| Bus broker selection, enrollment, device credential | `~/.local/state/communicate/bus/` |
| Optional profile files, `local.sh`, tmux/Ghostty overrides, mesh hosts | `~/.config/homi/profiles/` |
| Python for the daemon | `HOMI_PYTHON=/path/to/python3` |

Keep invitations, device credentials, and hosts out of source control and
public logs. A bus invitation grants membership on one bus; it never grants
shell access or seat control on any device.

## Optional profiles

```sh
homi profile preview --terminal --mesh    # read-only: every file, action, and conflict
homi profile install --terminal --mesh
homi profile status
homi profile uninstall
```

Modules: `--terminal`, `--mesh`, `--snapshots`, `--box`, `--accounts`. Profile
installation starts no service, runs no package manager, changes no login shell,
and reloads no tmux server. It records every file it writes with a backup and
removes only what is still exactly what it wrote. See [PROFILES.md](PROFILES.md),
[SNAPSHOTS.md](SNAPSHOTS.md), and [CONTAINED-EXECUTION.md](CONTAINED-EXECUTION.md).

## Troubleshooting

- **`homi: command not found`** — add `~/.local/share/communicate/bin` to PATH, or
  call the installed `~/.local/share/communicate/bin/homi` directly.
- **The session does not see HOMI** — restart Claude Code or start a new Codex
  thread; `homi doctor` shows the registration and cached version. A cached
  plugin proves installation, not that a running client loaded it.
- **`daemon release parity: different`** in `doctor` — the running daemon was
  started from another source (an unmanaged `homi start`, or an older service).
  `homi setup --service` restarts it on the installed release.
- **`MCP dependencies are missing`** — the installed payload was damaged.
  `uninstall --purge` from the retained archive, then `setup` again; state is
  preserved.
- **Setup refuses a file or registration** — it is no longer what HOMI wrote.
  The message names it; move it aside or reconcile the change, then retry.

## Homebrew

`brew install nonlocally/tap/homi` is the planned channel once the tap is
published. The formula installs the same archive under Homebrew's prefix and
puts `homi` and `communicate` on PATH; you still run `homi setup` to register
clients and, optionally, the service. Formula installation never configures your
machine by itself.

## Qualification

How a release is built, tested, and qualified before publication is described in
[RELEASING.md](RELEASING.md), with the installed-artifact, client-restoration,
and provider harnesses linked from [index.md](index.md).
