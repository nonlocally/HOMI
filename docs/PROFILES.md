# Optional workstation profiles

HOMI works without a terminal profile. These profiles retain selected, proven
Anu shell/tmux/Ghostty and mesh helpers. They are not installed by core setup and
do not replace your editor, Git configuration, or model-client settings.

```sh
homi profile preview --terminal --mesh
homi profile install --terminal --mesh
homi profile status
homi profile uninstall
```

The standalone equivalent is `python3 profiles/manage.py ...` in a source tree.
`preview` is the default action. All commands return JSON; `--home PATH` selects
an isolated home for testing. Profile installation never runs a package manager,
changes the login shell, reloads tmux, starts services, connects to a device, or
installs model runtimes. Open a new Bash shell after installation. Load the
generated tmux configuration deliberately in the server you intend to configure.
If you separately installed an owned snapshot schedule, profile uninstall asks
its service manager to unload that job before removing the owned files. It
refuses removal when ownership or unloading cannot be verified.

The terminal module uses Bash 4+, tmux, and fzf for pickers. The mesh module uses
Bash 4+, jq, SSH, and fzf for its hub. Tailscale is optional for manual hosts.
Ghostty and the configured JetBrainsMono Nerd Font are optional, separately
installed software. Clipboard integration selects pbcopy, wl-copy, or xclip.
The profile is written for macOS/Linux; automated macOS checks do not establish
full Linux runtime coverage. On a nonstandard Bash installation, put its bin
directory on PATH. No default-shell changes are made for you.
Fresh zsh sessions do not load these Bash helpers; run Bash explicitly or configure
your terminal's command deliberately. The installer does not write zsh startup files.

The account module uses Bash 4+, Python 3, jq, curl, and `column` for tables;
Codex account hooks also require `shasum`. Its optional credential client needs
the Infisical CLI and the selected existing credential-store client. SSH is used
only for explicitly configured remote sync/registration. Provider CLIs and
authentication are separate prerequisites. The box module needs Apple/container
on a supported Mac and an explicitly built image; selecting it on Linux does
not install a different backend. Snapshot scheduling needs the platform's user
service manager. `homi profile status` reports executable discovery, not proof
of authentication, service access, or a compatible installed version.

## What is retained

- Ghostty appearance and input preferences, with macOS settings selected only
  on macOS.
- tmux navigation, split/resize/swap/zoom, tiling, session/window bars, stash,
  nested-session mode, and an agent launcher picker.
- `t`, `tn`, `tk`, `tl`, `tp`, `tj`, `tw`, `twp`, `to`, `tws`, `twg`;
  linked session views preserve independent current windows for each client.
- `al`, `alw`, `taa`, `tra`, `tap`, `tscale`, `tsl`, `tslm`, and `tml`.
- `tss NAME` / `tsr [-n] NAME` workspace snapshots, with state under
  `~/.local/state/homi/workstation/sessions`. Names are restricted to tokens;
  save refuses to overwrite an existing snapshot. `tsr -n` previews restoration.
- `mesh` / `homi-mesh` for manual and Tailscale host discovery, SSH, VNC,
  explicit remote commands, host metadata, and remote terminal helpers.

Browser, chat, research, wall, phone, project-task dispatch, and dashboard
shortcuts are removed from this profile. It does not source every shell module,
start a landing UI, or introduce another terminal-message implementation.
Agent messaging and explicit execution control remain HOMI's existing APIs.

Snapshots rebuild topology and saved resume commands, not arbitrary process
memory. The inherited Claude inference chooses an unclaimed transcript for a
working directory; Codex uses an open rollout file with a last-session fallback.
Those fallback cases do not prove exact conversation identity. Non-agent
commands are staged but not automatically executed on restore. Existing sessions
are skipped. `homi profile install --snapshots` adds explicit snapshot/archive commands.
Scheduling remains a separate operation; see [snapshots](SNAPSHOTS.md).
Installing the profile does not migrate or load an existing LaunchAgent.

## User configuration and optional accounts

Generated files live in `~/.config/homi/profiles`. Keep private choices in:

- `local.sh`: trusted shell overrides, sourced by profile tools and shells;
- `local.tmux.conf`: loaded after the generated tmux configuration;
- `local.ghostty.conf`: loaded after the generated Ghostty configuration;
- `mesh/hosts.json` and `mesh/users.json`: private host/user mappings.

See `profiles/examples/local.sh`. The normal agent aliases use your native
Claude/Codex installation and authentication. No personal account service is
consulted. `cxx` and `cdxx` explicitly select their provider's full-auto modes;
`cx` and `cdx` preserve the corresponding interactive behavior. `cxc` refuses
to run without a configured containment adapter instead of silently running on
the host.

`HOMI_ACCOUNT_LAUNCHER` can name one executable accepting
`launch --provider claude|codex [--box] -- ARGS`, matching the existing
Anu account helper. `HOMI_BOX_LAUNCHER` can name an executable that accepts a
command and arguments. These are executable paths, not evaluated command strings.

`homi profile install --accounts` selects the packaged account launcher,
observer, and minimal secrets client. Configure the existing usage service,
credential store, and any existing account/cache paths explicitly in `local.sh`.
The module retains working rotation/rebalance and provider resume behavior;
it does not host the usage or secrets service. See the
[account module configuration](../profiles/runtime/accounts/README.md).

For the observer, also set `HOMI_PROFILE_WATCH=1`. The account module supplies
`HOMI_PANE_WATCHER`; a private override may name another executable. Generic
profiles start no watcher. Private tmux options such as `@anu_autorotate` and
`@anu_rebalance` belong in `local.tmux.conf`. Preserve account state, provider
homes, and session metadata before retiring old paths; do not run both old and
new observers on the same server.

`homi profile install --box` selects the optional local container adapter.
It never starts or installs a container runtime during profile installation.
See [contained execution](CONTAINED-EXECUTION.md) for explicit image builds,
mounts, credentials, and runtime requirements.

```sh
homi-mesh host add lab scientist@lab.example 2222
homi-mesh ssh lab
homi-mesh sshconfig
```

SSH export writes a separate `~/.config/homi/profiles/mesh/ssh.conf` and does
not change `~/.ssh/config`. Use it via `ssh -F PATH HOST` or add your own Include.
`mesh run` and `mesh deploy` execute the command you explicitly supply; they
are control operations, not agent messaging. VNC and actual remote host access
depend on configured platform software and authorization.

## Ownership, rollback, and migration

Profile installation snapshots its runtime into a content-addressed directory
under `~/.local/share/homi/profiles`. The active files and wrappers point to that
installed copy, so deleting or moving the source checkout does not break them.
No provider histories, credentials, or host inventories are copied into it.

The installer maintains `~/.local/state/homi/profiles/ownership.json` and original
backups. Shell, tmux, and Ghostty files receive a marked block; unrelated text
and file modes are preserved. Repeated installs preserve the first backup and
later user edits outside the block. A preflight conflict aborts installation.
A write failure during install or uninstall rolls back files already changed.
Edited generated files and edited managed blocks are not overwritten automatically.
Profile-owned configuration and ownership directories are restricted to the owner.
New snapshots (including terminal scrollback) and mesh data use private permissions
without changing the invoking shell's umask. Pre-existing snapshot data is retained;
review its permissions separately during personal migration.

Uninstall removes only matching owned content. If a target was replaced or an
owned block changed, it refuses the operation rather than deleting new content
or leaving dangling startup references. Original bytes are restored when there
are no later adjacent edits. Private overlays, snapshots, identities, provider
data, backups, and immutable payloads are retained. To roll back a normal
installation, use uninstall; then reinstall the previous release if needed.

File uninstall does not remove hooks already loaded into a running tmux server,
stop an account observer, or change existing shells. Before removing a profile
that you activated, deliberately stop its observer and remove/reconfigure its
loaded tmux hooks in each affected server. Otherwise those processes can retain
old state or reference removed helper paths. Preserve active panes and workloads;
the installer does not infer ownership of a whole tmux server or kill it. This
runtime retirement check is separate from the owned snapshot schedule handling.

Symlinked configuration files require a separate reviewed migration. The
installer intentionally refuses to write through a link into an existing Anu
checkout. It also refuses symlinked generated directories. It does not run the
legacy Anu unlinker: that command can remove replacement symlinks it no longer
owns.

```sh
homi profile migrate-preview
```

This read-only report identifies legacy configuration links, executable pointers,
known registration markers, and file hashes without printing configuration
contents. It is a disk inventory, not proof that no running shell, tmux hook,
watcher, scheduled job, plugin cache, or remote device still uses an old path.
Those require explicit runtime qualification. The report never disables old
commands or plugins. Keep the Anu checkout intact and preserve active sessions
until the separately approved migration proves the replacement works.

## Qualification

`python3 scripts/test-profiles.py` uses temporary homes, fake provider/SSH
executables, and a dedicated tmux socket. It covers ownership, repeat install,
rollback, user edits, symlink conflicts, installed helper independence, argument
preservation, manual mesh without Tailscale, SSH export, independent linked
client navigation, and basic topology snapshots. It never attaches to or kills
the user's default tmux server.

Actual Ghostty rendering, real provider resume/account handoff, Linux service
behavior, VNC, and cross-device access remain separate environment-dependent
acceptance checks. Do not report them as passed merely because source/import
or isolated tests succeeded.

`bash scripts/test-account-module.sh` runs the retained account/Codex/observer
fixtures with disposable state and blocked real integrations.
`python3 scripts/test-account-boundaries.py` checks installed account paths,
Claude hook merging, Codex hook execution paths, explicit containment, and the
minimal secrets client against fake providers/network/stores. Real quota-driven
handoff and private service access still need separate acceptance checks.

Where Ghostty is installed, qualify its actual parser against the release with
`python3 scripts/qualify-ghostty.py /path/to/homi-0.3.0`. This installs the artifact's
terminal profile in a temporary home, validates the generated include, checks the
loaded settings, and requires rejection of a deliberately invalid option. It opens
no window and checks neither rendering nor whether the requested font is available.
Missing Ghostty reports `unqualified`; this optional gate is separate from CI.
