#!/usr/bin/env bash
# The card: derived as a side effect of claiming (never a remembered write),
# overridable by the agent or the human, and surfaced in the roster.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-card.XXXXXX)"
export COMM_STATE="$T/state" HOMI_SOCK_DIR="$T/socks" HOMI_SESSIONS_DIR="$T/sess"
export HOMI_SELF=cardhost HOMI_TICK=1
mkdir -p "$HOMI_SESSIONS_DIR"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ "$COMM" homi stop >/dev/null 2>&1||true; rm -rf "$T"; }
trap cleanup EXIT
ID="$COMM_STATE/homi/identities.json"

PROJ="$T/physlean"; mkdir -p "$PROJ"
printf '# PhysLean\n\nA Lean 4 formalisation of physics.\n' > "$PROJ/AGENTS.md"

"$COMM" homi start >/dev/null 2>&1

echo "== a card is derived from the workspace, with no extra step"
"$COMM" homi claim prover --cwd "$PROJ" >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "card derived at claim (marked derived:true)" || bad "card derivation"
import json,sys
c=json.load(open(sys.argv[1]))["prover"]["card"]
assert c and c["derived"] is True, c
assert "PhysLean" in c["what"], c
PY

echo "== describe overrides it and marks it authored"
"$COMM" homi describe prover --what "proof automation over PhysLean" \
        --ask-me-for "tactic suggestions, proof state" >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "describe overrides and clears derived flag" || bad "describe override"
import json,sys
c=json.load(open(sys.argv[1]))["prover"]["card"]
assert c["what"]=="proof automation over PhysLean", c
assert c["ask_me_for"]=="tactic suggestions, proof state", c
assert c["derived"] is False, c
PY

echo "== a claim with no workspace still gets a card (never absent)"
"$COMM" homi claim bare >/dev/null 2>&1
python3 - "$ID" <<'PY' && ok "cardless claim still carries a card stub" || bad "card stub"
import json,sys
c=json.load(open(sys.argv[1]))["bare"]["card"]
assert c is not None and "what" in c, c
PY

echo "== the roster shows the card"
"$COMM" homi agents --json 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)
a={x["name"]:x for x in d["agents"]}
assert a["prover"]["card"]["what"]=="proof automation over PhysLean", a["prover"]
' && ok "agents_list carries the card" || bad "roster card"

echo "== describe refuses an unknown identity"
"$COMM" homi describe ghost --what "x" >/dev/null 2>&1 && bad "describe accepted a ghost" || ok "describe refuses unknown identity"

"$COMM" homi stop >/dev/null 2>&1
echo; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
