# Optional workspace snapshots and archive

`homi-snapshot` takes a snapshot of the whole tmux workspace with the profile's
`tss`, mirrors it to an archive volume, prunes the local window, and logs one
line per run. It is an optional module of the workstation profile: nothing in
core setup or in `homi profile install` takes snapshots or installs a schedule.

```sh
homi-snapshot run              # snapshot now (snap-YYYY-MM-DD-HHMM) + archive + prune
homi-snapshot list             # local snapshots, newest first (* = archived)
homi-snapshot restore 2026-09-24-0300   # a verified copy back from the archive
homi-snapshot status           # schedule, archive state, counts, recent log
homi-snapshot schedule preview # the owned 03/09/15/21 schedule, before installing it
```

The module lives at `profiles/runtime/modules/snapshots/` (`homi-snapshot` and
`schedule.py`), so an installed profile payload carries it. Until the profile
installer wires a wrapper, run it from the payload or the source tree, or let
`homi-snapshot schedule install` write the stable `~/.local/bin/homi-snapshot`.

## Format, naming, retention

Snapshots are the `tss` directories (`meta.tsv`, layouts, resume commands) under
the sessions directory, named `snap-YYYY-MM-DD-HHMM`. Snapshots from the earlier
nightly cadence are named `nightly-YYYY-MM-DD`; every command finds, orders,
archives, prunes, verifies, lists and restores both schemes. Ordering uses the
embedded timestamp, never the bare name. The local window keeps the newest 56
snapshots (14 days at four a day). Restore with `tsr NAME` (`homi-workstation
shell tsr NAME` outside a profile shell).

## Archive safety

Each archived snapshot carries a `.manifest.sha256` written when it was
mirrored, and the local copy is marked `.archived` only after the mirrored
files verify against it. Two rules are stricter than the Anu original:

- **Prune deletes a local copy only when the archive volume is mounted right
  now and holds a copy that verifies against its manifest, whose manifest the
  local files also satisfy, with exactly the same file set.** A `.archived`
  marker alone, an unmounted or absent volume, a differing archive copy, or a
  local copy edited after archiving all keep the snapshot. Nothing is ever
  deleted from the archive.
- **Restore refuses an archived copy that fails its checksum** (or has no
  manifest). Nothing is written to the sessions directory; a good copy is
  restored through a temporary directory, verified, marked archived, and only
  then given its final name.
- **A manifest is an exact inventory.** Verification requires every listed
  path to be a safe relative path inside the snapshot, every listed file to
  match, the regular files present (marker and manifest aside) to be exactly
  the listing, and no symlink or special entry anywhere in the copy. A local
  snapshot holding a symlink is never marked archived and never pruned; an
  archive copy with an extra, special or traversing entry is refused for
  restore and reported corrupt by `verify`. Existing text-mode and binary-mode
  shasum manifests keep verifying.
- **The archive configuration itself is checked** before anything is mirrored,
  pruned, restored or verified: the volume must be exactly a mount point (the
  whole mount-point field, never a prefix or a pattern), the archive directory
  must resolve inside that volume, and the archive and the sessions directory
  must be disjoint — neither the same directory nor an ancestor or descendant
  of the other, with symlinks resolved through every existing ancestor. A
  configuration that fails is refused with the reason and deletes nothing.

Without a configured archive volume every snapshot stays local and the log
says so; nothing is pruned.

## Configuration

Private settings belong in `~/.config/homi/profiles/local.sh` (trusted shell,
loaded by every profile tool and by the scheduled run):

```sh
# where tss saves; snapshots are <state>/sessions, the log <state>/snapshots.log
export HOMI_PROFILE_STATE="$HOME/.local/state/homi/workstation"
# the permanent archive: a mounted volume and a directory on it
export HOMI_SNAPSHOT_VOL="/Volumes/YOUR-ARCHIVE"
export HOMI_SNAPSHOT_ARCHIVE="$HOMI_SNAPSHOT_VOL/homi-snapshots"
# optional overrides
# export HOMI_SNAPSHOT_DIR="$HOMI_PROFILE_STATE/sessions"
# export HOMI_SNAPSHOT_LOG="$HOMI_PROFILE_STATE/snapshots.log"
# export HOMI_SNAPSHOT_KEEP=56
```

An existing Anu installation keeps its data in place by pointing
`HOMI_PROFILE_STATE` at the directory that already holds `sessions/` and
`snapshots.log`, and `HOMI_SNAPSHOT_ARCHIVE` at the existing archive
directory. Public defaults name no volume, host, or account.

## The schedule: explicit, owned, reversible

```sh
homi-snapshot schedule preview     # JSON: files, actions, the load command; writes nothing
homi-snapshot schedule install     # write + load; takes no snapshot itself
homi-snapshot schedule status      # loaded? which files are owned, edited, or missing
homi-snapshot schedule uninstall   # unload, then remove only unedited owned files
```

On macOS this is a one-shot LaunchAgent `com.communicate.homi.snapshots`
(`StartCalendarInterval` at 03:00, 09:00, 15:00 and 21:00, no `KeepAlive`, a
missed firing runs once on the next wake; logs in `~/Library/Logs`). On Linux it
is the user timer `communicate-homi-snapshots.timer` (`OnCalendar=*-*-*
03,09,15,21:00:00`, `Persistent=true`). Both run the owned wrapper
`~/.local/bin/homi-snapshot`.

Ownership boundaries:

- That compatible label is used only for the actual account home (from the
  password database, not `$HOME`) with standard config and state roots. Any
  other home, or an `XDG_CONFIG_HOME`/`XDG_STATE_HOME` override, gets a
  stable scoped label (`com.communicate.homi.snapshots.<tag>`), so an isolated
  or qualification installation can never reach the account's real job.
- Before anything is loaded or unloaded, the service manager is asked which
  file it loaded the label from. A job of our label loaded from another file
  is a collision: install, update and uninstall refuse and change nothing.
- An uninstall that finds no schedule-owned ledger entries is inert and never
  calls the service manager.
- An update that fails to activate restores the previous files and loads the
  previous schedule again when one was loaded; an identical owned schedule
  that is already loaded is neither rewritten nor restarted.
- `--platform` renders either platform for `preview`, but `install` and
  `uninstall` only drive the host's own manager unless an explicit
  `--manager PATH` fixture is given (that is how the tests run a fake
  launchd). On Linux, ownership of the unit files, enablement and activity are
  reported separately, and only this timer is enabled or disabled.
- A wrapper the profile installer owns is used as shared: never rewritten,
  retagged or removed here. Only a wrapper this schedule created is removed.

The files are owned through the profile ledger
(`~/.local/state/homi/profiles/ownership.json`) using the installer's own
conflict rules: an existing unowned file is never replaced, an owned file that
was edited is never removed, and `homi profile status` lists them. Run
`homi-snapshot schedule uninstall` before `homi profile uninstall`, which
removes owned files but never talks to a service manager. The service manager
is invoked only by these two verbs. `--home PATH --runtime PATH --platform`
select an isolated home, a runtime payload, and a rendering for qualification.

## Qualification

`bash scripts/test-snapshots.sh` runs under temporary homes with a fake
`mount`, `tmux` and a fake snapshotter first on PATH and one global fake
launchd named as an explicit `--manager` fixture: ordering across both naming
schemes, token resolution, both safety rules against a fixture archive
(unmounted volume, marker without a copy, differing archive bytes, locally
edited copy, corrupt restore), refused archive configurations (same or nested
directories, off-volume, symlink overlap, a longer volume name), symlink and
inventory rules, the unattended run, the retention default, and the schedule
across two isolated homes: scoped and default labels, install, identical-repeat
without restart, uninstall of one home leaving the other loaded, an inert
uninstall with nothing owned, a foreign loaded path refused, a failed
reactivation restoring files and the previous job, a shared profile-owned
wrapper retained, edited-file and unowned-file refusals, and a non-native
platform refused for mutation. It never touches real snapshots, a real volume,
a tmux server, or a service manager. Real launchd firing, a real external
volume, and Linux timers remain environment-specific checks.
