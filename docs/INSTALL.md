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
modules; supply Node20+, Python3.9+, and Bash. Run the extracted `bin/homi setup` to
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

Codex setup records the previous local marketplace, installed/enabled state and
the exact user configuration for `communicate@communicate` in its private install
ledger. It retains custom tool policies while enabling the new installation, and
restores the first recorded original on uninstall. Updates and rollback preserve
that original. Later user edits to the owned plugin table cause setup, rollback
or uninstall to refuse replacement until the settings are reconciled; they are
never silently overwritten.

This requires Codex's installed/enabled JSON listing and versioned configuration
API, verified with Codex CLI 0.156.1. No model request is made during installation.
Unsupported clients, nonlocal prior marketplaces and plugin settings supplied by
another profile or managed layer fail preflight before registration is replaced.
An older install ledger without original plugin-state evidence cannot safely
infer it: preserve the current settings and restore/manage that registration
manually before retrying. `homi doctor` distinguishes missing, installed-disabled
and installed-enabled plugins.

Keep client configuration unchanged while setup, rollback or uninstall runs.
Configuration API writes check their expected version, but the clients' separate
plugin registration commands do not provide an atomic transaction with concurrent
edits made by another process.
