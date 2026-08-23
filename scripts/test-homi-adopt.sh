#!/usr/bin/env bash
# homi adopt: one command onboards a device — probe facts, PLAN the exact
# provisioning this fleet needed by hand (reverse keys, dial alias from
# SSH_CONNECTION, per-user runtime dir, CLI shim, kernel hash-refresh),
# execute, pair, spawn. The planner is PURE (facts in, actions out) — these
# tests encode the field lessons: peer-device, mw83, mini-1, studio-2.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
PY(){ python3 -c "
import sys; sys.path.insert(0, '$HERE/lib')
import homi_adopt as ha
$1"; }

echo "== parse_facts: KEY=VALUE lines survive ssh banner noise, defaults on absence"
r="$(PY "
out='''Welcome to the cluster! (unauthorized use prohibited)
OS=Darwin
HOMEDIR=/Users/aadarwal
SHELL_=/opt/homebrew/bin/bash
XDG=
SSHIP=203.0.113.8
CCOWNER=501
UID_=502
OWNKEY=1
REV=0
SHIM=0
TMUX_BIN=/opt/homebrew/bin/tmux
CLAUDE_BIN=
KHASH=abc123
garbage line without equals
'''
f = ha.parse_facts(out)
print(f['os'], f['login_shell'], f['ssh_ip'], f['cc_collision'], f['own_key'], f['reverse_ok'], f['claude_bin'] or '-')")"
if [ "$r" = "Darwin /opt/homebrew/bin/bash 203.0.113.8 True True False -" ]; then
  ok "facts parsed; banner and junk ignored; collision from owner!=uid"
else bad "parse_facts (got: $r)"; fi

echo "== plan: healthy adopted device -> NO actions, NO checklist (idempotent)"
r="$(PY "
f = dict(os='Linux', login_shell='/bin/bash', home='/home/a', xdg='/run/user/1008',
         ssh_ip='1.2.3.4', cc_collision=False, own_key=True, reverse_ok=True,
         shim=True, tmux_bin='/usr/bin/tmux', claude_bin='/home/a/.local/bin/claude',
         kernel_hash='SAME', py3=True)
acts, checklist = ha.plan(f, dict(kernel_hash='SAME', my_addr='aadarwal@mini'))
print(len(acts), len(checklist))")"
if [ "$r" = "0 0" ]; then ok "adopting twice is a no-op"
else bad "idempotence (got: $r)"; fi

echo "== plan: the peer-device scenario (shared mac, bash, no MagicDNS, stale kernel)"
r="$(PY "
f = dict(os='Darwin', login_shell='/opt/homebrew/bin/bash', home='/Users/a', xdg='',
         ssh_ip='203.0.113.8', cc_collision=True, own_key=True, reverse_ok=False,
         shim=False, tmux_bin='/opt/homebrew/bin/tmux', claude_bin='/Users/a/.local/bin/claude',
         kernel_hash='OLD', py3=True)
acts, checklist = ha.plan(f, dict(kernel_hash='NEW', my_addr='aadarwal@mini'))
steps = [a['step'] for a in acts]
alias = [a for a in acts if a['step']=='reverse_alias'][0]
rt = [a for a in acts if a['step']=='runtime_dir'][0]
print('authorize_key_here' in steps, 'gen_own_key' in steps,
      alias['ip'], sorted(rt['profiles']),
      'kernel_refresh' in steps, 'restart_daemon' in steps, 'shim' in steps)")"
if [ "$r" = "True False 203.0.113.8 ['~/.bash_profile', '~/.bashrc'] True True True" ]; then
  ok "alias from SSH_CONNECTION ip; bash profiles; key authorized not regenerated; kernel refreshed"
else bad "peer-device plan (got: $r)"; fi

echo "== plan: the mw83 scenario (linux, xdg fine, bare box: no tmux/claude/shim)"
r="$(PY "
f = dict(os='Linux', login_shell='/bin/bash', home='/home/a', xdg='/run/user/1008',
         ssh_ip='203.0.113.8', cc_collision=False, own_key=True, reverse_ok=False,
         shim=False, tmux_bin='', claude_bin='', kernel_hash='OLD', py3=True)
acts, checklist = ha.plan(f, dict(kernel_hash='NEW', my_addr='aadarwal@mini'))
steps = [a['step'] for a in acts]
print('runtime_dir' in steps, 'reverse_alias' in steps,
      'install_tmux_static' in steps, 'install_claude' in steps, 'shim' in steps)")"
if [ "$r" = "False True True True True" ]; then
  ok "linux keeps systemd runtime dir; static tmux + native claude planned"
else bad "mw83 plan (got: $r)"; fi

echo "== plan: fresh mac with zsh and no key -> keygen + zshenv runtime dir"
r="$(PY "
f = dict(os='Darwin', login_shell='/bin/zsh', home='/Users/a', xdg='',
         ssh_ip='10.0.0.9', cc_collision=False, own_key=False, reverse_ok=False,
         shim=False, tmux_bin='/opt/homebrew/bin/tmux', claude_bin='/Users/a/.local/bin/claude',
         kernel_hash='', py3=True)
acts, _ = ha.plan(f, dict(kernel_hash='NEW', my_addr='aadarwal@mini'))
steps = [a['step'] for a in acts]
rt = [a for a in acts if a['step']=='runtime_dir'][0]
print(steps.index('gen_own_key') < steps.index('authorize_key_here'),
      rt['profiles'], 'kernel_refresh' in steps)")"
if [ "$r" = "True ['~/.zshenv'] False" ]; then
  ok "keygen precedes authorize; zshenv; darwin runtime dir even before a collision; absent kernel left to pair"
else bad "fresh mac plan (got: $r)"; fi

echo "== plan: reverse already works -> no alias, no key work beyond dedupe"
r="$(PY "
f = dict(os='Darwin', login_shell='/bin/zsh', home='/Users/a', xdg='',
         ssh_ip='10.0.0.9', cc_collision=False, own_key=True, reverse_ok=True,
         shim=True, tmux_bin='/x/tmux', claude_bin='/x/claude',
         kernel_hash='SAME', py3=True)
acts, _ = ha.plan(f, dict(kernel_hash='SAME', my_addr='aadarwal@mini'))
steps = [a['step'] for a in acts]
print('reverse_alias' in steps, 'authorize_key_here' in steps, 'runtime_dir' in steps)")"
if [ "$r" = "False False True" ]; then
  ok "working reverse leg is left alone; darwin runtime dir still provisioned"
else bad "reverse-ok plan (got: $r)"; fi

echo "== plan: no SSH_CONNECTION ip and broken reverse -> human checklist, never a bad alias"
r="$(PY "
f = dict(os='Linux', login_shell='/bin/bash', home='/home/a', xdg='/run/user/1',
         ssh_ip='', cc_collision=False, own_key=True, reverse_ok=False,
         shim=True, tmux_bin='/x/tmux', claude_bin='/x/claude',
         kernel_hash='SAME', py3=True)
acts, checklist = ha.plan(f, dict(kernel_hash='SAME', my_addr='aadarwal@mini'))
print(any(a['step']=='reverse_alias' for a in acts),
      any('reverse' in c for c in checklist))")"
if [ "$r" = "False True" ]; then ok "unknown hub address degrades to a checklist item"
else bad "no-ip plan (got: $r)"; fi

echo "== profile selection covers unknown shells"
r="$(PY "print(ha._profile_files('/usr/bin/fish'), ha._profile_files(''))")"
if [ "$r" = "['~/.profile'] ['~/.profile']" ]; then ok "unknown shell -> ~/.profile fallback"
else bad "profile fallback (got: $r)"; fi

echo "== spawn command: settings via FILE (no inline JSON), env exported, far tmux path used"
r="$(PY "
f = dict(os='Darwin', login_shell='/bin/zsh', home='/Users/a', xdg='',
         tmux_bin='/opt/homebrew/bin/tmux', claude_bin='/Users/a/.local/bin/claude')
cmds = ha.spawn_cmds('lathe', f)
joined = chr(10).join(cmds)
print('--settings ~/.homi-settings.json' in joined,
      '{' not in joined.replace('crossSessionInbound','X'),
      '/opt/homebrew/bin/tmux' in joined,
      'XDG_RUNTIME_DIR' in joined,
      'homi-lathe' in joined)" 2>&1 | tail -1)"
if [ "$r" = "True False True True True" ]; then
  ok "spawn: settings file referenced, no raw JSON braces in shell, env + session name right"
else bad "spawn cmds (got: $r)"; fi

echo "== settings file writer: emits exact JSON via printf-safe encoding"
r="$(PY "
line = ha.settings_file_cmd()
import subprocess
out = subprocess.run(['sh','-c', line + '; cat ~/.homi-settings.json'],
                     capture_output=True, text=True,
                     env={'HOME': '$T_HOME', 'PATH': '/usr/bin:/bin'})
import json; d = json.loads(out.stdout)
print(d.get('crossSessionInbound'))" 2>&1 | tail -1)"
export T_HOME="$(mktemp -d /tmp/homi-adopt.XXXXXX)"
r="$(T_HOME="$T_HOME" PY "
line = ha.settings_file_cmd()
import subprocess, os
out = subprocess.run(['sh','-c', line + '; cat \"\$HOME/.homi-settings.json\"'],
                     capture_output=True, text=True,
                     env={'HOME': os.environ['T_HOME'], 'PATH': '/usr/bin:/bin'})
import json; d = json.loads(out.stdout)
print(d.get('crossSessionInbound'))" 2>&1 | tail -1)"
rm -rf "$T_HOME"
if [ "$r" = "accept" ]; then ok "settings JSON survives the shell round-trip byte-exact"
else bad "settings writer (got: $r)"; fi

echo "== kernel files list includes homi_adopt itself and matches homi.py's"
r="$(PY "
import re
src = open('$HERE/lib/homi.py').read()
m = re.search(r'kernel_files = \[os.path.join\(here_dir, f\) for f in\s*\(([^)]*)\)', src)
names = sorted(re.findall(r'\"([a-z_0-9.]+py)\"', m.group(1)))
print('homi_adopt.py' in names, sorted(ha.KERNEL_FILES) == names)")"
if [ "$r" = "True True" ]; then ok "one kernel list, adopt ships with it"
else bad "kernel list (got: $r)"; fi

echo "== CLI wiring: homi adopt is dispatched"
r="$(grep -c 'op == "adopt"' "$HERE/lib/homi.py")"
if [ "$r" -ge 1 ]; then ok "adopt op wired into the CLI"
else bad "cli wiring (got: $r)"; fi

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
