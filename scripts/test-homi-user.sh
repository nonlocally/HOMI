#!/usr/bin/env bash
# End-to-end test for the user layer: user.json, the claim ceremony (homi init),
# handle precedence (env > user.json > OS user), handle stamping into
# card/status/agents, version surfacing, and the restore-tuple guard (no new
# per-identity fields). Fully isolated — same env discipline as test-homi-core.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMM="$HERE/bin/communicate"
T="$(mktemp -d /tmp/homi-test.XXXXXX)"
export COMM_STATE="$T/state"
export HOMI_SOCK_DIR="$T/socks"
export HOMI_SESSIONS_DIR="$T/sessions"
export HOMI_SELF="testhost"
export HOMI_TICK=1
export HOMI_FLEET_USER="osdefault"   # pin the getpass fallback so assertions are stable
unset HOMI_FLEET 2>/dev/null || true
HOMIS="$COMM_STATE/homi"
mkdir -p "$HOMI_SESSIONS_DIR"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup() { "$COMM" homi stop >/dev/null 2>&1 || true; rm -rf "$T"; }
trap cleanup EXIT

jget() { python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d[k]
print(d)' "$@" 2>/dev/null; }

echo "== section 1: unclaimed = loud degraded mode, old behavior preserved"
"$COMM" homi start >/dev/null 2>&1
st="$("$COMM" homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget self user)" = "None" ]; then
  ok "status.self.user is null before any claim"
else bad "status.self.user is null before any claim (got: $(printf '%s' "$st" | jget self user))"; fi
card="$("$COMM" homi card --json 2>/dev/null)"
if [ "$(printf '%s' "$card" | jget fleet)" = "osdefault" ]; then
  ok "card.fleet falls back to OS user when unclaimed"
else bad "card.fleet falls back to OS user (got: $(printf '%s' "$card" | jget fleet))"; fi
if [ "$(printf '%s' "$card" | jget user)" = "None" ]; then
  ok "card.user is null when unclaimed"
else bad "card.user is null when unclaimed"; fi
if "$COMM" homi user 2>&1 | grep -qi "unclaimed"; then
  ok "homi user says unclaimed"
else bad "homi user says unclaimed"; fi

echo "== section 2: the claim ceremony"
if "$COMM" homi init --handle tester1 --display "Tester One" >/dev/null 2>&1; then
  ok "homi init --handle tester1"
else bad "homi init --handle tester1"; fi
if [ -f "$HOMIS/user.json" ]; then ok "user.json written"; else bad "user.json written"; fi
u="$(cat "$HOMIS/user.json" 2>/dev/null)"
if [ "$(printf '%s' "$u" | jget v)" = "1" ] && [ "$(printf '%s' "$u" | jget handle)" = "tester1" ] \
   && [ "$(printf '%s' "$u" | jget display)" = "Tester One" ] \
   && [ -n "$(printf '%s' "$u" | jget created_at)" ]; then
  ok "user.json has the v1 shape (v, handle, display, created_at)"
else bad "user.json has the v1 shape"; fi
if "$COMM" homi user 2>/dev/null | grep -q "@tester1"; then
  ok "homi user shows @tester1"
else bad "homi user shows @tester1"; fi

echo "== section 3: the handle rides every surface (re-derived, never copied)"
card="$("$COMM" homi card --json 2>/dev/null)"
if [ "$(printf '%s' "$card" | jget user)" = "tester1" ]; then
  ok "card.user carries the handle"
else bad "card.user carries the handle"; fi
if [ "$(printf '%s' "$card" | jget fleet)" = "tester1" ]; then
  ok "card.fleet == handle (wire back-compat field follows the claim)"
else bad "card.fleet == handle (got: $(printf '%s' "$card" | jget fleet))"; fi
st="$("$COMM" homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget self user handle)" = "tester1" ]; then
  ok "status.self.user.handle"
else bad "status.self.user.handle"; fi
ag="$("$COMM" homi agents --json 2>/dev/null)"
if [ "$(printf '%s' "$ag" | jget user)" = "tester1" ]; then
  ok "agents payload carries the user"
else bad "agents payload carries the user"; fi

echo "== section 4: precedence env > user.json > OS user"
"$COMM" homi stop >/dev/null 2>&1; sleep 0.5
HOMI_FLEET=envwins "$COMM" homi start >/dev/null 2>&1
card="$("$COMM" homi card --json 2>/dev/null)"
if [ "$(printf '%s' "$card" | jget fleet)" = "envwins" ]; then
  ok "HOMI_FLEET env still overrides (test hook preserved)"
else bad "HOMI_FLEET env still overrides (got: $(printf '%s' "$card" | jget fleet))"; fi
"$COMM" homi stop >/dev/null 2>&1; sleep 0.5
"$COMM" homi start >/dev/null 2>&1   # NO env — the launchd hole: file must win
card="$("$COMM" homi card --json 2>/dev/null)"
if [ "$(printf '%s' "$card" | jget fleet)" = "tester1" ]; then
  ok "daemon restarted with no env: handle comes from user.json (launchd hole closed)"
else bad "daemon restarted with no env: handle from user.json (got: $(printf '%s' "$card" | jget fleet))"; fi
st="$("$COMM" homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget self user handle)" = "tester1" ]; then
  ok "user survives daemon restart (loaded, not regenerated)"
else bad "user survives daemon restart"; fi

echo "== section 5: re-claim is refused, --force re-derives"
if "$COMM" homi init --handle tester2 >/dev/null 2>&1; then
  bad "changing the handle without --force must be refused"
else ok "changing the handle without --force refused"; fi
if [ "$(cat "$HOMIS/user.json" | jget handle)" = "tester1" ]; then
  ok "refused re-claim left user.json untouched"
else bad "refused re-claim left user.json untouched"; fi
if "$COMM" homi init --handle tester2 --force >/dev/null 2>&1; then
  ok "homi init --force changes the handle"
else bad "homi init --force changes the handle"; fi
card="$("$COMM" homi card --json 2>/dev/null)"
if [ "$(printf '%s' "$card" | jget user)" = "tester2" ]; then
  ok "card re-derives the new handle immediately (re-assertion, not write-once)"
else bad "card re-derives the new handle immediately"; fi
if "$COMM" homi init --handle tester2 --display "Renamed" >/dev/null 2>&1; then
  ok "same handle, new display needs no --force"
else bad "same handle, new display needs no --force"; fi

echo "== section 6: validation"
for h in "Tester" "has_underscore" "-leading" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" "pm" "self" "all" "homi"; do
  if "$COMM" homi init --handle "$h" --force >/dev/null 2>&1; then
    bad "invalid handle accepted: $h"
  else ok "invalid handle refused: $h"; fi
done
H32="$(python3 -c 'print("a"*32)')"
if "$COMM" homi init --handle "$H32" --force >/dev/null 2>&1; then
  ok "a 32-char handle (the max) is legal"
else bad "a 32-char handle (the max) is legal"; fi
"$COMM" homi init --handle tester2 --force >/dev/null 2>&1
if [ "$(cat "$HOMIS/user.json" | jget handle)" = "tester2" ]; then
  ok "invalid claims never touched user.json"
else bad "invalid claims never touched user.json"; fi

echo "== section 7: version is surfaced and matches the source"
src_v="$(grep -m1 '^HOMI_VERSION' "$HERE/lib/homi.py" | cut -d'"' -f2)"
st="$("$COMM" homi status --json 2>/dev/null)"
if [ -n "$src_v" ] && [ "$(printf '%s' "$st" | jget self version)" = "$src_v" ]; then
  ok "status.self.version == HOMI_VERSION ($src_v)"
else bad "status.self.version == HOMI_VERSION (src=$src_v got=$(printf '%s' "$st" | jget self version))"; fi

echo "== section 8: restore-tuple guard — no new per-identity fields"
"$COMM" homi claim guardcheck >/dev/null 2>&1
"$COMM" homi stop >/dev/null 2>&1; sleep 0.5
"$COMM" homi start >/dev/null 2>&1; sleep 1.5
keys="$(python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
print(",".join(sorted(d["guardcheck"].keys())))' "$HOMIS/identities.json" 2>/dev/null)"
golden="aliases,boxed,card,claimed_at,home,kind,place,seat,supervision,surface,workspace"
if [ "$keys" = "$golden" ]; then
  ok "identity record keys unchanged ($keys)"
else bad "identity record keys unchanged (got: $keys, want: $golden)"; fi

echo "== section 9: a user-less legacy state dir boots clean"
"$COMM" homi stop >/dev/null 2>&1; sleep 0.5
rm -f "$HOMIS/user.json"
"$COMM" homi start >/dev/null 2>&1; sleep 1
st="$("$COMM" homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget ok)" = "True" ] && [ "$(printf '%s' "$st" | jget self user)" = "None" ]; then
  ok "boot without user.json: daemon healthy, user null again"
else bad "boot without user.json: daemon healthy, user null again"; fi
"$COMM" homi stop >/dev/null 2>&1; sleep 0.5
printf '{"v":1,"handle":12}' > "$HOMIS/user.json"   # wrong TYPE, valid JSON
"$COMM" homi start >/dev/null 2>&1; sleep 1
st="$("$COMM" homi status --json 2>/dev/null)"
if [ "$(printf '%s' "$st" | jget ok)" = "True" ] && [ "$(printf '%s' "$st" | jget self user)" = "None" ]; then
  ok "a non-string handle degrades to unclaimed (no crash-loop)"
else bad "a non-string handle degrades to unclaimed (got: $(printf '%s' "$st" | head -c 120))"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
