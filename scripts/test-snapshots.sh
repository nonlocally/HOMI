#!/usr/bin/env bash
# Optional snapshot/archive module: cadence naming across both schemes, the
# archive gate, the two safety rules (a local copy is deleted only after a
# mounted archive holds byte-identical verified files; a corrupt archived
# snapshot is never restored), unattended runs with a fake snapshotter, and the
# explicit owned schedule against a fake launchctl. Everything runs under a
# temporary HOME with fake mount/launchctl/tmux first on PATH: no real
# snapshots, drive, tmux server or service manager is touched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOD="$HERE/profiles/runtime/modules/snapshots"
SNAP="$MOD/homi-snapshot"
SCHED="$MOD/schedule.py"
T="$(mktemp -d /tmp/homi-snap.XXXXXX)"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { rm -rf "$T"; }
trap cleanup EXIT

FAKEHOME="$T/home with spaces"; mkdir -p "$FAKEHOME"
FAKEBIN="$T/bin"; mkdir -p "$FAKEBIN"
VOL="$T/vol"; ARCH="$VOL/homi-snapshots"; mkdir -p "$VOL"
LOGF="$T/calls.log"
cat > "$FAKEBIN/mount" <<EOF
#!/usr/bin/env bash
# the archive volume is "mounted" only while \$VOL/.mounted exists
[ -e "$VOL/.mounted" ] && printf '/dev/disk9s1 on %s (apfs, local)\n' "$VOL"
printf '/dev/disk1s1 on / (apfs, local)\n'
EOF
cat > "$FAKEBIN/launchctl" <<EOF
#!/usr/bin/env bash
printf 'launchctl %s\n' "\$*" >> "$LOGF"
case "\$1" in
  bootstrap) touch "$T/loaded" ;;
  bootout)   rm -f "$T/loaded" ;;
  print)     [ -e "$T/loaded" ] || exit 113 ;;
esac
exit 0
EOF
cat > "$FAKEBIN/tmux" <<'EOF'
#!/usr/bin/env bash
[ "$1" = info ] && exit 0
exit 1
EOF
chmod +x "$FAKEBIN"/*
export PATH="$FAKEBIN:$PATH"

# The CLI against a fixture tree. $1 = sessions dir; archive at $ARCH on $VOL.
_cli() { # <sess_dir> [args...]
  local sess="$1"; shift
  HOME="$FAKEHOME" HOMI_SNAPSHOT_DIR="$sess" HOMI_SNAPSHOT_LOG="$sess/../snapshots.log" \
    HOMI_SNAPSHOT_VOL="$VOL" HOMI_SNAPSHOT_ARCHIVE="$ARCH" bash "$SNAP" "$@" 2>&1
}
# A helper from the sourced script, in a subshell (it sets -u/pipefail).
_call() { # <sess_dir> <fn> [args...]
  local sess="$1"; shift
  ( set +u; export HOME="$FAKEHOME" HOMI_SNAPSHOT_DIR="$sess" HOMI_SNAPSHOT_LOG="$sess/../snapshots.log" \
      HOMI_SNAPSHOT_VOL="$VOL" HOMI_SNAPSHOT_ARCHIVE="$ARCH"
    source "$SNAP" >/dev/null 2>&1
    "$@" )
}
_names() { while read -r d; do basename "$d"; done | tr '\n' ' '; }
_mkfix() { # <sess_dir> <name> [archived]
  mkdir -p "$1/$2"
  printf 'saved\t%s\nsessions\t1\nwindows\t2\npanes\t3\nagents\t3\n' "$2" > "$1/$2/meta.tsv"
  printf 'window\t%s\tpane\n' "$2" > "$1/$2/layout.tsv"
  [ "${3:-}" = archived ] && printf 'yes\n' > "$1/$2/.archived"
  return 0
}
_mkarch() { # <sess_dir> <name>: a verified archive copy of the local snapshot
  mkdir -p "$ARCH/$2"
  cp "$1/$2"/*.tsv "$ARCH/$2/"
  ( cd "$ARCH/$2" && find . -type f ! -name '.manifest.sha256' -print0 | sort -z | xargs -0 shasum -a 256 > .manifest.sha256 )
}
mounted()   { touch "$VOL/.mounted"; }
unmounted() { rm -f "$VOL/.mounted"; }

echo "== module files"
[ -x "$SNAP" ] && ok "homi-snapshot is executable" || bad "homi-snapshot executable present at $SNAP"
[ -f "$SCHED" ] && ok "schedule.py present" || bad "schedule.py present"
if bash -n "$SNAP" 2>/dev/null; then ok "homi-snapshot parses"; else bad "homi-snapshot parses"; fi

echo "== ordering is chronological across both naming schemes"
S1="$T/s1/sessions"; mkdir -p "$S1"
_mkfix "$S1" snap-2026-08-21-0900; _mkfix "$S1" nightly-2026-08-22; _mkfix "$S1" snap-2026-08-21-2100; _mkfix "$S1" snap-2026-08-23-0300
got="$(_call "$S1" _snap_dirs "$S1" | _names)"
[ "$got" = "snap-2026-08-21-0900 snap-2026-08-21-2100 nightly-2026-08-22 snap-2026-08-23-0300 " ] && ok "oldest-first interleaves legacy nights by date" || bad "oldest-first ordering (got: $got)"
got="$(_call "$S1" _snap_dirs "$S1" -r | _names)"
[ "$got" = "snap-2026-08-23-0300 nightly-2026-08-22 snap-2026-08-21-2100 snap-2026-08-21-0900 " ] && ok "-r reverses to newest-first" || bad "-r ordering (got: $got)"
S2="$T/s2/sessions"; mkdir -p "$S2"
_mkfix "$S2" nightly-2026-08-22; _mkfix "$S2" snap-2026-08-22-0015; _mkfix "$S2" snap-2026-08-22-0900
got="$(_call "$S2" _snap_dirs "$S2" | _names)"
[ "$got" = "snap-2026-08-22-0015 nightly-2026-08-22 snap-2026-08-22-0900 " ] && ok "a legacy night orders as 03:00 within its day" || bad "legacy night ordering (got: $got)"
S3="$T/s3/sessions"; mkdir -p "$S3"
_mkfix "$S3" snap-2026-08-23-0300; _mkfix "$S3" default; _mkfix "$S3" workspace-2026-07-26; _mkfix "$S3" snap-2026-08-23
got="$(_call "$S3" _snap_dirs "$S3" | _names)"
[ "$got" = "snap-2026-08-23-0300 " ] && ok "named workspaces and malformed names are left alone" || bad "name filtering (got: $got)"

echo "== prune deletes a local copy only after a MOUNTED archive holds verified, byte-identical files"
S4="$T/s4/sessions"; mkdir -p "$S4"; rm -rf "$ARCH"; mkdir -p "$ARCH"
_mkfix "$S4" nightly-2026-08-20 archived;  _mkarch "$S4" nightly-2026-08-20
_mkfix "$S4" snap-2026-08-21-0300 archived; _mkarch "$S4" snap-2026-08-21-0300
_mkfix "$S4" snap-2026-08-21-2100                                   # marked nothing, never archived
_mkfix "$S4" snap-2026-08-22-0300 archived                          # marker but NO archive copy
_mkfix "$S4" snap-2026-08-22-0900 archived; _mkarch "$S4" snap-2026-08-22-0900
printf 'tampered\n' >> "$ARCH/snap-2026-08-22-0900/layout.tsv"      # archive bytes differ from local
_mkfix "$S4" snap-2026-08-22-1500 archived; _mkarch "$S4" snap-2026-08-22-1500
printf 'edited locally\n' >> "$S4/snap-2026-08-22-1500/layout.tsv"   # local bytes differ from archive
_mkfix "$S4" snap-2026-08-23-0300 archived; _mkarch "$S4" snap-2026-08-23-0300
unmounted
out="$(_cli "$S4" prune 1)"
[ -d "$S4/nightly-2026-08-20" ] && [ -d "$S4/snap-2026-08-21-0300" ] && ok "archive not mounted: nothing deleted, whatever the markers say" || bad "unmounted prune deleted a local copy (out: $out)"
printf '%s' "$out" | grep -q "not mounted" && ok "…and the report says the archive is not mounted" || bad "unmounted prune report (out: $out)"
mounted
out="$(_cli "$S4" prune 1)"
[ ! -d "$S4/nightly-2026-08-20" ] && [ ! -d "$S4/snap-2026-08-21-0300" ] && ok "verified-identical archived copies: the two oldest are deleted" || bad "verified copies not pruned (out: $out)"
[ -d "$S4/snap-2026-08-21-2100" ] && ok "never-archived snapshot survives" || bad "never-archived snapshot deleted"
[ -d "$S4/snap-2026-08-22-0300" ] && ok "a .archived marker with no archive copy does not permit deletion" || bad "marker-only snapshot deleted"
[ -d "$S4/snap-2026-08-22-0900" ] && ok "an archive copy whose bytes differ does not permit deletion" || bad "differing-archive snapshot deleted"
[ -d "$S4/snap-2026-08-22-1500" ] && ok "a local copy edited after archiving does not permit deletion" || bad "locally-edited snapshot deleted"
[ -d "$S4/snap-2026-08-23-0300" ] && ok "newest snapshot survives" || bad "newest snapshot deleted"
printf '%s' "$out" | grep -q "removed 2" && ok "report counts the 2 removed" || bad "removed count (out: $out)"
printf '%s' "$out" | grep -q "4 not-yet-verified" && ok "report counts the 4 kept for lack of a verified archive copy" || bad "kept count (out: $out)"
[ -d "$ARCH/nightly-2026-08-20" ] && ok "nothing is ever deleted from the archive" || bad "archive copy deleted"

echo "== restore refuses a corrupt archived snapshot and leaves the destination unchanged"
S5="$T/s5/sessions"; mkdir -p "$S5"; rm -rf "$ARCH"; mkdir -p "$ARCH"
_mkfix "$T/s5/stage" snap-2026-08-24-0300; _mkarch "$T/s5/stage" snap-2026-08-24-0300
_mkfix "$T/s5/stage" snap-2026-08-25-0300; _mkarch "$T/s5/stage" snap-2026-08-25-0300
printf 'bitrot\n' >> "$ARCH/snap-2026-08-25-0300/meta.tsv"
mounted
out="$(_cli "$S5" restore 2026-08-25-0300)"; rc=$?
[ $rc -ne 0 ] && ok "corrupt archive: restore exits nonzero" || bad "corrupt restore exited 0"
printf '%s' "$out" | grep -qi "refus" && ok "…and says it refused" || bad "refusal message (out: $out)"
[ ! -e "$S5/snap-2026-08-25-0300" ] && [ -z "$(ls -A "$S5")" ] && ok "…and wrote nothing into the sessions dir, not even a partial" || bad "corrupt restore left files: $(ls -A "$S5")"
out="$(_cli "$S5" restore 2026-08-24-0300)"; rc=$?
[ $rc -eq 0 ] && [ -f "$S5/snap-2026-08-24-0300/meta.tsv" ] && ok "a verified archive restores" || bad "verified restore (rc=$rc out: $out)"
printf '%s' "$out" | grep -q "tsr snap-2026-08-24-0300" && ok "…naming the tsr command" || bad "tsr hint (out: $out)"
[ -f "$S5/snap-2026-08-24-0300/.archived" ] && ok "…and the restored copy is marked archived (it is verified on the archive)" || bad "restored copy not marked archived"
unmounted
out="$(_cli "$S5" restore 2026-08-24-0300)"
printf '%s' "$out" | grep -q "already present locally" && ok "a local snapshot restores without the archive" || bad "local restore (out: $out)"
out="$(_cli "$S5" restore 2026-08-25-0300)"; rc=$?
[ $rc -ne 0 ] && printf '%s' "$out" | grep -q "not mounted" && ok "restore from an unmounted archive is refused with a reason" || bad "unmounted restore (rc=$rc out: $out)"

echo "== restore resolves every token form"
S6="$T/s6/sessions"; mkdir -p "$S6"
_mkfix "$S6" nightly-2026-08-22; _mkfix "$S6" snap-2026-08-23-0300; _mkfix "$S6" snap-2026-08-23-2100
[ "$(_call "$S6" _resolve_snap "$S6" snap-2026-08-23-0300)" = "snap-2026-08-23-0300" ] && ok "full current-scheme name passes through" || bad "full name"
[ "$(_call "$S6" _resolve_snap "$S6" 2026-08-23-0300)" = "snap-2026-08-23-0300" ] && ok "YYYY-MM-DD-HHMM gains the snap- prefix" || bad "date-time token"
[ "$(_call "$S6" _resolve_snap "$S6" 2026-08-22)" = "nightly-2026-08-22" ] && ok "a bare date finds the day's single legacy night" || bad "bare date legacy"
out="$(_call "$S6" _resolve_snap "$S6" 2026-08-23 2>&1)"; rc=$?
[ $rc -ne 0 ] && printf '%s' "$out" | grep -q ambiguous && ok "a bare date with several snapshots is refused as ambiguous" || bad "ambiguous date (rc=$rc out: $out)"
_call "$S6" _resolve_snap "$S6" ../etc >/dev/null 2>&1 && bad "a path token is accepted" || ok "a non-date, non-name token is rejected"

echo "== list, status and retention"
out="$(_cli "$S4" list)"
[ "$(printf '%s\n' "$out" | head -1 | awk '{print $2}')" = "snap-2026-08-23-0300" ] && ok "list is newest-first" || bad "list order (out: $out)"
printf '%s' "$out" | grep -q '^\* snap-2026-08-23-0300' && ok "list marks archived snapshots" || bad "archived mark (out: $out)"
out="$(_cli "$S4" status)"
printf '%s' "$out" | grep -q "every 6h at 03:00, 09:00, 15:00, 21:00" && ok "status states the cadence" || bad "cadence text (out: $out)"
printf '%s' "$out" | grep -q "keep newest 56 snapshots" && ok "KEEP defaults to 56 (14 days at 4/day)" || bad "keep default (out: $out)"
out="$(HOMI_SNAPSHOT_KEEP=8 _cli "$S4" status)"
printf '%s' "$out" | grep -q "keep newest 8 snapshots" && ok "HOMI_SNAPSHOT_KEEP overrides" || bad "keep override (out: $out)"
printf '%s' "$out" | grep -q "com.communicate.homi.snapshots" && ok "status names the HOMI schedule label" || bad "label (out: $out)"
out="$(_cli "$S4" bogus)"; rc=$?
[ $rc -eq 2 ] && ok "unknown command exits 2" || bad "unknown command rc=$rc"

echo "== an unattended run: fake snapshotter, no tmux server needed"
S7="$T/s7/sessions"; mkdir -p "$S7"
cat > "$T/fake-fns" <<'EOF'
tss() { local d="${HOMI_SNAPSHOT_DIR}/$1"; mkdir -p "$d"; printf 'saved\tnow\nsessions\t2\nwindows\t3\npanes\t5\nagents\t1\n' > "$d/meta.tsv"; printf 'w\n' > "$d/layout.tsv"; }
EOF
out="$(HOMI_SNAPSHOT_FNS="$T/fake-fns" _cli "$S7" run snap-2026-09-01-0300)"
[ -f "$S7/snap-2026-09-01-0300/meta.tsv" ] && ok "run takes the named snapshot" || bad "run snapshot (out: $out)"
[ ! -e "$S7/snap-2026-09-01-0300.partial" ] && ok "…promoted atomically from its .partial" || bad "partial left behind"
grep -q 'OK     sessions=2 windows=3 panes=5 agents=1  -> snap-2026-09-01-0300' "$T/s7/snapshots.log" && ok "…and logged one aligned OK line" || bad "log line (log: $(cat "$T/s7/snapshots.log" 2>/dev/null))"
out="$(HOMI_SNAPSHOT_FNS="$T/fake-fns" HOMI_SNAPSHOT_VOL= HOMI_SNAPSHOT_ARCHIVE= HOME="$FAKEHOME" HOMI_SNAPSHOT_DIR="$S7" HOMI_SNAPSHOT_LOG="$T/s7/snapshots.log" bash "$SNAP" run snap-2026-09-01-0900 2>&1)"
printf '%s' "$out" | grep -q "no archive volume configured" && ok "with no archive configured the run says so and keeps everything" || bad "no-archive message (out: $out)"
[ -d "$S7/snap-2026-09-01-0300" ] && ok "…nothing pruned without an archive" || bad "pruned without archive"
out="$(HOMI_SNAPSHOT_FNS="$T/fake-fns" _cli "$S7" run 'bad name/../x')"; rc=$?
[ $rc -ne 0 ] && printf '%s' "$out" | grep -q "unsafe" && ok "an unsafe snapshot name is refused" || bad "unsafe name (rc=$rc out: $out)"
name="snap-$(date '+%Y-%m-%d-%H%M')"
printf '%s' "$name" | grep -Eq '^snap-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{4}$' && ok "the default name matches snap-YYYY-MM-DD-HHMM" || bad "default name shape"

echo "== the explicit owned schedule (fake launchctl; ownership through the profile ledger)"
export HOME="$FAKEHOME"
rm -f "$LOGF"   # the status cases above probed launchctl; the schedule section starts clean
sched() { python3 "$SCHED" "$@" --home "$FAKEHOME" --runtime "$HERE/profiles/runtime" 2>&1; }
PL="$FAKEHOME/Library/LaunchAgents/com.communicate.homi.snapshots.plist"
WRAP="$FAKEHOME/.local/bin/homi-snapshot"
LEDGER="$FAKEHOME/.local/state/homi/profiles/ownership.json"
out="$(sched preview --platform darwin)"; rc=$?
[ $rc -eq 0 ] && printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["read_only"] and d["label"]=="com.communicate.homi.snapshots"; assert [h for h in d["hours"]]==[3,9,15,21]' 2>/dev/null && ok "preview is JSON, read-only, and names the 03/09/15/21 cadence" || bad "preview (rc=$rc out: $out)"
[ ! -e "$PL" ] && [ ! -e "$WRAP" ] && [ ! -e "$LEDGER" ] && [ ! -s "$LOGF" ] && ok "preview wrote nothing and called no service manager" || bad "preview had side effects"
out="$(sched preview --platform linux)"
printf '%s' "$out" | grep -q 'OnCalendar=\*-\*-\* 03,09,15,21:00:00' && printf '%s' "$out" | grep -q 'Persistent=true' && ok "the Linux rendering is a persistent 03/09/15/21 timer" || bad "linux timer rendering (out: $out)"
out="$(sched install --platform darwin)"; rc=$?
[ $rc -eq 0 ] && [ -f "$PL" ] && ok "install writes the LaunchAgent under the selected HOME" || bad "install (rc=$rc out: $out)"
hours="$(plutil -extract StartCalendarInterval json -o - "$PL" 2>/dev/null | python3 -c 'import json,sys; print(" ".join(str(e["Hour"]) for e in json.load(sys.stdin)))' 2>/dev/null)"
[ "$hours" = "3 9 15 21" ] && ok "…StartCalendarInterval fires at 03/09/15/21 (no drifting StartInterval)" || bad "plist hours (got: $hours)"
grep -q '<key>StartInterval</key>' "$PL" && bad "plist uses StartInterval" || ok "…no StartInterval key"
grep -q 'KeepAlive\|RunAtLoad' "$PL" && bad "plist is a daemon, not a one-shot" || ok "…one-shot job: no KeepAlive/RunAtLoad"
[ -x "$WRAP" ] && grep -q 'modules/snapshots/homi-snapshot' "$WRAP" && ok "…and an owned wrapper at ~/.local/bin/homi-snapshot runs the payload script" || bad "wrapper"
grep -q "$WRAP" "$PL" && ok "…the job runs that wrapper" || bad "plist ProgramArguments"
grep -q 'launchctl bootstrap' "$LOGF" && ok "…and loads it explicitly through launchctl" || bad "bootstrap not called"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); e=d['entries']; assert sys.argv[2] in e and sys.argv[3] in e and e[sys.argv[2]]['kind']=='file'" "$LEDGER" "$PL" "$WRAP" 2>/dev/null && ok "both files are recorded in the profile ownership ledger" || bad "ledger entries"
out="$(sched status --platform darwin)"
printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["loaded"] is True' 2>/dev/null && ok "status reports the job loaded" || bad "status loaded (out: $out)"
before="$(cat "$PL")"; out="$(sched install --platform darwin)"; rc=$?
[ $rc -eq 0 ] && [ "$(cat "$PL")" = "$before" ] && ok "repeat install is idempotent" || bad "repeat install (rc=$rc out: $out)"
printf 'user edit\n' >> "$WRAP"
out="$(sched uninstall --platform darwin)"; rc=$?
[ $rc -ne 0 ] && [ -e "$WRAP" ] && ok "uninstall refuses to remove an owned file the user edited" || bad "edited-file refusal (rc=$rc out: $out)"
python3 - "$WRAP" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text(); p.write_text(t.replace("user edit\n", ""))
PY
out="$(sched uninstall --platform darwin)"; rc=$?
[ $rc -eq 0 ] && [ ! -e "$PL" ] && [ ! -e "$WRAP" ] && ok "uninstall removes both owned files" || bad "uninstall (rc=$rc out: $out)"
grep -q 'launchctl bootout' "$LOGF" && ok "…after unloading the job" || bad "bootout not called"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert sys.argv[2] not in d['entries']" "$LEDGER" "$PL" 2>/dev/null && ok "…and drops the ledger entries" || bad "ledger not cleaned"
mkdir -p "$(dirname "$PL")"; printf 'someone else\n' > "$PL"
out="$(sched install --platform darwin)"; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$PL")" = "someone else" ] && ok "install refuses to replace an unowned existing LaunchAgent" || bad "unowned plist refusal (rc=$rc out: $out)"
rm -f "$PL"
touch "$T/bootstrap-fails"
cat > "$FAKEBIN/launchctl" <<EOF
#!/usr/bin/env bash
printf 'launchctl %s\n' "\$*" >> "$LOGF"
case "\$1" in
  bootstrap) [ -e "$T/bootstrap-fails" ] && exit 5; touch "$T/loaded" ;;
  bootout)   rm -f "$T/loaded" ;;
  print)     [ -e "$T/loaded" ] || exit 113 ;;
esac
exit 0
EOF
out="$(sched install --platform darwin)"; rc=$?
[ $rc -ne 0 ] && [ ! -e "$PL" ] && [ ! -e "$WRAP" ] && ok "a load that fails leaves no files behind" || bad "failed load left files (rc=$rc out: $out)"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert sys.argv[2] not in d['entries'] and sys.argv[3] not in d['entries']" "$LEDGER" "$PL" "$WRAP" 2>/dev/null && ok "…and no ledger entries" || bad "failed load left ledger entries"
rm -f "$T/bootstrap-fails"
unset HOME

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
