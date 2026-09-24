#!/usr/bin/env bash
# Optional snapshot/archive module: cadence naming across both schemes, the
# archive gate, the two safety rules (a local copy is deleted only after a
# mounted archive holds byte-identical verified files; a corrupt archived
# snapshot is never restored), unattended runs with a fake snapshotter, and the
# explicit owned schedule against a GLOBAL fake launchd shared by several
# isolated homes (label scoping, loaded-path ownership, inert uninstall,
# failed-reactivation restore, a wrapper shared with the profile). Everything
# runs under temporary homes with fake mount/launchctl/tmux first on PATH and
# the fake manager named explicitly: no real snapshots, drive, tmux server or
# service manager is touched.
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
LAUNCHD="$T/launchd"; mkdir -p "$LAUNCHD"     # the fake domain: one file per loaded label, holding its path
cat > "$FAKEBIN/mount" <<EOF
#!/usr/bin/env bash
# the archive volume is "mounted" only while \$VOL/.mounted exists
[ -e "$VOL/.mounted" ] && printf '/dev/disk9s1 on %s (apfs, local)\n' "$VOL"
[ -e "$T/vol two/.mounted" ] && printf '/dev/disk9s2 on %s (apfs, local)\n' "$T/vol two"
printf '/dev/disk1s1 on / (apfs, local)\n'
EOF
cat > "$FAKEBIN/launchctl" <<EOF
#!/usr/bin/env bash
# A GLOBAL fake launchd domain shared by every HOME in this suite, like the
# real per-user domain: label -> the path it was bootstrapped from.
D="$LAUNCHD"
printf 'launchctl %s\n' "\$*" >> "$LOGF"
case "\$1" in
  bootstrap)
    p="\$3"; l="\$(basename "\$p" .plist)"
    if [ -e "$T/bootstrap-fails" ]; then exit 5; fi
    if [ -e "$T/bootstrap-fails-once" ]; then rm -f "$T/bootstrap-fails-once"; exit 5; fi
    printf '%s\n' "\$p" > "\$D/\$l" ;;
  bootout)
    l="\${2##*/}"; rm -f "\$D/\$l" ;;
  print)
    l="\${2##*/}"; [ -e "\$D/\$l" ] || exit 113
    printf '%s = {\n\tpath = %s\n\tstate = waiting\n}\n' "\$l" "\$(cat "\$D/\$l")" ;;
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
grep -q 'sys.dont_write_bytecode = True' "$SCHED" && ok "schedule.py never writes bytecode into the payload" || bad "dont_write_bytecode missing"

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

echo "== an archive configuration that could make prune delete the only copy is refused"
S8="$T/s8/sessions"; mkdir -p "$S8"; rm -rf "$ARCH"; mkdir -p "$ARCH"; mounted
_mkfix "$S8" snap-2026-08-26-0300 archived; _mkfix "$S8" snap-2026-08-26-0900 archived; _mkfix "$S8" snap-2026-08-26-1500
_cfg() { # <archive> <sessions> [args...]: the CLI with an explicit archive/sessions pair
  local arch="$1" sess="$2"; shift 2
  HOME="$FAKEHOME" HOMI_SNAPSHOT_DIR="$sess" HOMI_SNAPSHOT_LOG="$T/s8/snapshots.log" HOMI_SNAPSHOT_VOL="$VOL" HOMI_SNAPSHOT_ARCHIVE="$arch" bash "$SNAP" "$@" 2>&1
}
out="$(_cfg "$S8" "$S8" prune 0)"
[ -d "$S8/snap-2026-08-26-0300" ] && [ -d "$S8/snap-2026-08-26-0900" ] && printf '%s' "$out" | grep -qi "refus" && ok "archive == sessions dir: prune deletes nothing and says the archive is refused" || bad "same-dir archive (out: $out)"
mkdir -p "$S8/archive"
out="$(_cfg "$S8/archive" "$S8" prune 0)"
[ -d "$S8/snap-2026-08-26-0300" ] && printf '%s' "$out" | grep -qi "refus" && ok "an archive inside the sessions dir is refused" || bad "nested archive (out: $out)"
mkdir -p "$T/elsewhere"
out="$(_cfg "$T/elsewhere" "$S8" prune 0)"
[ -d "$S8/snap-2026-08-26-0300" ] && printf '%s' "$out" | grep -qi "outside" && ok "an archive outside the mounted volume is refused" || bad "archive outside VOL (out: $out)"
out="$(_cfg "$T/elsewhere" "$S8" archive)"
[ ! -e "$S8/snap-2026-08-26-1500/.archived" ] && printf '%s' "$out" | grep -qi "refus" && ok "…and archive mirrors nothing there" || bad "archive outside VOL mirrored (out: $out)"
ln -s "$S8" "$VOL/sessions-link"
out="$(_cfg "$VOL/sessions-link" "$S8" prune 0)"
[ -d "$S8/snap-2026-08-26-0300" ] && printf '%s' "$out" | grep -qi "refus" && ok "a symlink on the volume pointing back at the sessions dir is refused" || bad "symlink overlap (out: $out)"
mkdir -p "$ARCH/sess"; _mkfix "$ARCH/sess" snap-2026-08-27-0300 archived; _mkarch "$ARCH/sess" snap-2026-08-27-0300
out="$(_cfg "$ARCH" "$ARCH/sess" prune 0)"
[ -d "$ARCH/sess/snap-2026-08-27-0300" ] && printf '%s' "$out" | grep -qi "refus" && ok "a sessions dir inside the archive is refused" || bad "sessions inside archive (out: $out)"
out="$(_cfg "$ARCH" "$S8" status)"
printf '%s' "$out" | grep -q "mounted at" && ok "a well-formed configuration reports the volume mounted" || bad "good config status (out: $out)"
unmounted; mkdir -p "$T/vol two"; touch "$T/vol two/.mounted"
out="$(_cfg "$ARCH" "$S8" status)"
printf '%s' "$out" | grep -q "not mounted" && ok "the mount check matches the exact mount path: a longer name is not this volume" || bad "mount prefix match (out: $out)"
rm -f "$T/vol two/.mounted"; mounted

echo "== a local symlink or special entry is never treated as archived"
S9="$T/s9/sessions"; mkdir -p "$S9"; rm -rf "$ARCH"; mkdir -p "$ARCH"; mounted
_mkfix "$S9" snap-2026-08-28-0300; ln -s meta.tsv "$S9/snap-2026-08-28-0300/latest"
out="$(_cli "$S9" archive)"
[ ! -e "$S9/snap-2026-08-28-0300/.archived" ] && printf '%s' "$out" | grep -q "archive FAILED" && ok "a snapshot holding a symlink is not marked archived" || bad "symlink snapshot archived (out: $out)"
out="$(_cli "$S9" prune 0)"
[ -d "$S9/snap-2026-08-28-0300" ] && ok "…and prune keeps it" || bad "symlink snapshot pruned"

echo "== an archive copy with extra, special or traversing entries is refused"
S10="$T/s10/sessions"; mkdir -p "$S10"; rm -rf "$ARCH"; mkdir -p "$ARCH"; mounted
_mkfix "$T/s10/stage" snap-2026-08-29-0300; _mkarch "$T/s10/stage" snap-2026-08-29-0300; printf 'extra\n' > "$ARCH/snap-2026-08-29-0300/extra.txt"
out="$(_cli "$S10" restore 2026-08-29-0300)"; rc=$?
[ $rc -ne 0 ] && [ -z "$(ls -A "$S10")" ] && ok "an archive copy with a file its manifest does not list is refused, destination untouched" || bad "extra file restore (rc=$rc out: $out; left: $(ls -A "$S10"))"
_mkfix "$T/s10/stage" snap-2026-08-29-0900; _mkarch "$T/s10/stage" snap-2026-08-29-0900
printf '%s  ./../../escape.txt\n' "$(shasum -a 256 "$T/s10/stage/snap-2026-08-29-0900/meta.tsv" | cut -c1-64)" >> "$ARCH/snap-2026-08-29-0900/.manifest.sha256"
out="$(_cli "$S10" restore 2026-08-29-0900)"; rc=$?
[ $rc -ne 0 ] && [ -z "$(ls -A "$S10")" ] && [ ! -e "$T/s10/escape.txt" ] && ok "a manifest that traverses out of the snapshot is refused" || bad "traversing manifest (rc=$rc out: $out)"
_mkfix "$T/s10/stage" snap-2026-08-29-1500; _mkarch "$T/s10/stage" snap-2026-08-29-1500; ln -s /etc/hosts "$ARCH/snap-2026-08-29-1500/hosts"
out="$(_cli "$S10" restore 2026-08-29-1500)"; rc=$?
[ $rc -ne 0 ] && [ -z "$(ls -A "$S10")" ] && ok "an archive copy holding a symlink is refused" || bad "symlink in archive (rc=$rc out: $out)"
_mkfix "$S10" snap-2026-08-30-0300 archived; _mkarch "$S10" snap-2026-08-30-0300; printf 'extra\n' > "$ARCH/snap-2026-08-30-0300/extra.txt"
_mkfix "$S10" snap-2026-08-30-0900
out="$(_cli "$S10" prune 1)"
[ -d "$S10/snap-2026-08-30-0300" ] && ok "prune keeps a local copy whose archive copy is not an exact inventory" || bad "prune deleted despite extra archive file (out: $out)"
out="$(_cli "$S10" verify)"; rc=$?
[ $rc -ne 0 ] && printf '%s' "$out" | grep -q "CORRUPT" && ok "verify reports those copies corrupt" || bad "verify (rc=$rc out: $out)"
_mkfix "$T/s10/stage" snap-2026-08-31-0300; mkdir -p "$ARCH/snap-2026-08-31-0300"; cp "$T/s10/stage/snap-2026-08-31-0300"/*.tsv "$ARCH/snap-2026-08-31-0300/"
( cd "$ARCH/snap-2026-08-31-0300" && { shasum -a 256 ./meta.tsv; shasum -a 256 -b ./layout.tsv; } > .manifest.sha256 )
out="$(_cli "$S10" restore 2026-08-31-0300)"; rc=$?
[ $rc -eq 0 ] && [ -f "$S10/snap-2026-08-31-0300/layout.tsv" ] && ok "existing text and binary-mode shasum manifests keep verifying" || bad "gnu manifest formats (rc=$rc out: $out)"

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
printf '%s' "$out" | grep -q "loaded:    no" && ok "status reports the (scoped) job not loaded" || bad "loaded line (out: $out)"
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

echo "== the explicit owned schedule: one GLOBAL fake launchd shared by every home, named as an explicit fixture"
rm -f "$LOGF"
ACCOUNT_HOME="$(python3 -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')"
HOME_A="$T/home-a"; HOME_B="$T/home-b"; HOME_C="$T/home-c"; HOME_E="$T/home-e"; HOME_F="$T/home-f"; HOME_G="$T/home-g"
mkdir -p "$HOME_A" "$HOME_B" "$HOME_C" "$HOME_E" "$HOME_F" "$HOME_G"
sched() { # <home> <verb> [args...]
  local h="$1"; shift
  python3 "$SCHED" "$@" --home "$h" --runtime "$HERE/profiles/runtime" --platform darwin --manager "$FAKEBIN/launchctl" 2>&1
}
jq_() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
label_of() { sched "$1" status | jq_ 'print(d["label"])'; }
plist_of() { printf '%s/Library/LaunchAgents/%s.plist' "$1" "$(label_of "$1")"; }
calls() { grep -c . "$LOGF" 2>/dev/null || echo 0; }

echo "-- labels: default only for the actual account home with standard roots; stable scoped labels elsewhere"
out="$(env -u XDG_CONFIG_HOME -u XDG_STATE_HOME python3 "$SCHED" preview --home "$ACCOUNT_HOME" --runtime "$HERE/profiles/runtime" --platform darwin 2>&1)"
printf '%s' "$out" | jq_ 'assert d["read_only"]; assert d["label"] == "com.communicate.homi.snapshots", d["label"]; assert d["scope"] == "default"' 2>/dev/null && ok "the account home with standard roots keeps the compatible default label (read-only preview)" || bad "default label for the account home (out: $out)"
LA="$(sched "$HOME_A" preview | jq_ 'print(d["label"])')"
case "$LA" in com.communicate.homi.snapshots.*) ok "an isolated home gets a scoped label ($LA)";; *) bad "scoped label (got: $LA)";; esac
[ "$(sched "$HOME_A" preview | jq_ 'print(d["label"])')" = "$LA" ] && ok "…which is stable across runs" || bad "label not stable"
LB="$(sched "$HOME_B" preview | jq_ 'print(d["label"])')"
[ "$LB" != "$LA" ] && ok "…and different for a different home" || bad "two homes share a label"
out="$(XDG_STATE_HOME="$T/xdg-state" python3 "$SCHED" preview --home "$ACCOUNT_HOME" --runtime "$HERE/profiles/runtime" --platform darwin 2>&1)"
printf '%s' "$out" | jq_ 'assert d["scope"] == "scoped" and d["label"] != "com.communicate.homi.snapshots"' 2>/dev/null && ok "a nonstandard state root scopes even the account home" || bad "XDG scope (out: $out)"
[ ! -s "$LOGF" ] && ok "preview called no service manager" || bad "preview called the manager"
out="$(HOME="$HOME_A" python3 "$SCHED" preview --runtime "$HERE/profiles/runtime" --platform darwin 2>&1)"
printf '%s' "$out" | jq_ "assert d['label'] == '$LA' and d['scope'] == 'scoped'" 2>/dev/null && ok "with no --home the selection follows \$HOME (the wrapper's case) and is scoped there" || bad "\$HOME default (out: $out)"
out="$(env -u HOME -u XDG_CONFIG_HOME -u XDG_STATE_HOME python3 "$SCHED" preview --runtime "$HERE/profiles/runtime" --platform darwin 2>&1)"
printf '%s' "$out" | jq_ 'assert d["label"] == "com.communicate.homi.snapshots" and d["scope"] == "default"' 2>/dev/null && ok "with no HOME at all the account home is selected (read-only preview)" || bad "no-HOME default (out: $out)"

echo "-- install/uninstall across two homes leave each other alone"
out="$(sched "$HOME_A" install)"; rc=$?
PA="$(plist_of "$HOME_A")"
[ $rc -eq 0 ] && [ -f "$PA" ] && [ "$(cat "$LAUNCHD/$LA")" = "$PA" ] && ok "A installs and the manager holds A's label from A's file" || bad "install A (rc=$rc out: $out)"
hours="$(plutil -extract StartCalendarInterval json -o - "$PA" 2>/dev/null | python3 -c 'import json,sys; print(" ".join(str(e["Hour"]) for e in json.load(sys.stdin)))' 2>/dev/null)"
[ "$hours" = "3 9 15 21" ] && ok "…StartCalendarInterval fires at 03/09/15/21" || bad "plist hours (got: $hours)"
grep -q 'KeepAlive\|RunAtLoad\|StartInterval' "$PA" && bad "plist is a daemon or drifting timer" || ok "…one-shot job: no KeepAlive/RunAtLoad/StartInterval"
WA="$HOME_A/.local/bin/homi-snapshot"
[ -x "$WA" ] && grep -q "$WA" "$PA" && ok "…the job runs A's owned wrapper" || bad "wrapper/ProgramArguments"
python3 -c "import json,sys; e=json.load(open(sys.argv[1]))['entries']; assert e[sys.argv[2]]['owner']=='snapshots-schedule' and e[sys.argv[3]]['owner']=='snapshots-schedule'" "$HOME_A/.local/state/homi/profiles/ownership.json" "$PA" "$WA" 2>/dev/null && ok "…both files are ledger entries tagged as schedule-owned" || bad "ledger tags"
out="$(sched "$HOME_B" install)"; rc=$?
[ $rc -eq 0 ] && [ -e "$LAUNCHD/$LB" ] && [ -e "$LAUNCHD/$LA" ] && ok "B installs beside A in the same domain" || bad "install B (rc=$rc out: $out)"
n="$(calls)"; out="$(sched "$HOME_A" install)"; rc=$?
[ $rc -eq 0 ] && printf '%s' "$out" | jq_ 'assert d["action"] == "unchanged" and d["restarted"] is False' 2>/dev/null && [ "$(calls)" -le $((n + 1)) ] && ok "an identical, owned, loaded schedule is not rewritten or restarted (one status query only)" || bad "idempotent A (rc=$rc calls: $n -> $(calls) out: $out)"
out="$(sched "$HOME_B" uninstall)"; rc=$?
[ $rc -eq 0 ] && [ ! -e "$LAUNCHD/$LB" ] && [ -e "$LAUNCHD/$LA" ] && [ -f "$PA" ] && ok "uninstalling B unloads only B; A stays loaded from its file" || bad "uninstall B (rc=$rc out: $out)"

echo "-- an uninstall with no schedule-owned entries is inert"
n="$(calls)"; out="$(sched "$HOME_C" uninstall)"; rc=$?
[ $rc -eq 0 ] && printf '%s' "$out" | jq_ 'assert d["inert"] is True and d["removed"] == []' 2>/dev/null && [ "$(calls)" -eq "$n" ] && ok "no owned entries: ok, inert, and the manager was never called" || bad "inert uninstall (rc=$rc calls $n -> $(calls) out: $out)"

echo "-- a job loaded from a path that is not ours is never touched"
printf '%s\n' "/elsewhere/$LA.plist" > "$LAUNCHD/$LA"
n="$(calls)"; out="$(sched "$HOME_A" status)"
printf '%s' "$out" | jq_ 'assert d["loaded"] is True and d["ours"] is False and d["loaded_path"].startswith("/elsewhere/")' 2>/dev/null && ok "status reports the label loaded from a foreign path" || bad "foreign status (out: $out)"
before="$(cat "$PA")"; out="$(sched "$HOME_A" uninstall)"; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$PA")" = "$before" ] && ! grep -q 'bootout' <(tail -n +$((n+1)) "$LOGF") && ok "uninstall refuses: no bootout, files kept" || bad "foreign uninstall (rc=$rc out: $out)"
cp -R "$HERE/profiles/runtime" "$T/runtime2"
out="$(python3 "$SCHED" install --home "$HOME_A" --runtime "$T/runtime2" --platform darwin --manager "$FAKEBIN/launchctl" 2>&1)"; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$PA")" = "$before" ] && ! grep -q 'bootout\|bootstrap' <(tail -n +$((n+1)) "$LOGF") && ok "an update refuses too: nothing written, nothing loaded" || bad "foreign update (rc=$rc out: $out)"
printf '%s\n' "$PA" > "$LAUNCHD/$LA"

echo "-- a failed reactivation restores the previous files AND the previous loaded job"
out="$(sched "$HOME_E" install)"; PE="$(plist_of "$HOME_E")"; LE="$(label_of "$HOME_E")"; WE="$HOME_E/.local/bin/homi-snapshot"
[ -e "$LAUNCHD/$LE" ] && ok "E installed and loaded" || bad "install E (out: $out)"
v1="$(cat "$WE")"; touch "$T/bootstrap-fails-once"; n="$(calls)"
out="$(python3 "$SCHED" install --home "$HOME_E" --runtime "$T/runtime2" --platform darwin --manager "$FAKEBIN/launchctl" 2>&1)"; rc=$?
[ $rc -ne 0 ] && ok "the update whose bootstrap fails exits nonzero" || bad "failed update rc=$rc"
[ "$(cat "$WE")" = "$v1" ] && ok "…the wrapper is back to its previous bytes" || bad "wrapper not restored"
python3 -c "import hashlib,json,sys; e=json.load(open(sys.argv[1]))['entries'][sys.argv[2]]; assert e['installed_hash']==hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest()" "$HOME_E/.local/state/homi/profiles/ownership.json" "$WE" 2>/dev/null && ok "…and the ledger hash matches the restored file" || bad "ledger hash after restore"
[ "$(cat "$LAUNCHD/$LE" 2>/dev/null)" = "$PE" ] && ok "…and the previous job is loaded again from our file" || bad "previous activation not restored"
grep -c 'bootstrap' <(tail -n +$((n+1)) "$LOGF") | grep -q '^2$' && ok "…one failed bootstrap, one restoring bootstrap" || bad "bootstrap sequence: $(tail -n +$((n+1)) "$LOGF" | tr '\n' ';')"
printf '%s' "$out" | grep -qi 'previous' && ok "…and the error says the previous schedule was put back" || bad "error text (out: $out)"

echo "-- a wrapper the profile installed is shared: used, never retagged, never removed by the schedule"
mkdir -p "$HOME_F/.local/bin" "$HOME_F/.local/state/homi/profiles"
WF="$HOME_F/.local/bin/homi-snapshot"
sched "$HOME_F" preview | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next(f["content"] for f in d["files"] if f["path"].endswith("/homi-snapshot")), end="")' > "$WF"; chmod 755 "$WF"
python3 - "$HOME_F/.local/state/homi/profiles/ownership.json" "$WF" <<'PY'
import hashlib, json, sys
p, w = sys.argv[1:3]
json.dump({"schema": 1, "entries": {w: {"kind": "file", "original": {"kind": "absent"},
          "installed_hash": hashlib.sha256(open(w, "rb").read()).hexdigest(), "block": None}}}, open(p, "w"), indent=2)
PY
out="$(sched "$HOME_F" install)"; rc=$?
[ $rc -eq 0 ] && printf '%s' "$out" | jq_ 'assert any(f["action"] == "shared" and f["path"].endswith("/homi-snapshot") for f in d["files"])' 2>/dev/null && ok "install uses the profile-owned wrapper as shared" || bad "shared wrapper install (rc=$rc out: $out)"
python3 -c "import json,sys; e=json.load(open(sys.argv[1]))['entries'][sys.argv[2]]; assert 'owner' not in e" "$HOME_F/.local/state/homi/profiles/ownership.json" "$WF" 2>/dev/null && ok "…without retagging its ledger entry" || bad "wrapper entry retagged"
out="$(sched "$HOME_F" uninstall)"; rc=$?
PF="$(plist_of "$HOME_F")"
[ $rc -eq 0 ] && [ ! -e "$PF" ] && [ -x "$WF" ] && ok "uninstall removes the schedule's plist and keeps the profile's wrapper" || bad "shared wrapper uninstall (rc=$rc out: $out)"
python3 -c "import json,sys; e=json.load(open(sys.argv[1]))['entries']; assert sys.argv[2] in e and 'owner' not in e[sys.argv[2]]" "$HOME_F/.local/state/homi/profiles/ownership.json" "$WF" 2>/dev/null && ok "…with its ledger metadata intact" || bad "wrapper ledger metadata lost"

echo "-- ownership rules and platform boundaries"
printf 'user edit\n' >> "$WA"
out="$(sched "$HOME_A" uninstall)"; rc=$?
[ $rc -ne 0 ] && [ -e "$WA" ] && [ -e "$LAUNCHD/$LA" ] && ok "uninstall refuses an edited owned file and unloads nothing" || bad "edited-file refusal (rc=$rc out: $out)"
python3 - "$WA" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); p.write_text(p.read_text().replace("user edit\n", ""))
PY
out="$(sched "$HOME_A" uninstall)"; rc=$?
[ $rc -eq 0 ] && [ ! -e "$PA" ] && [ ! -e "$WA" ] && [ ! -e "$LAUNCHD/$LA" ] && ok "uninstall A unloads and removes both owned files" || bad "uninstall A (rc=$rc out: $out)"
mkdir -p "$(dirname "$PA")"; printf 'someone else\n' > "$PA"
out="$(sched "$HOME_A" install)"; rc=$?
[ $rc -ne 0 ] && [ "$(cat "$PA")" = "someone else" ] && ok "install refuses to replace an unowned existing LaunchAgent" || bad "unowned plist refusal (rc=$rc out: $out)"
rm -f "$PA"
touch "$T/bootstrap-fails"; n="$(calls)"
out="$(sched "$HOME_G" install)"; rc=$?
PG="$HOME_G/Library/LaunchAgents/$(sched "$HOME_G" preview | jq_ 'print(d["label"])').plist"
[ $rc -ne 0 ] && [ ! -e "$PG" ] && [ ! -e "$HOME_G/.local/bin/homi-snapshot" ] && ok "a first install whose load fails leaves no files behind" || bad "failed first load left files (rc=$rc out: $out)"
python3 -c "import json,sys,os; p=sys.argv[1]; d=json.load(open(p)) if os.path.exists(p) else {'entries':{}}; assert not d['entries']" "$HOME_G/.local/state/homi/profiles/ownership.json" 2>/dev/null && ok "…and no ledger entries" || bad "failed load left ledger entries"
rm -f "$T/bootstrap-fails"
out="$(python3 "$SCHED" install --home "$HOME_G" --runtime "$HERE/profiles/runtime" --platform linux 2>&1)"; rc=$?
[ $rc -ne 0 ] && printf '%s' "$out" | grep -q -- '--manager' && ok "a non-native --platform cannot mutate without an explicit --manager fixture" || bad "non-native mutation (rc=$rc out: $out)"
out="$(python3 "$SCHED" preview --home "$HOME_G" --runtime "$HERE/profiles/runtime" --platform linux 2>&1)"
printf '%s' "$out" | grep -q 'OnCalendar=\*-\*-\* 03,09,15,21:00:00' && printf '%s' "$out" | grep -q 'Persistent=true' && ok "…while rendering the Linux persistent 03/09/15/21 timer is fine" || bad "linux timer rendering (out: $out)"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
