#!/usr/bin/env bash
# Agent transfer: the premove/depart/arrive op sequence between two daemons.
# Proves the address survives the move (mail sent mid-move routes to the new
# home), delivered mail is never re-delivered (cursor math), undelivered mail
# follows the agent, and the merge dedups by msg_id.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-move.XXXXXX)"
mkdir -p "$T/a-sess" "$T/b-sess"
acomm(){ COMM_STATE="$T/a" HOMI_SOCK_DIR="$T/as" HOMI_SESSIONS_DIR="$T/a-sess" \
         HOMI_SELF=origin HOMI_TICK=1 "$COMM" "$@"; }
bcomm(){ COMM_STATE="$T/b" HOMI_SOCK_DIR="$T/bs" HOMI_SESSIONS_DIR="$T/b-sess" \
         HOMI_SELF=target HOMI_TICK=1 "$COMM" "$@"; }
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ acomm homi stop >/dev/null 2>&1||true; bcomm homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT
jget(){ python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1], ""))' "$1"; }

acomm homi start >/dev/null 2>&1; bcomm homi start >/dev/null 2>&1
# device links both ways (same operator)
acomm homi link target --sock "$T/b/homi/in/origin.sock" >/dev/null 2>&1
bcomm homi link origin --sock "$T/a/homi/in/target.sock" >/dev/null 2>&1

echo "== an agent with history: 2 delivered + 1 undelivered message"
acomm homi claim traveler >/dev/null 2>&1
acomm homi send traveler "old msg 1" --from boss >/dev/null 2>&1
acomm homi send traveler "old msg 2" --from boss >/dev/null 2>&1
# mark the first two delivered (cursor=2), then one undelivered arrives
python3 - "$T/a/homi/mail/traveler" <<'PY'
import os,sys
d=sys.argv[1]
with open(os.path.join(d,".cursor"),"w") as f: f.write("2")
PY
acomm homi send traveler "undelivered msg 3" --from boss >/dev/null 2>&1

echo "== premove: reports the mailbox truthfully, no live session"
PRE="$(acomm homi premove traveler 2>/dev/null)"
[ "$(printf '%s' "$PRE" | jget lines)" = "3" ] && ok "premove sees 3 lines" || bad "premove lines ($PRE)"
[ "$(printf '%s' "$PRE" | jget cursor)" = "2" ] && ok "premove sees cursor 2" || bad "premove cursor"
[ "$(printf '%s' "$PRE" | jget live)" = "False" ] && ok "premove: not live" || bad "premove live flag"

echo "== depart: release + proxy in one breath; the address survives"
DEP="$(acomm homi depart traveler target 2>/dev/null)"
[ "$(printf '%s' "$DEP" | jget ok)" = "True" ] && ok "depart succeeded" || bad "depart ($DEP)"
if grep -q '"kind": "proxy"' "$T/a/homi/identities.json" && grep -q '"home": "target"' "$T/a/homi/identities.json"; then
  ok "origin holds a proxy homed at target"
else bad "proxy rebind"; fi
# mail sent AT the origin right now must route over the link (the straggler)
acomm homi send traveler "straggler mid-move" --from boss >/dev/null 2>&1
deadline=$((SECONDS+10)); got=""
while [ $SECONDS -lt $deadline ]; do
  bcomm homi inbox traveler 2>/dev/null | grep -q 'straggler mid-move' && { got=1; break; }
  sleep 1
done
[ -n "$got" ] && ok "mid-move mail followed the agent (address never died)" || bad "straggler routing"

echo "== arrive: two-pass merge honors the cursor"
STAGED="$T/staged-inbox.jsonl"
cp "$T/a/homi/mail/traveler/inbox.jsonl" "$STAGED"
ARR="$(bcomm homi arrive traveler --staged "$STAGED" --cursor 2 2>/dev/null)"
[ "$(printf '%s' "$ARR" | jget merged_delivered)" = "2" ] && ok "2 delivered lines merged" || bad "merge delivered ($ARR)"
[ "$(printf '%s' "$ARR" | jget merged_undelivered)" = "1" ] && ok "1 undelivered line merged" || bad "merge undelivered ($ARR)"
# target cursor: the straggler (auto-claim, line 1, delivered? it was stored then
# store->wake had no session => stays undelivered) + 2 merged-delivered => cursor >= 2
CUR="$(cat "$T/b/homi/mail/traveler/.cursor" 2>/dev/null || echo 0)"
LINES="$(grep -c . "$T/b/homi/mail/traveler/inbox.jsonl" 2>/dev/null || echo 0)"
[ "$LINES" = "4" ] && ok "target inbox holds all 4 messages" || bad "target inbox lines ($LINES)"
UNDELIV=$((LINES - CUR))
[ "$UNDELIV" = "2" ] && ok "exactly the straggler + msg3 await delivery (cursor $CUR)" || bad "cursor math (lines=$LINES cursor=$CUR)"
# old delivered mail is behind the cursor: a resumed session won't re-receive it
python3 - "$T/b/homi/mail/traveler" <<'PY' && ok "delivered history sits behind the cursor" || bad "history ordering"
import json,os,sys
d=sys.argv[1]
cur=int(open(os.path.join(d,".cursor")).read().strip() or 0)
lines=[json.loads(l) for l in open(os.path.join(d,"inbox.jsonl")) if l.strip()]
behind=[e["text"] for e in lines[:cur]]
ahead=[e["text"] for e in lines[cur:]]
assert "old msg 1" in behind and "old msg 2" in behind, behind
assert any("undelivered msg 3" in t for t in ahead), ahead
assert any("straggler" in t for t in ahead), ahead
PY

echo "== idempotence: re-running arrive dedups (msg_id), nothing doubles"
ARR2="$(bcomm homi arrive traveler --staged "$STAGED" --cursor 2 2>/dev/null)"
[ "$(printf '%s' "$ARR2" | jget merged_delivered)" = "0" ] && ok "re-merge added 0 delivered" || bad "re-merge delivered ($ARR2)"
[ "$(printf '%s' "$ARR2" | jget merged_undelivered)" = "0" ] && ok "re-merge added 0 undelivered" || bad "re-merge undelivered ($ARR2)"
LINES2="$(grep -c . "$T/b/homi/mail/traveler/inbox.jsonl")"
[ "$LINES2" = "4" ] && ok "inbox still 4 lines after re-merge" || bad "re-merge grew inbox ($LINES2)"

echo "== the moved agent answers at its new home; premove gates non-local"
P2="$(acomm homi premove traveler 2>/dev/null)"
[ "$(printf '%s' "$P2" | jget ok)" = "False" ] && ok "origin premove now refuses (proxy, not local)" || bad "premove gate ($P2)"
bcomm homi send traveler "welcome home" --from boss >/dev/null 2>&1
bcomm homi inbox traveler 2>/dev/null | grep -q 'welcome home' && ok "target-local mail lands" || bad "target-local mail"

echo "== _move_run orchestration itself (the CLI+MCP shared path)"
# Regression: the suite used to test only the daemon ops, so a crash inside the
# shared orchestration (a shadowed accumulator) shipped green. Drive _move_run
# directly with ssh/rsync stubbed — no network, no writes.
if python3 - <<'PYEOF' 2>/dev/null
import sys; sys.path.insert(0, "lib")
import homi
homi._ssh_run = lambda *a, **k: (0, "H:/home/u\nC:/usr/bin/communicate\nS:/home/u/.st", "")
homi._find_transcript = lambda name: None
def caller(req):
    op = req.get("op")
    if op == "premove": return {"ok": True, "live": False, "mailbox": "/nonexistent", "lines": 0, "cursor": 0}
    if op == "status":  return {"ok": True, "links": {"dev": {"addr": "u@dev"}}}
    return {"ok": True}
r = homi._move_run(caller, "agent1", "dev", addr="u@dev")
assert r.get("ok"), r
assert any("moved agent1" in l for l in r.get("lines") or []), r
d = homi._move_run(caller, "agent1", "dev", addr="u@dev", dry=True)
assert d.get("ok") and d.get("dry"), d
PYEOF
then ok "_move_run completes (real + dry) without crashing"; else bad "_move_run orchestration"; fi

echo "== depart requires the link; unknown device refused"
acomm homi claim stayer >/dev/null 2>&1
D2="$(acomm homi depart stayer nowhere 2>/dev/null)"
[ "$(printf '%s' "$D2" | jget ok)" = "False" ] && ok "depart to unlinked device refused" || bad "depart gate"

acomm homi stop >/dev/null 2>&1; bcomm homi stop >/dev/null 2>&1
echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
