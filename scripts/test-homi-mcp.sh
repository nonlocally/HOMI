#!/usr/bin/env bash
# Distribution acceptance: build + pack the npm package, install the TARBALL into
# a throwaway project (as a downloader would), run setup (no launchd), then drive
# the MCP server as a client — proving a message sent through an MCP tool lands
# real mail in the daemon store. Fully isolated: its own COMM_STATE + socket.
set -uo pipefail
command -v node >/dev/null 2>&1 || { echo "skip: node not installed"; exit 0; }
PKG="/Users/aadarwal/src/aadarwal/communicate/packages/homi"
T="$(mktemp -d /tmp/homi-mcp.XXXXXX)"
export COMM_STATE="$T/state" HOMI_SOCK="$T/state/homi/homi.sock"
export XDG_DATA_HOME="$T/share" HOMI_SELF="mcphost" HOMI_TICK=1 HOMI_NO_PERSIST=1
cleanup(){
  # stop the isolated daemon; it was never installed to launchd (--no-persist)
  node "$T/node_modules/@aadarwal/homi/dist/cli.js" >/dev/null 2>&1 <<< "" || true
  pkill -f "$T/.*homi.py daemon" 2>/dev/null || true
  rm -rf "$T"
}
trap cleanup EXIT

echo "== build + pack"
( cd "$PKG" && npm run build >/dev/null 2>&1 && npm pack >/dev/null 2>&1 ) || { echo "FAIL build/pack"; exit 1; }
TARBALL="$(ls -t "$PKG"/aadarwal-homi-*.tgz | head -1)"
echo "   tarball: $(basename "$TARBALL")"

echo "== fresh install of the tarball"
( cd "$T" && npm init -y >/dev/null 2>&1 && npm install "$TARBALL" >/dev/null 2>&1 ) || { echo "FAIL install"; exit 1; }
CLI="$T/node_modules/@aadarwal/homi/dist/cli.js"
[ -f "$CLI" ] && echo "ok   installed dist/cli.js" || { echo "FAIL no cli.js"; exit 1; }

echo "== setup (no persist) + doctor"
node "$CLI" setup -y --no-persist >/dev/null 2>&1
node "$CLI" doctor 2>&1 | sed 's/^/   /'

echo "== MCP client drives the server"
node "$PKG/test/mcp-smoke.mjs" "$CLI"
rc=$?

node "$CLI" >/dev/null 2>&1 <<< "" || true
COMM_STATE="$T/state" HOMI_SOCK="$T/state/homi/homi.sock" python3 "$PKG/vendor/homi.py" call stop >/dev/null 2>&1 || true
exit $rc
