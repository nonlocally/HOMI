#!/usr/bin/env bash
# End-to-end check of the communicate distribution: manifests valid, payload
# homi-free, tarball installable, CLI + MCP + setup work from the packed artifact.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$ROOT/packages/communicate"
fails=0
ok()   { echo "  ok  $*"; }
fail() { echo "  FAIL $*"; fails=$((fails+1)); }

echo "1) manifest + skill lint"
for f in plugins/.claude-plugin/marketplace.json plugins/communicate/.claude-plugin/plugin.json \
         plugins/communicate/.codex-plugin/plugin.json .agents/plugins/marketplace.json; do
  jq -e '.name=="communicate"' "$ROOT/$f" >/dev/null 2>&1 && ok "$f" || fail "$f invalid"
done
python3 - "$ROOT" <<'PY' && ok "SKILL.md frontmatter (name+description only, dir==name)" || fail "SKILL.md frontmatter"
import glob, re, sys
root = sys.argv[1]; bad = 0
for p in glob.glob(root + "/plugins/communicate/skills/*/SKILL.md"):
    d = p.split("/")[-2]
    m = re.match(r"^---\n(.*?)\n---\n", open(p).read(), re.S)
    keys = [l.split(":")[0].strip() for l in m.group(1).splitlines() if l and not l.startswith(" ")]
    name = re.search(r"^name:\s*(\S+)", m.group(1), re.M).group(1)
    if keys != ["name", "description"] or name != d: bad += 1
sys.exit(1 if bad else 0)
PY

echo "2) codex plugin validator (needs a python with yaml; skips if none)"
V="$HOME/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py"
PYYAML=""
for py in "${COMM_DIST_PY:-}" python3; do
  [ -n "$py" ] && "$py" -c "import yaml" >/dev/null 2>&1 && PYYAML="$py" && break
done
if [ -f "$V" ] && [ -n "$PYYAML" ]; then
  "$PYYAML" "$V" "$ROOT/plugins/communicate" >/dev/null 2>&1 && ok "validate_plugin passed" || fail "validate_plugin failed"
else
  echo "  skip (validator or pyyaml absent)"
fi

echo "3) vendor is homi-free"
( cd "$PKG" && node scripts/vendor.mjs >/dev/null ) || fail "vendor.mjs errored"
find "$PKG/vendor" \( -name 'homi*.py' -o -name 'homi.sh' \) | grep -q . && fail "homi artifact in vendor" || ok "no homi artifacts"

echo "4) npm pack -> install into temp prefix -> run"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
TARBALL="$(cd "$PKG" && npm pack --silent 2>/dev/null | tail -1)"
[ -f "$PKG/$TARBALL" ] && ok "packed $TARBALL" || { fail "npm pack"; exit 1; }
npm install --prefix "$TMP" --silent "$PKG/$TARBALL" >/dev/null 2>&1 || fail "npm install of tarball"
BIN="$TMP/node_modules/.bin/communicate"
"$BIN" version >/dev/null 2>&1 && ok "installed bin: version" || fail "installed bin: version"
"$BIN" agents >/dev/null 2>&1 && ok "installed bin: agents" || fail "installed bin: agents"

echo "5) setup --dry-run from the installed artifact writes nothing"
FH="$(mktemp -d)"
HOME="$FH" COMMUNICATE_DATA="$FH/data" "$BIN" setup --dry-run >/dev/null 2>&1 || fail "setup --dry-run errored"
[ -e "$FH/data" ] && fail "dry-run created data dir" || ok "dry-run inert"
rm -rf "$FH"

echo "6) unit smokes + codex-queue payload test"
( cd "$PKG" && node test/mcp-smoke.mjs >/dev/null 2>&1 ) && ok "mcp-smoke" || fail "mcp-smoke"
( cd "$PKG" && node test/setup-smoke.mjs >/dev/null 2>&1 ) && ok "setup-smoke" || fail "setup-smoke"
"$ROOT/scripts/test-codex-queue.sh" >/dev/null 2>&1 && ok "codex-queue" || fail "codex-queue"
"$ROOT/scripts/test-setup-repo.sh" >/dev/null 2>&1 && ok "setup-repo" || fail "setup-repo"
"$ROOT/scripts/test-ask.sh" >/dev/null 2>&1 && ok "ask+coach" || fail "ask+coach"
"$ROOT/scripts/test-roster-desc.sh" >/dev/null 2>&1 && ok "roster-desc" || fail "roster-desc"
"$ROOT/scripts/test-cards.sh" >/dev/null 2>&1 && ok "cards" || fail "cards"
rm -f "$PKG/$TARBALL"

[ "$fails" -eq 0 ] && echo "PASS: communicate distribution end to end" || { echo "FAIL: $fails check(s)"; exit 1; }
