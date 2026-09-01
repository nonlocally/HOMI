#!/usr/bin/env bash
# Sandboxed test of `communicate setup-repo`: dry-run inert, merge-not-clobber
# with backup, marketplace points at THIS checkout, uninstall restores.
# Codex side is exercised via a stub codex on PATH that records its argv.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/bin/communicate"
FH="$(mktemp -d)"; trap 'rm -rf "$FH"' EXIT
mkdir -p "$FH/.claude" "$FH/bin"
SP="$FH/.claude/settings.json"
printf '{"sentinel":"keep-me","enabledPlugins":{"existing@mkt":true},"permissions":{"allow":["Read"]}}\n' > "$SP"
cat > "$FH/bin/codex" <<'STUB'
#!/usr/bin/env bash
echo "$@" >> "${CODEX_LOG:?}"
exit 0
STUB
chmod +x "$FH/bin/codex"
run() { HOME="$FH" PATH="$FH/bin:$PATH" CODEX_LOG="$FH/codex.log" "$CLI" setup-repo "$@" 2>&1; }
fails=0; ok(){ echo "  ok  $*"; }; fail(){ echo "  FAIL $*"; fails=$((fails+1)); }
jqs() { python3 -c "import json,sys;d=json.load(open('$SP'));print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

echo "1) dry-run writes nothing"
out="$(run --dry-run)"
echo "$out" | grep -q "\[dry-run\] would point marketplace" || fail "dry-run did not narrate claude"
echo "$out" | grep -q "\[dry-run\] would run: codex plugin marketplace add" || fail "dry-run did not narrate codex"
[ -z "$(jqs "d.get('extraKnownMarketplaces')")" ] || [ "$(jqs "d.get('extraKnownMarketplaces')")" = "None" ] && ok "settings untouched" || fail "dry-run edited settings"
[ -f "$FH/codex.log" ] && fail "dry-run invoked codex" || ok "codex not invoked"

echo "2) install registers this checkout"
run >/dev/null || fail "setup-repo errored"
[ "$(jqs "d['extraKnownMarketplaces']['communicate']['source']['path']")" = "$ROOT/plugins" ] && ok "marketplace -> checkout" || fail "marketplace path wrong"
[ "$(jqs "d['enabledPlugins']['communicate@communicate']")" = "True" ] && ok "plugin enabled" || fail "plugin not enabled"
[ "$(jqs "d['sentinel']")" = "keep-me" ] && [ "$(jqs "d['enabledPlugins']['existing@mkt']")" = "True" ] && ok "merge preserved existing keys" || fail "merge clobbered"
ls "$FH/.claude/"settings.json.communicate-backup-* >/dev/null 2>&1 && ok "backup written" || fail "no backup"
grep -q "plugin marketplace add $ROOT" "$FH/codex.log" && grep -q "plugin add communicate@communicate" "$FH/codex.log" && ok "codex registered via CLI" || fail "codex commands wrong: $(cat "$FH/codex.log" 2>/dev/null)"

echo "2b) marketplace conflict: remove-and-retry completes the switch"
cat > "$FH/bin/codex" <<'STUB'
#!/usr/bin/env bash
echo "$@" >> "${CODEX_LOG:?}"
if [ "$1 $2 $3" = "plugin marketplace add" ] && [ -f "${CODEX_CONFLICT:?}" ]; then
  rm -f "$CODEX_CONFLICT"; exit 1
fi
exit 0
STUB
chmod +x "$FH/bin/codex"
: > "$FH/codex.log"; touch "$FH/conflict.flag"
HOME="$FH" PATH="$FH/bin:$PATH" CODEX_LOG="$FH/codex.log" CODEX_CONFLICT="$FH/conflict.flag" "$CLI" setup-repo --codex >/dev/null 2>&1
seq="$(grep -c "plugin marketplace add" "$FH/codex.log")"
[ "$seq" = 2 ] && grep -q "plugin marketplace remove communicate" "$FH/codex.log" && grep -q "plugin add communicate@communicate" "$FH/codex.log" \
  && ok "conflict -> remove -> retry -> install" || fail "conflict sequence wrong: $(cat "$FH/codex.log")"

echo "3) uninstall restores"
run --uninstall >/dev/null || fail "uninstall errored"
[ "$(jqs "d.get('extraKnownMarketplaces',{}).get('communicate')")" = "None" ] && [ "$(jqs "d.get('enabledPlugins',{}).get('communicate@communicate')")" = "None" ] && ok "keys removed" || fail "keys remain"
[ "$(jqs "d['sentinel']")" = "keep-me" ] && ok "unrelated keys intact" || fail "uninstall damaged settings"
grep -q "plugin remove communicate@communicate" "$FH/codex.log" && ok "codex remove issued" || fail "codex remove missing"

[ "$fails" -eq 0 ] && echo "PASS: setup-repo registers/unregisters this checkout cleanly" || { echo "FAIL: $fails"; exit 1; }
