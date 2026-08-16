#!/usr/bin/env bash
# Workspace axis: recorded always (path + git ref + branch), worktree opt-in.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-ws.XXXXXX)"
export COMM_STATE="$T/state" HOMI_SOCK_DIR="$T/socks" HOMI_SESSIONS_DIR="$T/sess"
export HOMI_SELF=wshost HOMI_TICK=1
mkdir -p "$HOMI_SESSIONS_DIR"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ "$COMM" homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT
ID="$COMM_STATE/homi/identities.json"

# a real git repo to point at
REPO="$T/repo"; mkdir -p "$REPO"; cd "$REPO"
git init -q .; git config user.email t@t; git config user.name t
echo hello > README.md; git add README.md
git -c commit.gpgsign=false commit -qm "first"
SHA="$(git rev-parse --short HEAD)"; BR="$(git rev-parse --abbrev-ref HEAD)"
cd "$HERE"

"$COMM" homi start >/dev/null 2>&1

echo "== a git workspace is recorded with ref + branch"
"$COMM" homi claim ws1 --cwd "$REPO" >/dev/null 2>&1
python3 - "$ID" "$REPO" "$SHA" "$BR" <<'PY' && ok "git workspace recorded (path/ref/branch)" || bad "git workspace record"
import json,sys,os
d=json.load(open(sys.argv[1]))["ws1"]["workspace"]
assert d and os.path.realpath(d["path"])==os.path.realpath(sys.argv[2]), d
assert d["ref"].startswith(sys.argv[3]), d
assert d["branch"]==sys.argv[4], d
assert d["worktree"] is False, d
PY

echo "== a non-git directory still records its path (no ref)"
mkdir -p "$T/plain"
"$COMM" homi claim ws2 --cwd "$T/plain" >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "non-git workspace records path, ref=None" || bad "non-git workspace"
import json,sys
d=json.load(open(sys.argv[1]))["ws2"]["workspace"]
assert d["path"].endswith("/plain"), d
assert d["ref"] is None and d["branch"] is None, d
PY

echo "== no --cwd means no workspace (never invented)"
"$COMM" homi claim ws3 >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "workspace stays null when not given" || bad "workspace invented"
import json,sys
assert json.load(open(sys.argv[1]))["ws3"]["workspace"] is None
PY

echo "== --worktree creates a real worktree on its own branch"
"$COMM" homi claim ws4 --cwd "$REPO" --worktree >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "worktree recorded with worktree:true" || bad "worktree record"
import json,sys,os
d=json.load(open(sys.argv[1]))["ws4"]["workspace"]
assert d["worktree"] is True, d
assert d["branch"]=="homi/ws4", d
assert os.path.isdir(d["path"]), d
PY
( cd "$REPO" && git worktree list | grep -q 'homi/ws4' ) && ok "git agrees the worktree exists" || bad "git worktree list"

echo "== a second agent on the same repo gets its own worktree"
"$COMM" homi claim ws5 --cwd "$REPO" --worktree >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "two worktree agents do not collide" || bad "worktree collision"
import json,sys
d=json.load(open(sys.argv[1]))
assert d["ws4"]["workspace"]["path"]!=d["ws5"]["workspace"]["path"]
assert d["ws5"]["workspace"]["branch"]=="homi/ws5"
PY

echo "== a removed worktree with its branch intact is re-adopted, not orphaned"
"$COMM" homi claim ws6 --cwd "$REPO" --worktree >/dev/null 2>&1
WS6_PATH="$(python3 - "$ID" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["ws6"]["workspace"]["path"])
PY
)"
"$COMM" homi release ws6 >/dev/null 2>&1
# `git worktree remove` is the normal recovery path -- it deliberately leaves
# the branch (homi/ws6) behind, only dropping the worktree's directory + the
# administrative link back to it.
( cd "$REPO" && git worktree remove --force "$WS6_PATH" ) >/dev/null 2>&1
( cd "$REPO" && git worktree list | grep -q 'homi/ws6' ) && bad "worktree remove didn't remove it" || ok "worktree entry removed, branch left behind"
"$COMM" homi claim ws6 --cwd "$REPO" --worktree >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "re-claim adopts homi/ws6 (branch survives remove + re-claim)" || bad "re-claim orphaned the original branch"
import json,sys,os
d=json.load(open(sys.argv[1]))["ws6"]["workspace"]
assert d["worktree"] is True, d
assert d["branch"]=="homi/ws6", d
assert os.path.isdir(d["path"]), d
PY
( cd "$REPO" && git branch --list ws6 | grep -q . ) && bad "stray branch 'ws6' was created" || ok "no stray branch named ws6"
( cd "$REPO" && git worktree list | grep -q 'homi/ws6' ) && ok "git agrees homi/ws6 worktree exists again" || bad "worktree not recreated"

echo "== re-claiming an already-claimed name must not litter a second repo"
REPO2="$T/repo2"; mkdir -p "$REPO2"
( cd "$REPO2" && git init -q . && git config user.email t@t && git config user.name t \
  && echo hi > f.txt && git add f.txt && git -c commit.gpgsign=false commit -qm "first" ) >/dev/null 2>&1
"$COMM" homi claim ws7 --cwd "$REPO" --worktree >/dev/null 2>&1
WS7_BEFORE="$(python3 -c "import json;print(json.load(open('$ID'))['ws7']['workspace']['path'])")"
# ws7 is already claimed; a repeat --worktree claim against a DIFFERENT repo
# must be a true no-op -- no branch, no worktree directory, no change to the
# already-recorded workspace (before the fix this ran `git worktree add`
# against REPO2 and then discarded the result on the "already claimed" path).
"$COMM" homi claim ws7 --cwd "$REPO2" --worktree >/dev/null 2>&1
python3 - "$ID" "$WS7_BEFORE" <<'PY' && ok "re-claim against a second repo left the recorded workspace untouched" || bad "re-claim overwrote workspace"
import json,sys
d=json.load(open(sys.argv[1]))["ws7"]["workspace"]
assert d["path"]==sys.argv[2], d
assert d["worktree"] is True, d
assert d["branch"]=="homi/ws7", d
PY
( cd "$REPO2" && git branch --list 'homi/ws7' | grep -q . ) && bad "re-claim created a stray homi/ws7 branch in the SECOND repo" || ok "no stray branch in the second repo"
[ -d "$T/repo2-worktrees/ws7" ] && bad "re-claim created a stray worktree dir in the SECOND repo" || ok "no stray worktree directory in the second repo"

"$COMM" homi stop >/dev/null 2>&1
echo; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
