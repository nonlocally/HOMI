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

"$COMM" homi stop >/dev/null 2>&1
echo; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
