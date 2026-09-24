# Installation

Version 0.3.0 is a release candidate until qualification is complete. Do not treat
an unpublished tap or private archive as a completed public installation channel.

## Homebrew

The intended public command is `brew install nonlocally/tap/homi`. Its formula uses
a versioned SHA-256-verified archive and declares core runtime dependencies. Formula
installation does not activate plugins/services or replace terminal configuration.

Run `homi setup --claude --codex`, selecting the clients you use. Add `--service`
for daemon persistence. `--no-clients` supports CLI/service use without plugins.
Use `--dry-run` to inspect setup and `homi doctor` to check actual installed paths.

## Runtime archive

Download `homi-VERSION.tar.gz` and its checksum from the matching GitHub release.
Verify the SHA-256 before extracting. The archive includes production Node
modules; supply Node20+, Python3, and Bash. Run the extracted `bin/homi setup` to
create a stable per-user installation and enable selected integrations. Follow its
PATH guidance for a new shell. A source checkout or temporary npm cache is not
required after installation.

## Optional capabilities

tmux is needed for terminal seats. The terminal/mesh profile may need fzf and jq.
Ghostty and Tailscale are optional; manual SSH hosts do not require Tailscale.
Install model clients and authentication separately. Preview optional config with
`homi profile preview --terminal --mesh`. Private overrides stay outside packages;
core upgrades do not silently update the workstation profile.

## Upgrade and removal

Obtain the new version through the installation channel, then run its setup/update
operation to activate selected integrations. A Homebrew upgrade downloads the new
package; `homi update` activates it. Runtime commands continue using the installed
active release until activation succeeds, and follow that release after rollback.
Setup/update and doctor run from the invoked package so a newer download can
activate or diagnose an older installation. Source checkouts execute their own code.
Keep the previous payload available
until acceptance. Rollback preserves user data and restores the former payload.

Ordinary uninstall removes only owned integration objects and preserves state.
Package and profile removal are separate. Never run an old Anu/Communicate
uninstaller blindly after replacement paths or plugin identifiers are installed.

Migration requires an ownership/backup record. Verify executable, plugin and daemon
paths in a fresh client: falling back to an old checkout does not qualify the new
installation. Disable conflicting legacy integration reversibly only on designated
test machines, preserving their configuration and active work.
