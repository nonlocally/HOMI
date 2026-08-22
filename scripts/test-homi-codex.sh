#!/usr/bin/env bash
# Codex session view: parse a codex rollout JSONL into the same turn shape as
# the Claude adapter, resolve a pane -> its MAIN rollout (earliest-started in
# the pane cwd; sub-agents spawn later), and route a codex-bound seat through
# the timeline. Module-level (no daemon needed for parse/resolve).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d /tmp/homi-codex.XXXXXX)"
export CODEX_HOME="$T/codex"
mkdir -p "$CODEX_HOME/sessions/2026/08/22"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ rm -rf "$T"; }
trap cleanup EXIT

RF="$CODEX_HOME/sessions/2026/08/22/rollout-2026-08-22T00-00-00-main0000-1111-2222-3333-444444444444.jsonl"
python3 - "$RF" <<'PY'
import json, sys
rf = sys.argv[1]
def rec(**k): return json.dumps(k)
def ri(ts, **payload): return rec(type="response_item", timestamp=ts, payload=payload)
lines = [
    rec(type="session_meta", timestamp="2026-08-22T00:00:00.000Z",
        payload={"session_id": "main0000-1111-2222-3333-444444444444",
                 "cwd": "/work/physlib", "originator": "cli"}),
    rec(type="event_msg", timestamp="2026-08-22T00:00:00.100Z",
        payload={"type": "task_started"}),
    ri("2026-08-22T00:00:01.000Z", type="message", role="developer",
       content=[{"type": "text", "text": "<skills_instructions> system stuff"}]),
    ri("2026-08-22T00:00:02.000Z", type="message", role="user",
       content=[{"type": "text", "text": "prove the pendulum lemma"}]),
    ri("2026-08-22T00:00:03.000Z", type="reasoning", summary="private chain"),
    ri("2026-08-22T00:00:04.000Z", type="message", role="assistant",
       content=[{"type": "text", "text": "on it — checking the imports"}]),
    ri("2026-08-22T00:00:05.000Z", type="custom_tool_call", name="shell",
       input={"command": "lake build Physlib.Pendulum"}),
    ri("2026-08-22T00:00:06.000Z", type="custom_tool_call_output",
       output="giant build log"),
    ri("2026-08-22T00:00:07.000Z", type="function_call", name="apply_patch",
       input={"path": "Physlib/Pendulum.lean"}),
    rec(type="compacted", timestamp="2026-08-22T00:00:08.000Z",
        payload={"message": ""}),
    ri("2026-08-22T00:00:09.000Z", type="message", role="assistant",
       content=[{"type": "text", "text": "done — it compiles."}]),
]
open(rf, "w").write("\n".join(lines) + "\n")
PY
ok "fixture rollout written"

echo "== parse: codex records -> the timeline turn shape"
seq="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
r = hc.turns_for('%99', _rollout='$RF')
for t in r['turns']:
    print(t['role'] + '|' + t['text'].replace(chr(10),' '))")"
expect="user|prove the pendulum lemma
assistant|on it — checking the imports
tool|\$ lake build Physlib.Pendulum
tool|⟶ apply_patch Physlib/Pendulum.lean
mark|· context compacted ·
assistant|done — it compiles."
if [ "$seq" = "$expect" ]; then
  ok "turn sequence exact: user/assistant kept, developer/reasoning/output dropped"
else bad "codex parse (got: $seq)"; fi

echo "== turns_for shape matches the Claude adapter"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
d = hc.turns_for('%99', _rollout='$RF')
print(d.get('ok'), d.get('present'), d.get('sid'), d.get('total'), d.get('truncated'))")"
if [ "$r" = "True True main0000-1111-2222-3333-444444444444 6 False" ]; then
  ok "shape: ok/present/sid/total/truncated as the Claude adapter"
else bad "codex shape (got: $r)"; fi

echo "== incremental append (cursor returns only the new)"
r="$(python3 -c "
import json, sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
a = hc.turns_for('%99', _rollout='$RF')
last = a['turns'][-1]['i']
with open('$RF','a') as f:
    f.write(json.dumps({'type':'response_item','timestamp':'2026-08-22T00:00:20.000Z','payload':{'type':'message','role':'assistant','content':[{'type':'text','text':'a later note'}]}})+chr(10))
b = hc.turns_for('%99', _rollout='$RF', after=last)
print(len(b['turns']), b['turns'][-1]['text'] if b['turns'] else '-')")"
if [ "$r" = "1 a later note" ]; then ok "append re-read; after=<last> returns exactly the new turn"
else bad "codex incremental (got: $r)"; fi

echo "== resolution picks the pane MAIN session (earliest in cwd, not a sub-agent)"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
cands = [
  ('/r/subagent.jsonl', 200.0, '/work/physlib'),   # newer sub-agent, same cwd
  ('/r/main.jsonl',     100.0, '/work/physlib'),   # oldest in cwd = main
  ('/r/other.jsonl',     50.0, '/elsewhere'),      # oldest overall but wrong cwd
]
print(hc._pick_main(cands, '/work/physlib'))")"
if [ "$r" = "/r/main.jsonl" ]; then ok "main = earliest-started rollout in the pane cwd"
else bad "main-session pick (got: $r)"; fi

echo "== a seat with no codex rollout degrades honestly"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
d = hc.turns_for('%99', _rollout=None)   # explicit: no rollout resolved
print(d.get('ok'), d.get('present'), len(d.get('turns') or []))")"
if [ "$r" = "True False 0" ]; then ok "no rollout -> present:false, empty (messages-only)"
else bad "codex empty shape (got: $r)"; fi

echo "== containment: a rollout-named file OUTSIDE \$CODEX_HOME is refused"
OUT="$T/evil-rollout-2026-08-22T00-00-00-deadbeef.jsonl"
python3 -c "
import json
open('$OUT','w').write(json.dumps({'type':'session_meta','payload':{'session_id':'x','cwd':'/w'}})+chr(10)+json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'text','text':'SECRET LEAK'}]}})+chr(10))
"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
d = hc.turns_for('%99', _rollout='$OUT')
print(d.get('present'), len(d.get('turns') or []))")"
if [ "$r" = "False 0" ]; then ok "out-of-CODEX_HOME rollout rejected (no arbitrary-file leak)"
else bad "containment (got: $r)"; fi

echo "== _pick_main: cwd normalized, all-None deterministic"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
# trailing-slash / non-normal cwd must still match
c1 = [('/r/main.jsonl',100.0,'/work/physlib'),('/r/sub.jsonl',200.0,'/work/physlib')]
a = hc._pick_main(c1, '/work/physlib/')
# all-None start: deterministic regardless of input order
b1 = hc._pick_main([('/r/a.jsonl',None,'/w'),('/r/b.jsonl',None,'/w')], '/w')
b2 = hc._pick_main([('/r/b.jsonl',None,'/w'),('/r/a.jsonl',None,'/w')], '/w')
print(a, b1==b2)")"
if [ "$r" = "/r/main.jsonl True" ]; then ok "cwd match normalized; None-start tie-break deterministic"
else bad "_pick_main edges (got: $r)"; fi

echo "== live reflects pane existence (dead/unknown pane -> live:false)"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
d = hc.turns_for('%99', _rollout='$RF')   # %99: no such pane
print(d.get('present'), d.get('live'))")"
if [ "$r" = "True False" ]; then ok "present with a rollout, but live:false when the pane is gone"
else bad "live liveness (got: $r)"; fi

echo "== all real codex tool-call kinds render as receipts"
r="$(python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_codex as hc
def tool(pt, **extra):
    return hc._parse_record({'type':'response_item','timestamp':'2026-08-22T00:00:00Z','payload':dict(type=pt, **extra)})
kinds = ['web_search_call','tool_search_call','function_call','custom_tool_call','local_shell_call']
ok_all = all(len(tool(k, name=k))==1 and tool(k, name=k)[0]['role']=='tool' for k in kinds)
# outputs still dropped
drop = tool('custom_tool_call_output', output='x')==[] and tool('web_search_call_output')==[]
print(ok_all, drop)")"
if [ "$r" = "True True" ]; then ok "every *_call kind renders; *_call_output dropped"
else bad "tool-kind coverage (got: $r)"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" = "0" ]
