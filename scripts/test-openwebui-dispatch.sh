#!/usr/bin/env bash
# The OpenWebUI dispatch face, exercised against a LIVE homi roster.
#
# The defect this covers: _directory() was repointed at `homi agents --json`
# (rows carry kind ∈ {local,proxy} and no workdir) while both consumers still
# branched on the OLD registry vocabulary — so `dispatchable` was always False
# and ask_agent answered "not dispatchable" for every agent on the bus.
# pydantic is not installed here (it lives in the Open WebUI venv), so the
# harness stubs it and drives the real Tools class.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/owui-dispatch.XXXXXX)"
export COMM_STATE="$T/state" HOMI_SOCK_DIR="$T/socks" HOMI_SESSIONS_DIR="$T/sess"
export HOMI_SELF="dispatchhost" HOMI_TICK=1
mkdir -p "$HOMI_SESSIONS_DIR"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ "$COMM" homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT

cat > "$T/harness.py" <<'PY'
"""Drive openwebui/dispatch_tool.py with a stub pydantic (the real one lives in
the Open WebUI venv, not in this repo's test environment)."""
import importlib.util, json, os, sys, types

class _F(object):
    def __init__(self, default=None, **kw):
        self.default = default

class BaseModel(object):
    def __init__(self, **kw):
        for k, v in list(vars(type(self)).items()):
            if k.startswith("_"):
                continue
            setattr(self, k, v.default if isinstance(v, _F) else v)
        for k, v in kw.items():
            setattr(self, k, v)

_p = types.ModuleType("pydantic")
_p.BaseModel = BaseModel
_p.Field = lambda default=None, **kw: _F(default)
sys.modules["pydantic"] = _p

spec = importlib.util.spec_from_file_location("dispatch_tool", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
t = mod.Tools()
t.valves.communicate_path = os.environ["COMM_PATH"]
t.valves.timeout_seconds = int(os.environ.get("ASK_TIMEOUT", "6"))
verb = sys.argv[2]
if verb == "directory":
    rows, err = t._directory()
    print(json.dumps({"rows": rows, "err": err}))
elif verb == "list":
    print(t.list_agents())
elif verb == "ask":
    print(t.ask_agent(sys.argv[3], sys.argv[4]))
PY

run(){ COMM_PATH="$COMM" python3 "$T/harness.py" "$HERE/openwebui/dispatch_tool.py" "$@" 2>&1; }

"$COMM" homi start >/dev/null 2>&1
"$COMM" homi claim spec1 >/dev/null 2>&1
"$COMM" homi describe spec1 --what "runs FDTD sweeps" --ask-me-for "simulation setup" >/dev/null 2>&1

echo "== the directory reads homi's roster (not the deleted registry)"
DIR="$(run directory)"
python3 - "$DIR" <<'PY' && ok "directory returns roster rows with a measured state" || bad "directory rows ($DIR)"
import json,sys
d=json.loads(sys.argv[1])
assert d["err"] is None, d
r=[x for x in d["rows"] if x["name"]=="spec1"]
assert r, d
r=r[0]
assert "state" in r, r          # measured liveness, not a kind guess
assert r["what"]=="runs FDTD sweeps", r
PY

echo "== list_agents reports each agent's card + measured dispatchability"
LS="$(run list)"
printf '%s' "$LS" | grep -q 'spec1' && ok "list_agents lists the claimed agent" || bad "list_agents ($LS)"
printf '%s' "$LS" | grep -q 'runs FDTD sweeps' && ok "list_agents surfaces the capability card" || bad "card missing from list_agents"

echo "== ask_agent routes through homi ask (a real reply round trip)"
( ASK_TIMEOUT=25 run ask spec1 "what is 2+2?" > "$T/ask.out" 2>&1 ) &
ASKPID=$!
TOKEN=""
for i in $(seq 1 40); do
  TOKEN="$("$COMM" homi inbox spec1 2>/dev/null | python3 -c '
import json,re,sys
tok=""
for line in sys.stdin:
    try: d=json.loads(line)
    except Exception: continue
    m=re.search(r"homi reply (\S+)", d.get("text",""))
    if m: tok=m.group(1)
print(tok)')"
  [ -n "$TOKEN" ] && break
  sleep 0.5
done
if [ -n "$TOKEN" ]; then ok "ask_agent delivered a homi ask (return token in the mailbox)"; else bad "no homi ask reached spec1"; fi
"$COMM" homi reply "$TOKEN" "the answer is 4" --from spec1 >/dev/null 2>&1
wait $ASKPID 2>/dev/null
if grep -q 'the answer is 4' "$T/ask.out"; then ok "ask_agent returned the specialist's reply"; else bad "ask_agent reply ($(cat "$T/ask.out"))"; fi
grep -qi 'not dispatchable' "$T/ask.out" && bad "ask_agent still refuses roster agents as 'not dispatchable'" || ok "no bogus 'not dispatchable' refusal"

echo "== an unanswered ask is reported as durably held, never as a lie"
OUT="$(ASK_TIMEOUT=3 run ask spec1 "nobody is home")"
printf '%s' "$OUT" | grep -qi 'mailbox\|held\|stored' && ok "timeout says the message is durably stored" || bad "timeout wording ($OUT)"

echo "== unknown names and a broken CLI path fail loudly, never with a traceback"
OUT="$(run ask nosuchagent "hello")"
printf '%s' "$OUT" | grep -q "no agent named" && ok "unknown agent named clearly" || bad "unknown agent ($OUT)"
OUT="$(COMM_PATH=/nonexistent/communicate python3 "$T/harness.py" "$HERE/openwebui/dispatch_tool.py" list 2>&1)"
printf '%s' "$OUT" | grep -qi 'traceback' && bad "broken CLI path raised ($OUT)" || ok "broken CLI path returns an error, not a traceback"
printf '%s' "$OUT" | grep -qi 'error' && ok "broken CLI path says ERROR" || bad "broken CLI path wording ($OUT)"

"$COMM" homi stop >/dev/null 2>&1
echo; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
