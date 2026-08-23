#!/usr/bin/env bash
# The device bridge: the board reaching a phone over ssh to read its screen,
# its inbox, and its action ledger — and to touch it.
#
# This turns an HTTP request into a command on a real personal device, so the
# tests here are mostly about the boundary: which devices, which verbs, and
# what happens to hostile input. Nothing here needs a phone attached.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }

py() { PYTHONPATH="$HERE/lib" python3 -c "$1" 2>&1; }

echo "== only KNOWN devices are reachable"
r="$(py "
import homi_device as d
print(d.known('aadarshs-pixel-10', ['aadarshs-pixel-10','aadarshs-mac-mini-1']))
print(d.known('some-other-box', ['aadarshs-pixel-10']))
")"
if [ "$(printf '%s' "$r" | head -1)" = "True" ] && \
   [ "$(printf '%s' "$r" | tail -1)" = "False" ]; then
  ok "a device not in the roster is not reachable"
else bad "device allowlist (got: $r)"; fi

echo "== a device name is never allowed to become shell"
r="$(py "
import homi_device as d
bad = ['a; rm -rf ~', 'a\$(id)', 'a\`id\`', '../../etc', 'a b', 'a|b',
       '-oProxyCommand=x', 'a&b', 'a>b', '']
print(all(not d.valid_device(x) for x in bad))
print(d.valid_device('aadarshs-pixel-10'))
")"
if [ "$(printf '%s' "$r" | head -1)" = "True" ] && \
   [ "$(printf '%s' "$r" | tail -1)" = "True" ]; then
  ok "metacharacters, flags, traversal and blanks are all refused"
else bad "device name validation (got: $r)"; fi

echo "== only allowlisted verbs cross the bridge"
r="$(py "
import homi_device as d
print(d.allowed('notifs'), d.allowed('screen'), d.allowed('tap'))
print(d.allowed('rm'), d.allowed('msg'), d.allowed('voice'))
")"
if [ "$(printf '%s' "$r" | head -1)" = "True True True" ] && \
   [ "$(printf '%s' "$r" | tail -1)" = "False False False" ]; then
  ok "read/touch verbs allowed; msg and voice are NOT exposed over HTTP"
else bad "verb allowlist (got: $r)"; fi

echo "== the ssh command is built as ARGUMENTS, never a shell string"
r="$(py "
import homi_device as d
argv = d.build('aadarshs-pixel-10', 'tap', ['540', '400'])
print(isinstance(argv, list))
print(all(isinstance(a, str) for a in argv))
print('&&' not in ' '.join(argv) and ';' not in ' '.join(argv))
print(argv[0])
")"
if [ "$(printf '%s' "$r" | sed -n 1p)" = "True" ] && \
   [ "$(printf '%s' "$r" | sed -n 2p)" = "True" ] && \
   [ "$(printf '%s' "$r" | sed -n 3p)" = "True" ] && \
   [ "$(printf '%s' "$r" | sed -n 4p)" = "ssh" ]; then
  ok "list-form argv, no shell metacharacters, ssh is argv[0]"
else bad "command construction (got: $r)"; fi

echo "== arguments are themselves validated (a tap is two integers)"
r="$(py "
import homi_device as d
try:
    d.build('aadarshs-pixel-10', 'tap', ['540; reboot', '400']); print('LEAK')
except ValueError:
    print('refused')
try:
    d.build('aadarshs-pixel-10', 'tap', ['540', '400']); print('ok')
except ValueError:
    print('over-strict')
")"
if [ "$(printf '%s' "$r" | head -1)" = "refused" ] && \
   [ "$(printf '%s' "$r" | tail -1)" = "ok" ]; then
  ok "a hostile tap argument is refused; a real one passes"
else bad "argument validation (got: $r)"; fi

echo "== ssh is pinned to non-interactive, bounded, no-agent-forwarding"
r="$(py "
import homi_device as d
argv = d.build('aadarshs-pixel-10', 'battery', [])
j = ' '.join(argv)
print('BatchMode=yes' in j)
print('ConnectTimeout' in j)
print('-A' not in argv)
")"
if [ "$(printf '%s' "$r" | tr '\n' ' ')" = "True True True" ]; then
  ok "batch mode, a connect timeout, and no agent forwarding"
else bad "ssh hardening (got: $r)"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
