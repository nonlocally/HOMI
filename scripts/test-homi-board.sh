#!/usr/bin/env bash
# Board test: the fabric's front end. One collector -> one snapshot ->
# index.html + state.json side by side; tailnet serve with a TTL cache.
# Three isolated daemons: A (@alice, the board's home), D (alice's second
# DEVICE, direct-socket link), B (@bob, a fleet PERSON via connect --direct).
# All board calls use --no-remote (far rosters ride ssh, which tests avoid).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-board.XXXXXX)"
mkdir -p "$T/a-sess" "$T/b-sess" "$T/d-sess" "$T/ahome" "$T/bhome"
export HOMI_CONNECT_DIRECT=1
export HOMI_BOARD_DIR="$T/board"
acomm(){ COMM_STATE="$T/a" HOMI_SOCK_DIR="$T/as" HOMI_SESSIONS_DIR="$T/a-sess" \
         HOMI_SELF=alice-dev HOMI_TICK=1 HOME="$T/ahome" HOMI_BOARD_DIR="$T/board" "$COMM" "$@"; }
bcomm(){ COMM_STATE="$T/b" HOMI_SOCK_DIR="$T/bs" HOMI_SESSIONS_DIR="$T/b-sess" \
         HOMI_SELF=bob-dev HOMI_TICK=1 HOME="$T/bhome" "$COMM" "$@"; }
dcomm(){ COMM_STATE="$T/d" HOMI_SOCK_DIR="$T/ds" HOMI_SESSIONS_DIR="$T/d-sess" \
         HOMI_SELF=alice-air HOMI_TICK=1 "$COMM" "$@"; }
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
SRVPID=""
# $! is the backgrounded SUBSHELL; the python server is a GRANDCHILD
# (subshell -> communicate bash -> python3), so -P misses it — target the
# unique per-run port instead, then reap the subshell.
kill_server(){ [ -n "$SRVPID" ] || return 0
               pkill -f "board --serve $PORT" 2>/dev/null
               pkill -P "$SRVPID" 2>/dev/null; kill "$SRVPID" 2>/dev/null
               wait "$SRVPID" 2>/dev/null; SRVPID=""; }
cleanup(){ kill_server
           acomm homi stop >/dev/null 2>&1||true; bcomm homi stop >/dev/null 2>&1||true
           dcomm homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT

jget() { python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d[int(k)] if isinstance(d,list) else d[k]
print(d)' "$@" 2>/dev/null; }

echo "== topology: @alice with a second device and a connected person"
acomm homi start >/dev/null 2>&1; bcomm homi start >/dev/null 2>&1; dcomm homi start >/dev/null 2>&1
acomm homi init --handle alice --display "Alice" >/dev/null 2>&1
bcomm homi init --handle bob >/dev/null 2>&1
acomm homi claim scout >/dev/null 2>&1
bcomm homi claim librarian >/dev/null 2>&1
acomm homi link alice-air --sock "$T/d/homi/in/alice-dev.sock" >/dev/null 2>&1
dcomm homi link alice-dev --sock "$T/a/homi/in/alice-air.sock" >/dev/null 2>&1
CODE="$(bcomm homi connect --invite 2>/dev/null | grep -o 'homi1\.[A-Za-z0-9+/=]*' | head -1)"
acomm homi connect @bob --code "$CODE" --yes --direct >/dev/null 2>&1 || bad "connect @bob"
acomm homi grant bob scout >/dev/null 2>&1
# The auto return-path fires only for a sender that is NOT already
# human-granted — send from a distinct name.
acomm homi send librarian@bob "hello" --from helper >/dev/null 2>&1
sleep 1.5

echo "== the snapshot (--json)"
snap="$(acomm homi board --json --no-remote 2>/dev/null)"
if [ -n "$snap" ]; then ok "board --json emits"; else bad "board --json emits"; fi
if [ "$(printf '%s' "$snap" | jget user handle)" = "alice" ]; then
  ok "snapshot names the person (@alice)"
else bad "snapshot names the person (got: $(printf '%s' "$snap" | jget user handle))"; fi
if [ "$(printf '%s' "$snap" | jget device)" = "alice-dev" ]; then
  ok "snapshot names this device"
else bad "snapshot names this device"; fi
srcv="$(grep -m1 '^HOMI_VERSION' "$HERE/lib/homi.py" | cut -d'"' -f2)"
if [ "$(printf '%s' "$snap" | jget version)" = "$srcv" ]; then
  ok "snapshot carries the kernel version"
else bad "snapshot carries the kernel version"; fi
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
t=d["totals"]
assert t["devices"] >= 2 and t["people"] == 1 and t["agents"] >= 1, t
' "$snap" 2>/dev/null; then
  ok "totals count devices, people, agents"
else bad "totals count devices, people, agents (got: $(printf '%s' "$snap" | jget totals))"; fi
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
home=d["devices"][0]
names={a["name"] for a in home["agents"]}
assert "scout" in names, names
a=[x for x in home["agents"] if x["name"]=="scout"][0]
assert a.get("state") and a.get("provenance"), a
' "$snap" 2>/dev/null; then
  ok "home roster lists scout with state + provenance"
else bad "home roster lists scout with state + provenance"; fi

echo "== people: grants, the auto return path, the pinned key"
if python3 -c '
import json,sys,time
d=json.loads(sys.argv[1])
p=[x for x in d["people"] if x["handle"]=="bob"][0]
assert "scout" in p["granted"], p
auto=p.get("auto") or {}
assert "helper" in auto and auto["helper"] > time.time(), auto
assert (p.get("fp") or "").startswith("SHA256:"), p.get("fp")
' "$snap" 2>/dev/null; then
  ok "person row: granted names + TTLd auto return path + pinned fp"
else bad "person row grants/auto/fp (got: $(printf '%s' "$snap" | jget people))"; fi

echo "== links: measured round trips on device links"
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
dev=[l for l in d["links"] if l["device"]=="alice-air"][0]
assert dev["kind"]=="device" and isinstance(dev.get("rtt_ms"), int), dev
flt=[l for l in d["links"] if l["device"]=="bob"][0]
assert flt["kind"]=="fleet", flt
' "$snap" 2>/dev/null; then
  ok "device link carries measured rtt_ms; fleet link present"
else bad "link rows (got: $(printf '%s' "$snap" | jget links))"; fi
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
far=[x for x in d["devices"] if x["device"]=="alice-air"][0]
assert far["roster_provenance"].startswith("not fetched"), far
' "$snap" 2>/dev/null; then
  ok "far roster provenance is honest (not fetched under --no-remote/sock)"
else bad "far roster provenance honesty"; fi

echo "== unreachable device: honest, never a crash"
acomm homi link ghost-dev --sock "$T/nowhere.sock" >/dev/null 2>&1
acomm homi send x@ghost-dev "queued forever" --from scout >/dev/null 2>&1
sleep 0.5
snap2="$(acomm homi board --json --no-remote 2>/dev/null)"; rc=$?
if [ $rc -eq 0 ] && [ -n "$snap2" ]; then ok "collector survives an unreachable link (exit 0)"; else bad "collector survives unreachable (rc=$rc)"; fi
if python3 -c '
import json,sys
d=json.loads(sys.argv[1])
g=[l for l in d["links"] if l["device"]=="ghost-dev"][0]
assert g.get("rtt_ms") is None and g.get("queue", 0) >= 1, g
' "$snap2" 2>/dev/null; then
  ok "unreachable link: no fake rtt, queued mail surfaced"
else bad "unreachable link honesty (got: $(printf '%s' "$snap2" | jget links))"; fi
acomm homi unlink ghost-dev >/dev/null 2>&1

echo "== render: self-contained page + sibling state.json"
acomm homi board --no-remote >/dev/null 2>&1
if [ -f "$T/board/index.html" ] && [ -f "$T/board/state.json" ]; then
  ok "index.html + state.json written side by side"
else bad "index.html + state.json written"; fi
if grep -q "scout" "$T/board/index.html" && grep -q '"handle": *"alice"\|"handle":"alice"' "$T/board/index.html"; then
  ok "snapshot inlined into the page"
else bad "snapshot inlined into the page"; fi
if grep -q 'location.protocol === "file:"' "$T/board/index.html"; then
  ok "file:// poll guard present (self-contained offline)"
else bad "file:// poll guard present"; fi
if [ "$(grep -c -e 'https\?://' -e 'url(' -e '@import' -e '<link' -e 'src=' "$T/board/index.html")" = "0" ]; then
  ok "no external references (urls, imports, links, src)"
else bad "no external references"; fi
if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$T/board/state.json" 2>/dev/null; then
  ok "state.json is valid JSON"
else bad "state.json is valid JSON"; fi

echo "== serve: tailnet-style HTTP with a TTL cache (loopback for tests)"
PORT=$((17421 + RANDOM % 500))
acomm homi board --serve "$PORT" --bind 127.0.0.1 --no-remote >/dev/null 2>&1 &
SRVPID=$!
up=""
for i in $(seq 1 20); do
  curl -sf -m 2 "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && { up=1; break; }
  sleep 0.5
done
if [ -n "$up" ]; then ok "server answers on /"; else bad "server answers on /"; fi
page="$(curl -sf -m 5 "http://127.0.0.1:$PORT/" 2>/dev/null)"
if printf '%s' "$page" | grep -q "the fabric, right now"; then
  ok "served page carries the masthead"
else bad "served page carries the masthead"; fi
g1="$(curl -sf -m 5 "http://127.0.0.1:$PORT/state.json" 2>/dev/null | jget generated_ts)"
g2="$(curl -sf -m 5 "http://127.0.0.1:$PORT/state.json" 2>/dev/null | jget generated_ts)"
if [ -n "$g1" ] && [ "$g1" = "$g2" ]; then
  ok "TTL cache: two quick reads share one collection"
else bad "TTL cache (got: $g1 vs $g2)"; fi
sleep 11
g3="$(curl -sf -m 10 "http://127.0.0.1:$PORT/state.json" 2>/dev/null | jget generated_ts)"
if [ -n "$g3" ] && [ "$g3" != "$g1" ]; then
  ok "TTL actually expires: a later read re-collected"
else bad "TTL expiry (got: $g3 vs $g1)"; fi
if curl -sf -m 5 -I "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q "200"; then
  ok "HEAD answers 200 (uptime checks work)"
else bad "HEAD answers 200"; fi
if curl -sf -m 5 "http://127.0.0.1:$PORT/?cachebust=1" >/dev/null 2>&1; then
  ok "a query string does not 404 the page"
else bad "query string handling"; fi
kill_server

echo "== the live-dot reflects a seat surface, not just the mail plane"
if command -v node >/dev/null 2>&1; then
  R="$(node -e '
    const fs=require("fs");
    const py=fs.readFileSync(process.argv[1],"utf8");
    const m=py.match(/function effState\(a\)[\s\S]*?\n  }\n  function stClass[\s\S]*?\n  }\n  function dotClass\(a\)[\s\S]*?\n  }/);
    if(!m){console.log("NOFUNCS");process.exit(0);}
    eval(m[0]);
    const codex={state:"stored",surface_state:"busy"};
    const sleeping={state:"stored",surface_state:null};
    const claude={state:"live"};
    const dead={state:"stored",surface_state:"dead"};
    const g=a=>/\blive\b/.test(dotClass(a));
    console.log((g(codex)&&effState(codex)==="busy")?"1":"0",
                (!g(sleeping)&&effState(sleeping)==="stored")?"1":"0",
                (g(claude))?"1":"0",
                (!g(dead)&&effState(dead)==="dead")?"1":"0");
  ' "$HERE/lib/homi_board.py")"
  if [ "$R" = "1 1 1 1" ]; then
    ok "codex seat (stored+busy) reads live; sleeping stays stored; claude/dead unchanged"
  else bad "board live-dot surface awareness (got: $R)"; fi
else
  ok "skip dot test (no node)"
fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
