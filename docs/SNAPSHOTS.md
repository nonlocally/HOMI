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

The files are owned through the profile ledger
(`~/.local/state/homi/profiles/ownership.json`) using the installer's own
conflict rules: an existing unowned file is never replaced, an owned file that
was edited is never removed, and `homi profile status` lists them. Run
`homi-snapshot schedule uninstall` before `homi profile uninstall`, which
removes owned files but never talks to a service manager. The service manager
is invoked only by these two verbs. `--home PATH --runtime PATH --platform`
select an isolated home, a runtime payload, and a rendering for qualification.

## Qualification

`bash scripts/test-snapshots.sh` runs under a temporary HOME with a fake
`mount`, `launchctl` and `tmux` first on PATH and a fake snapshotter: ordering
across both naming schemes, token resolution, both safety rules against a
fixture archive (unmounted volume, marker without a copy, differing archive
bytes, locally edited copy, corrupt restore), the unattended run, the retention
default, and the schedule's preview, install, idempotent repeat, edited-file
refusal, uninstall, and unowned-file refusal. It never touches real snapshots, a
real volume, a tmux server, or a service manager. Real launchd firing, a real
external volume, and Linux timers remain environment-specific checks.
