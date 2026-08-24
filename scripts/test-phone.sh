#!/usr/bin/env bash
# The `phone` verb layer: one CLI that turns an Android device (Termux + adb,
# no root) into a surface any fabric agent can drive. The testable core is the
# PARSING — the UI tree and the notification inbox — because that is what an
# LLM actually reasons over. Device I/O is shelled out and stubbed here.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHONE="$HERE/lib/phone"
T="$(mktemp -d /tmp/phone-test.XXXXXX)"
# The ledger is the accountability record, so the suite must never write to
# the real one. It did: every lease and shell-verb test that did not set
# PHONE_LEDGER itself appended to ~/.local/state/communicate/phone-actions.jsonl,
# leaving 138 rows with actors named "intruder", "ghost" and "driver1" in the
# file a human is supposed to consult to find out who touched their phone.
# Exported once here so it is the default for every invocation; the tests that
# set it explicitly still override it.
export PHONE_LEDGER="$T/default-ledger.jsonl"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ rm -rf "$T"; }
trap cleanup EXIT

# A realistic uiautomator dump: nested nodes, the label NOT itself clickable
# (the real WhatsApp/Gmail shape — text sits inside a clickable row).
cat > "$T/ui.xml" <<'XML'
<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.whatsapp" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2424]">
    <node index="1" text="" resource-id="com.whatsapp:id/conversation" class="android.view.ViewGroup" package="com.whatsapp" content-desc="" clickable="true" enabled="true" bounds="[0,300][1080,500]">
      <node index="0" text="peer peer-user" resource-id="com.whatsapp:id/conversation_contact_name" class="android.widget.TextView" package="com.whatsapp" content-desc="" clickable="false" enabled="true" bounds="[100,320][600,380]" />
      <node index="1" text="see you at 3" resource-id="com.whatsapp:id/single_msg" class="android.widget.TextView" package="com.whatsapp" content-desc="" clickable="false" enabled="true" bounds="[100,400][700,460]" />
    </node>
    <node index="2" text="" resource-id="com.whatsapp:id/send" class="android.widget.ImageButton" package="com.whatsapp" content-desc="Send" clickable="true" enabled="true" bounds="[900,2000][1000,2100]" />
    <node index="3" text="Disabled thing" resource-id="" class="android.widget.Button" package="com.whatsapp" content-desc="" clickable="true" enabled="false" bounds="[0,1000][200,1100]" />
  </node>
</hierarchy>
XML

cat > "$T/notifs.json" <<'JSON'
[
  {"id":1,"tag":"","key":"0|com.whatsapp|1|null|10123","group":"","packageName":"com.whatsapp","title":"peer peer-user","content":"see you at 3","when":"2026-08-23 09:15:00"},
  {"id":2,"tag":"","key":"0|com.google.android.gm|2|null|10200","group":"","packageName":"com.google.android.gm","title":"MIT Payroll","content":"Your statement is ready","when":"2026-08-23 08:00:00"},
  {"id":3,"tag":"","key":"0|com.google.android.apps.turbo|3|null|10300","group":"","packageName":"com.google.android.apps.turbo","title":"Reduce screen timeout","content":"Long screen timeout consumes battery","when":"2026-08-22 23:00:00"},
  {"id":4,"tag":"","key":"0|com.whatsapp|4|null|10123","group":"","packageName":"com.whatsapp","title":"Mom","content":"call me when free","when":"2026-08-23 10:02:00"},
  {"id":5,"tag":"","key":"0|com.whatsapp|5|null|10123","group":"","packageName":"com.whatsapp","title":"Lab group","content":"","lines":["Ravi: pushed the fix","Sara: running it now"],"when":"2026-08-23 10:05:00"}
]
JSON

echo "== the CLI exists and is honest about unknown verbs"
if [ -x "$PHONE" ]; then ok "lib/phone is executable"; else bad "lib/phone missing or not executable"; fi
out="$("$PHONE" definitely-not-a-verb 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "unknown verb"; then
  ok "unknown verb -> non-zero exit + honest message"
else bad "unknown verb handling (rc=$rc out=$out)"; fi

echo "== ui: parse a real uiautomator tree into LLM-readable lines"
out="$("$PHONE" ui --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -q "peer peer-user" && printf '%s' "$out" | grep -q "Send"; then
  ok "ui lists on-screen text and content-desc"
else bad "ui parse (got: $out)"; fi
if printf '%s' "$out" | grep -qE "\[?[0-9]+,[0-9]+\]?"; then
  ok "ui emits tappable coordinates"
else bad "ui coordinates missing"; fi

echo "== find: text -> the CLICKABLE ancestor's center (the real-app shape)"
# "peer peer-user" is a non-clickable TextView inside a clickable row
# [0,300][1080,500] -> center 540,400. Tapping the label's own center (350,350)
# would miss the row's handler in many apps.
out="$("$PHONE" find "peer peer-user" --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -q "540" && printf '%s' "$out" | grep -q "400"; then
  ok "find resolves a label to its clickable ancestor's center"
else bad "find clickable-ancestor (got: $out)"; fi

echo "== find: content-desc matches too (icon buttons have no text)"
out="$("$PHONE" find "Send" --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -q "950" && printf '%s' "$out" | grep -q "2050"; then
  ok "find matches content-desc (Send button center)"
else bad "find content-desc (got: $out)"; fi

echo "== find: case-insensitive, and misses are honest (not a wrong tap)"
out="$("$PHONE" find "peer peer-user" --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -q "540"; then ok "find is case-insensitive"
else bad "find case-insensitivity (got: $out)"; fi
out="$("$PHONE" find "Nonexistent Label" --from "$T/ui.xml" 2>&1)"; rc=$?
if [ $rc -ne 0 ] && ! printf '%s' "$out" | grep -qE "^[0-9]+ [0-9]+$"; then
  ok "a miss exits non-zero and emits NO coordinates (never a blind tap)"
else bad "find miss must not yield coordinates (rc=$rc out=$out)"; fi

echo "== find: disabled elements are never offered as targets"
out="$("$PHONE" find "Disabled thing" --from "$T/ui.xml" 2>&1)"; rc=$?
if [ $rc -ne 0 ]; then ok "disabled element is not a tap target"
else bad "disabled element was offered (out=$out)"; fi

echo "== notifs: the universal inbox, newest first, junk-free"
out="$("$PHONE" notifs --from "$T/notifs.json" 2>&1)"
if printf '%s' "$out" | grep -q "Mom" && printf '%s' "$out" | grep -q "peer peer-user"; then
  ok "notifs lists real messages"
else bad "notifs listing (got: $out)"; fi
first="$(printf '%s' "$out" | grep -nE "Mom|peer peer-user" | head -1)"
if printf '%s' "$first" | grep -q "Mom"; then
  ok "notifs is newest-first (10:02 Mom before 09:15 peer)"
else bad "notifs ordering (got: $first)"; fi

echo "== notifs --app: filter to one app by friendly name"
out="$("$PHONE" notifs --from "$T/notifs.json" --app whatsapp 2>&1)"
if printf '%s' "$out" | grep -q "Mom" && ! printf '%s' "$out" | grep -q "MIT Payroll"; then
  ok "--app whatsapp maps to com.whatsapp and filters"
else bad "--app filter (got: $out)"; fi

echo "== notifs --since: only what arrived after a timestamp"
out="$("$PHONE" notifs --from "$T/notifs.json" --since "2026-08-23 09:00:00" 2>&1)"
if printf '%s' "$out" | grep -q "Mom" && ! printf '%s' "$out" | grep -q "screen timeout"; then
  ok "--since drops older notifications"
else bad "--since filter (got: $out)"; fi

echo "== notifs --json stays machine-readable for the brain"
out="$("$PHONE" notifs --from "$T/notifs.json" --json 2>&1)"
if printf '%s' "$out" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert isinstance(d,list) and d and 'packageName' in d[0]
print('ok')" 2>/dev/null | grep -q ok; then
  ok "--json emits parseable JSON"
else bad "--json output (got: $out)"; fi

echo "== device verbs degrade honestly with no device attached"
out="$(PHONE_NO_DEVICE=1 "$PHONE" say "hello" 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qiE "no device|not available|termux"; then
  ok "say without a device fails loudly, never silently"
else bad "no-device degradation (rc=$rc out=$out)"; fi

echo "== level-1 first: app/media control must survive adb being OFF"
# Android turns wireless debugging off on network change or idle, so the CORE
# experience cannot depend on adb. These verbs build a deep link and hand it to
# termux-open-url (Termux:API), which needs no adb at all.
out="$("$PHONE" play "here comes the sun" --print-url 2>&1)"
if printf '%s' "$out" | grep -q "music.youtube.com" && printf '%s' "$out" | grep -q "here+comes+the+sun"; then
  ok "play builds a ytmusic deep link (url-encoded)"
else bad "play url (got: $out)"; fi

out="$("$PHONE" play "hello" --app spotify --print-url 2>&1)"
if printf '%s' "$out" | grep -qi "spotify"; then ok "play --app spotify targets spotify"
else bad "play spotify url (got: $out)"; fi

out="$("$PHONE" open whatsapp --print-url 2>&1)"
if printf '%s' "$out" | grep -qiE "whatsapp|wa.me"; then ok "open resolves an app to its deep link"
else bad "open app deep link (got: $out)"; fi

out="$("$PHONE" open "https://example.com/x?a=1" --print-url 2>&1)"
if printf '%s' "$out" | grep -q "https://example.com/x?a=1"; then ok "open passes a url through unchanged"
else bad "open url passthrough (got: $out)"; fi

out="$("$PHONE" open definitely-not-an-app --print-url 2>&1)"; rc=$?
if [ $rc -ne 0 ]; then ok "open of an unknown app fails honestly"
else bad "open unknown app should fail (out=$out)"; fi

echo "== whatsapp: a phone number + text becomes a prefilled chat link"
out="$("$PHONE" msg whatsapp --to "+16175551234" --text "on my way" --print-url 2>&1)"
if printf '%s' "$out" | grep -q "16175551234" && printf '%s' "$out" | grep -q "on%20my%20way"; then
  ok "msg builds a wa.me link with encoded text"
else bad "msg wa.me link (got: $out)"; fi

echo "== voice: one turn = capture -> hand to a brain -> the brain speaks back"
# --text injects a transcript, so the turn is testable without a microphone
# (and lets the human type instead of talk).
out="$("$PHONE" voice --text "what are my messages" --to ember --print-only 2>&1)"
if printf '%s' "$out" | grep -q "ember" && printf '%s' "$out" | grep -q "what are my messages"; then
  ok "voice routes a transcript to the named agent"
else bad "voice routing (got: $out)"; fi

out="$("$PHONE" voice --text "   " --print-only 2>&1)"; rc=$?
if [ $rc -ne 0 ]; then ok "an empty transcript is refused, never sent as a blank prompt"
else bad "voice empty transcript (rc=$rc out=$out)"; fi

out="$("$PHONE" voice --text "hi" --print-only 2>&1)"
if printf '%s' "$out" | grep -qiE "voice|spoken|phone say"; then
  ok "the delivered prompt tells the brain it is a SPOKEN turn (answer via phone say)"
else bad "voice prompt framing (got: $out)"; fi

echo "== multiline messages are NOT lost (the known termux-api empty-content bug)"
# termux-notification-list returns content:"" for MessagingStyle notifications
# — exactly the WhatsApp/group-chat case — but still fills `lines`. Dropping
# those would silently hide the most important messages on the phone.
out="$("$PHONE" notifs --from "$T/notifs.json" --app whatsapp 2>&1)"
if printf '%s' "$out" | grep -q "Lab group" && printf '%s' "$out" | grep -q "pushed the fix"; then
  ok "a group chat with empty content still shows its message lines"
else bad "multiline recovery (got: $out)"; fi

echo "== ptt: a persistent notification whose buttons ARE the agent's front door"
# termux-notification --buttonN-action runs a shell command inside Termux, and
# an action containing $REPLY gets a RemoteInput text box. So the lock-screen
# notification becomes both press-to-talk AND type-to-agent — no adb involved.
out="$("$PHONE" ptt --to ember --print-only 2>&1)"
if printf '%s' "$out" | grep -q -- "--button1" && printf '%s' "$out" | grep -q "voice"; then
  ok "ptt wires a Talk button to a voice turn"
else bad "ptt talk button (got: $out)"; fi
if printf '%s' "$out" | grep -q 'REPLY'; then
  ok "ptt wires a typed-reply button via RemoteInput (\$REPLY)"
else bad "ptt reply button (got: $out)"; fi
if printf '%s' "$out" | grep -q -- "--ongoing"; then
  ok "ptt is ongoing so it stays pinned"
else bad "ptt not ongoing (got: $out)"; fi
# Tapping the notification BODY must talk too. Without an explicit --action,
# Android launches the posting app instead — the human lands on Termux:API's
# info screen and reasonably thinks it is broken. (Observed live.)
if printf '%s' "$out" | grep -q -- "--action"; then
  ok "tapping the notification body starts a voice turn (not the API app)"
else bad "ptt missing tap-anywhere --action (got: $out)"; fi

echo "== voice --seat: deliver straight into a local agent's pane"
# When the brain lives on THIS device, the mail plane is a needless hop (and
# an older agent build may not read live mail at all). Typing into its tmux
# pane is the shortest, most reliable path.
out="$("$PHONE" voice --text "hello there" --seat homi-ember --print-only 2>&1)"
if printf '%s' "$out" | grep -q "homi-ember" && printf '%s' "$out" | grep -q "hello there"; then
  ok "voice --seat targets a tmux pane instead of the mail plane"
else bad "voice --seat (got: $out)"; fi
if printf '%s' "$out" | grep -qi "send-keys"; then
  ok "voice --seat delivers by typing into the pane"
else bad "voice --seat should use tmux send-keys (got: $out)"; fi

echo "== the ledger: every action leaves a trace, at the only chokepoint there is"
# Full two-way agency is safe because it is ACCOUNTABLE, not because it is
# gated. Every capability flows through this CLI, so this CLI is where the
# record gets written — an agent cannot act on the phone without logging it.
LEDGER="$T/actions.jsonl"
# A FAILED send must be recorded too — "I tried to message your mother and
# could not" is exactly the kind of thing the record exists to preserve.
PHONE_LEDGER="$LEDGER" PHONE_NO_DEVICE=1 "$PHONE" msg whatsapp --to "+16175551234" \
  --text "some message" --send >/dev/null 2>&1
if [ -s "$LEDGER" ] && python3 -c "
import json,sys
rows=[json.loads(l) for l in open('$LEDGER') if l.strip()]
a=rows[-1]
assert a['verb']=='msg', a
assert 'some message' in json.dumps(a), a
assert a.get('ts') and a.get('kind')=='act', a
assert a['detail']['sent'] is False, a
assert 'fail' in str(a.get('result','')).lower(), a
print('ok')" 2>/dev/null | grep -q ok; then
  ok "an attempted action appends a structured entry — failures included"
else bad "ledger append (got: $(cat "$LEDGER" 2>/dev/null | tail -1))"; fi

echo "== reads of PERSONAL data are logged too, but marked as reads"
PHONE_LEDGER="$LEDGER" "$PHONE" notifs --from "$T/notifs.json" >/dev/null 2>&1
if python3 -c "
import json
rows=[json.loads(l) for l in open('$LEDGER') if l.strip()]
r=[x for x in rows if x['verb']=='notifs']
assert r, 'no notifs entry'
assert r[-1]['kind']=='read', r[-1]
print('ok')" 2>/dev/null | grep -q ok; then
  ok "reading the inbox is recorded as kind=read (it is someone's private mail)"
else bad "read logging"; fi

echo "== mechanical reads do NOT flood the ledger"
before=$(wc -l < "$LEDGER")
PHONE_LEDGER="$LEDGER" "$PHONE" find "Send" --from "$T/ui.xml" >/dev/null 2>&1
PHONE_LEDGER="$LEDGER" "$PHONE" ui --from "$T/ui.xml" >/dev/null 2>&1
after=$(wc -l < "$LEDGER")
if [ "$before" = "$after" ]; then
  ok "ui/find are mechanical and stay out of the record"
else bad "ledger noise (grew $before -> $after)"; fi

echo "== phone log reads the ledger back"
out="$(PHONE_LEDGER="$LEDGER" "$PHONE" log --limit 5 2>&1)"
if printf '%s' "$out" | grep -q "msg"; then ok "phone log renders recent actions"
else bad "phone log (got: $out)"; fi
out="$(PHONE_LEDGER="$LEDGER" "$PHONE" log --json 2>&1)"
if printf '%s' "$out" | python3 -c "
import json,sys
d=json.load(sys.stdin); assert isinstance(d,list) and d; print('ok')" 2>/dev/null | grep -q ok; then
  ok "phone log --json is machine-readable (the cockpit renders it)"
else bad "phone log --json"; fi

echo "== msg --send: the two-way loop, and it must never fake success"
out="$(PHONE_NO_DEVICE=1 "$PHONE" msg whatsapp --to "+16175551234" --text "hi" --send 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qiE "adb|device|cannot"; then
  ok "--send without adb fails loudly (it cannot tap, so it must not claim it sent)"
else bad "msg --send no-device honesty (rc=$rc out=$out)"; fi

echo "== find --id: resource-id beats a label (labels move between releases)"
out="$("$PHONE" find --id "com.whatsapp:id/send" --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -q "950" && printf '%s' "$out" | grep -q "2050"; then
  ok "find --id resolves a resource-id to coordinates"
else bad "find --id (got: $out)"; fi

echo "== screen --out - streams to stdout (no picture left on disk)"
if grep -q 'out_path == "-"' "$HERE/lib/phone" && \
   grep -q "stdout.buffer.write" "$HERE/lib/phone"; then
  ok "screen supports streaming to stdout for the cockpit"
else bad "screen --out - missing"; fi

echo "== watchdog: the phone heals itself, because Android will keep killing it"
# Termux dies on network changes and hard sleeps. A shell loop gets killed
# too, so the watchdog registers with Android's own JobScheduler — the one
# scheduler the OS actually honours.
out="$(PHONE_NO_DEVICE=1 "$PHONE" watchdog --print-only 2>&1)"
if printf '%s' "$out" | grep -qi "sshd" && printf '%s' "$out" | grep -qi "daemon"; then
  ok "a heal pass covers sshd and the homi daemon"
else bad "watchdog coverage (got: $out)"; fi
if printf '%s' "$out" | grep -qi "wake-lock\|wakelock"; then
  ok "the heal pass re-takes the wakelock"
else bad "watchdog wakelock (got: $out)"; fi
if printf '%s' "$out" | grep -qi "adb"; then
  ok "the heal pass reconnects adb"
else bad "watchdog adb (got: $out)"; fi

out="$(PHONE_NO_DEVICE=1 "$PHONE" watchdog --install --print-only 2>&1)"
if printf '%s' "$out" | grep -q "termux-job-scheduler"; then
  ok "install registers with Android's JobScheduler (survives process death)"
else bad "watchdog install (got: $out)"; fi
if printf '%s' "$out" | grep -q -- "--persisted true"; then
  ok "the job is persisted, so it survives a reboot too"
else bad "watchdog not persisted (got: $out)"; fi

echo "== the ledger's integrity claim must hold for EVERY sensitive verb"
# The claim in lib/phone is that an agent cannot act on the phone without
# leaving a trace. That was false: camera capture, GPS, clipboard, mic and
# key-events all recorded nothing — and `where` (location) was listed in the
# policy comment as a logged read while its own function never called record().
missing="$(python3 -c "
import re
src = open('$HERE/lib/phone').read()
bad = []
for v in ('cmd_key','cmd_open','cmd_media','cmd_photo','cmd_notify',
          'cmd_clip','cmd_listen','cmd_where','cmd_tap','cmd_type','cmd_say'):
    m = re.search(r'^def %s\(.*?(?=^def |\Z)' % v, src, re.S|re.M)
    if not m or 'record(' not in m.group(0):
        bad.append(v)
print(' '.join(bad))
")"
if [ -z "$missing" ]; then
  ok "every sensitive verb records (camera, gps, clipboard, mic, keys, launches)"
else bad "verbs act with NO ledger entry:$missing"; fi

echo "== shizuku is preferred over adb, and adb is only a fallback"
# Shizuku holds shell UID over Binder IPC: no port, no connection, nothing
# for Android to switch off. adb is the fragile path that kept dying, so it
# must never be REQUIRED when Shizuku is present.
if grep -q "def have_rish" "$HERE/lib/phone" && grep -q "def dev_shell" "$HERE/lib/phone"; then
  ok "there is a single device-shell entry point with a shizuku path"
else bad "no dev_shell/have_rish abstraction"; fi

# every place that used to hard-require adb must now accept either tier
# Grep for adb_ready()/need_adb() OUTSIDE the functions allowed to fall back
# to adb. The old assertion looked only for need_adb() and passed vacuously
# while cmd_msg spelled the same check out inline — a test weaker than its
# own headline, which is how the contradiction survived.
stray="$(python3 -c "
import re
src = open('$HERE/lib/phone').read()
allowed = ('dev_shell', 'have_shell', '_probe_shell', 'cmd_screen', 'launch',
           'adb', 'adb_ready', 'need_adb')
bad = []
for m in re.finditer(r'^def (\w+)\(.*?(?=^def |\Z)', src, re.S|re.M):
    name, body = m.group(1), m.group(0)
    if name in allowed:
        continue
    if 'adb_ready(' in body or 'need_adb()' in body:
        bad.append(name)
print(' '.join(bad))
")"
if [ -z "$stray" ]; then ok "no verb hard-requires adb any more"
else bad "verbs still requiring adb directly:$stray"; fi

out="$(PHONE_NO_DEVICE=1 "$PHONE" tap 10 10 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "shizuku"; then
  ok "with no device at all, the error names shizuku as the fix"
else bad "no-shell error should mention shizuku (rc=$rc out=$out)"; fi

echo "== bulk data is READ FROM A FILE, never streamed through the bridge"
# Measured: rish truncates its stdout at a pipe-buffer boundary — a UI dump
# came back as exactly 8192 bytes. So the privileged side WRITES to /sdcard
# and the unprivileged side READS the file directly. Streaming large output
# through the bridge is the bug, not the transport.
if grep -q "def dev_read" "$HERE/lib/phone"; then
  ok "there is a file-based path for bulk device data"
else bad "no dev_read helper"; fi
if grep -q "8192\|truncat" "$HERE/lib/phone"; then
  ok "the truncation hazard is documented where it bites"
else bad "truncation hazard undocumented"; fi

echo "== a persistent shell, because the spawn IS the cost"
# Measured on the device: every rish invocation costs ~1.1s to start
# app_process and load its dex, while the command itself takes milliseconds.
# Paying that per verb is what made the agent feel glacial. One long-lived
# shell fed over a socket removes it.
SOCK="$T/shell.sock"
PHONE_SHELL_SOCK="$SOCK" PHONE_SHELL_ARGV=sh "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK" ] && break; sleep 0.4; done
if [ -S "$SOCK" ]; then ok "shelld starts and listens on a socket"
else bad "shelld did not create $SOCK"; fi

out="$(PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "echo hello-from-persistent" 2>&1 | tail -1)"
if [ "$out" = "hello-from-persistent" ]; then ok "a command round-trips through the persistent shell"
else bad "shelld round trip (got: $out)"; fi

# state must persist across calls — that is the point of one shell
PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "MARKER=stateful" >/dev/null 2>&1
out="$(PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "echo \$MARKER" 2>&1 | tail -1)"
if [ "$out" = "stateful" ]; then ok "the shell is the SAME process across calls"
else bad "shell state not preserved (got: $out)"; fi

# exit codes have to survive, or callers cannot tell success from failure
PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "true" >/dev/null 2>&1; a=$?
PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "(exit 7)" >/dev/null 2>&1; b=$?
if [ "$a" = "0" ] && [ "$b" = "7" ]; then ok "exit codes survive the socket"
else bad "exit codes lost (true=$a exit7=$b)"; fi

# A command CAN kill the shell (`exit`, a crash). The daemon must outlive it,
# or one bad command silently bricks every later call.
PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "exit 3" >/dev/null 2>&1
out="$(PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "echo survived" 2>&1 | tail -1)"
if [ "$out" = "survived" ]; then ok "the daemon respawns a shell that died"
else bad "daemon bricked after its shell exited (got: $out)"; fi

# output containing the sentinel must not truncate the response
out="$(PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "echo a; echo b; echo c" 2>&1 | tr '\n' ',')"
if printf '%s' "$out" | grep -q "a,b,c"; then ok "multi-line output comes back whole"
else bad "multiline through shelld (got: $out)"; fi

big="$(PHONE_SHELL_SOCK="$SOCK" "$PHONE" sh "seq 1 5000" 2>&1 | wc -l | tr -d ' ')"
if [ "$big" = "5000" ]; then ok "large output is not truncated at a buffer boundary"
else bad "large output truncated (got $big lines, wanted 5000)"; fi

PHONE_SHELL_SOCK="$SOCK" "$PHONE" shelld --stop >/dev/null 2>&1

echo "== a FAILED ui dump must never serve the previous screen"
# uiautomator dump fails routinely (animating surface, secure window, screen
# off). Writing to a fixed path meant those failures silently returned the
# LAST screen, and `find` then handed out coordinates for something no longer
# there — the agent taps a real button believing it saw it.
STUB="$T/stubbin"; mkdir -p "$STUB"
printf '#!/bin/sh\ncase "$1" in\n  dump) exit 1 ;;\n  *) exit 0 ;;\nesac\n' > "$STUB/uiautomator"
printf '#!/bin/sh\nexit 0\n' > "$STUB/mkdir_ok"
chmod +x "$STUB/uiautomator"
SOCK2="$T/stale.sock"
PHONE_SHELL_SOCK="$SOCK2" PHONE_SHELL_ARGV=sh "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK2" ] && break; sleep 0.4; done
out="$(PATH="$STUB:$PATH" PHONE_SHELL_SOCK="$SOCK2" "$PHONE" ui 2>&1)"; rc=$?
if [ $rc -ne 0 ] && ! printf '%s' "$out" | grep -qE "^[0-9]+,[0-9]+"; then
  ok "a failed dump exits non-zero and emits NO screen content"
else bad "STALE SCREEN served after a failed dump (rc=$rc out=$(printf '%s' "$out" | head -2))"; fi
PHONE_SHELL_SOCK="$SOCK2" "$PHONE" shelld --stop >/dev/null 2>&1

echo "== the liveness check must not cost a process spawn"
if grep -q "_SHELL_OK" "$HERE/lib/phone" && \
   grep -A6 "def _probe_shell" "$HERE/lib/phone" | grep -q "shelld_call"; then
  ok "have_shell is memoised and asks the daemon before spawning anything"
else bad "have_shell still probes by spawning"; fi

echo "== a hung command must not wedge the daemon for everyone else"
# The accept loop is single-threaded: without a deadline, one command that
# never returns (a uiautomator dump on a screen that never goes idle) blocks
# every later client forever, making the fast path slower than the spawn path
# it replaced.
SOCK3="$T/wedge.sock"
PHONE_SHELL_SOCK="$SOCK3" PHONE_SHELL_ARGV=sh PHONE_SHELL_DEADLINE=3 \
  "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK3" ] && break; sleep 0.4; done
start=$(date +%s)
PHONE_SHELL_SOCK="$SOCK3" "$PHONE" sh "sleep 30" >/dev/null 2>&1; hrc=$?
mid=$(date +%s)
out="$(PHONE_SHELL_SOCK="$SOCK3" "$PHONE" sh "echo recovered" 2>&1 | tail -1)"
end=$(date +%s)
if [ $((mid-start)) -le 12 ]; then ok "a hung command is abandoned at the deadline ($((mid-start))s)"
else bad "hung command was not bounded ($((mid-start))s)"; fi
if [ "$out" = "recovered" ] && [ $((end-mid)) -le 12 ]; then
  ok "the daemon still serves the NEXT client promptly after a hang"
else bad "daemon wedged after a hang (out=$out took $((end-mid))s)"; fi
PHONE_SHELL_SOCK="$SOCK3" "$PHONE" shelld --stop >/dev/null 2>&1

echo "== type must preserve real text, and admit what it dropped"
# The strip-regex silently deleted apostrophes, =, $, <, >, |, backtick and
# more, then reported success by counting WORDS WHOSE EXIT CODE WAS 0 — which
# says nothing about what arrived. An actuator that lies about what it typed
# is worse than one that fails.
# Assert the ROUND TRIP through a shell parser, not the literal bytes:
# shlex correctly emits 'Don'"'"'t' for Don't, which is how a shell actually
# delivers an apostrophe. Checking for the literal would fail on correct code.
out="$("$PHONE" type --print-only "Don't panic: x=2 & y=7 (ok)" 2>&1)"
if printf '%s' "$out" | python3 -c "
import shlex, sys
words = []
for line in sys.stdin.read().splitlines():
    if line.startswith('input text '):
        words.append(shlex.split(line)[-1])
got = ' '.join(words)
want = \"Don't panic: x=2 & y=7 (ok)\"
print('OK' if got == want else 'GOT:' + got)
" | grep -q '^OK$'; then
  ok "type delivers the exact text through the shell (apostrophes, =, &)"
else bad "type mangles text (got: $out)"; fi
if printf '%s' "$out" | grep -q "input text"; then
  ok "--print-only shows the exact input text argv"
else bad "--print-only should show the argv (got: $out)"; fi

echo "== output with NO trailing newline must not hang the shell daemon"
# The sentinel is emitted on its own line after the command. If the command's
# output does not end in a newline, the sentinel lands on the SAME line and
# the startswith() check never matches — so the daemon waited the full
# deadline on every such command. Measured: `head -c 100 file` took 75.20s
# and returned rc=124, with the data present the whole time.
SOCK4="$T/nonl.sock"
PHONE_SHELL_SOCK="$SOCK4" PHONE_SHELL_ARGV=sh PHONE_SHELL_DEADLINE=8 \
  "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK4" ] && break; sleep 0.4; done
st=$(date +%s)
out="$(PHONE_SHELL_SOCK="$SOCK4" "$PHONE" sh "printf 'no-trailing-newline'" 2>&1)"; rc=$?
el=$(( $(date +%s) - st ))
if [ "$rc" = "0" ] && [ $el -le 5 ]; then
  ok "a command with no trailing newline returns immediately (${el}s)"
else bad "no-newline output hung or failed (rc=$rc ${el}s out=$out)"; fi
if printf '%s' "$out" | grep -q "no-trailing-newline"; then
  ok "and its output is intact"
else bad "no-newline output lost (got: $out)"; fi
# and a normal newline-terminated command must not gain a spurious blank line
out="$(PHONE_SHELL_SOCK="$SOCK4" "$PHONE" sh "echo one; echo two" 2>&1 | tr '\n' ',')"
if [ "$out" = "one,two," ] || [ "$out" = "one,two" ]; then
  ok "newline-terminated output is unchanged (no injected blank line)"
else bad "newline handling changed output (got: $out)"; fi
PHONE_SHELL_SOCK="$SOCK4" "$PHONE" shelld --stop >/dev/null 2>&1

echo "== launching an app needs no shell tier (am start works as the app UID)"
# Measured on the device: `am start -n <component>` succeeds from Termux with
# shizuku down and adb off. So losing the shell tier must not cost app
# launching — it is the first step of most tasks.
if grep -q "am start -n" "$HERE/lib/phone" && \
   grep -B6 "am start -n" "$HERE/lib/phone" | grep -qiE "level 1|no shell|without a shell"; then
  ok "open has a level-1 launch path documented at the call site"
else bad "open still requires a shell to launch an app"; fi

echo "== the watchdog must REPORT a lost tier, not just degrade silently"
# Shizuku's service can stop while the phone stays up (observed: up 2 days,
# service gone). Level 1 keeps working, which is by design — but if nobody is
# told, the phone quietly loses half its capability and the agent starts
# refusing things for reasons the human cannot see.
if python3 -c "
import re
src = open('$HERE/lib/phone').read()
m = re.search(r'WATCHDOG_STEPS = \((.*?)\n\)', src, re.S)
import sys
sys.exit(0 if (m and 'shizuku' in m.group(1)) else 1)
"; then
  ok "the watchdog checks the shell tier"
else bad "watchdog does not check shizuku"; fi
if grep -q "tier_notice\|termux-notification.*shizuku\|notify_tier" "$HERE/lib/phone"; then
  ok "a lost shell tier raises a notification the human can act on"
else bad "a lost tier is silent"; fi

echo "== the lease: only one driver may act on the phone at a time"
# Two agents interleaving taps is not a race that produces a wrong pixel; it
# is one agent typing into another's chat. The lease lives in shelld because
# its accept loop is single-threaded, which makes check-and-set atomic for
# free, and because it arbitrates local, remote and cockpit callers alike.
SOCK5="$T/lease.sock"
PHONE_SHELL_SOCK="$SOCK5" PHONE_SHELL_ARGV=sh "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK5" ] && break; sleep 0.4; done
export PHONE_SHELL_SOCK="$SOCK5"

out="$("$PHONE" lease acquire --as tongs 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "a free phone can be leased"
else bad "acquire failed (rc=$rc out=$out)"; fi

out="$("$PHONE" lease acquire --as intruder 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -q "tongs"; then
  ok "a second driver is refused, and told who holds it"
else bad "second acquire should fail naming the holder (rc=$rc out=$out)"; fi

out="$("$PHONE" lease acquire --as tongs 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "the holder may re-acquire (renew) its own lease"
else bad "holder re-acquire failed (rc=$rc out=$out)"; fi

out="$("$PHONE" lease status 2>&1)"
if printf '%s' "$out" | grep -q "tongs"; then ok "status names the holder"
else bad "status (got: $out)"; fi

# A mutating verb from a NON-holder must refuse rather than interleave.
out="$(PHONE_ACTOR=intruder "$PHONE" key HOME 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "lease\|held by"; then
  ok "a mutating verb from a non-holder is refused"
else bad "non-holder was allowed to act (rc=$rc out=$out)"; fi

# Reads stay free: watching must never require taking the wheel.
out="$(PHONE_ACTOR=onlooker "$PHONE" notifs --from "$T/notifs.json" 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "reads are not gated by the lease"
else bad "a read was blocked by the lease (rc=$rc)"; fi

out="$("$PHONE" lease release --as tongs 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "the holder can release"
else bad "release failed (rc=$rc out=$out)"; fi
out="$("$PHONE" lease acquire --as intruder 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "and the phone is free again"
else bad "still held after release (rc=$rc out=$out)"; fi

# A crashed holder must not lock the phone forever.
"$PHONE" lease release --as intruder >/dev/null 2>&1
"$PHONE" lease acquire --as ghost --ttl 1 >/dev/null 2>&1
sleep 2
out="$("$PHONE" lease acquire --as tongs 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then ok "an expired lease is reclaimable (a crashed holder does not brick the phone)"
else bad "expired lease still held (rc=$rc out=$out)"; fi
"$PHONE" lease release --as tongs >/dev/null 2>&1
"$PHONE" shelld --stop >/dev/null 2>&1
unset PHONE_SHELL_SOCK

echo "== the ledger must be a TRACE, not a list of claims"
# "open keep -> opened" says what was attempted and asserted. It does not say
# WHO asked, whether the underlying command actually succeeded, or how long
# it took — so a slow tap and a failed one read identically, and a request
# cannot be followed back to its requester.
LED2="$T/trace.jsonl"
PHONE_LEDGER="$LED2" PHONE_ACTOR=tongs PHONE_NO_DEVICE=1 "$PHONE" msg whatsapp \
  --to "+16175551234" --text "trace me" --send >/dev/null 2>&1
if python3 -c "
import json
rows=[json.loads(l) for l in open('$LED2') if l.strip()]
a=rows[-1]
assert a.get('actor')=='tongs', 'actor missing: %r' % a
assert isinstance(a.get('ms'), (int,float)), 'ms missing: %r' % a
assert 'rc' in a, 'rc missing: %r' % a
print('ok')" 2>/dev/null | grep -q ok; then
  ok "an entry carries actor, elapsed ms, and an exit code"
else bad "ledger is not a trace (got: $(tail -1 "$LED2" 2>/dev/null))"; fi

out="$(PHONE_LEDGER="$LED2" "$PHONE" log --limit 3 --actor tongs 2>&1)"
if printf '%s' "$out" | grep -q "tongs"; then
  ok "log can be filtered to one actor (who did this?)"
else bad "log --actor (got: $out)"; fi

echo "== look: the screen an agent can actually afford to read"
# `ui` dumps every node with text — hundreds of lines on a real app, most of
# it container ids the agent can do nothing with. A model paying that on
# every step burns its context on scaffolding. `look` is the same screen,
# summarised: what app, and the things that can actually be acted on.
out="$("$PHONE" look --from "$T/ui.xml" 2>&1)"
if printf '%s' "$out" | grep -qi "whatsapp"; then
  ok "look names the app in the foreground"
else bad "look should name the app (got: $out)"; fi
if printf '%s' "$out" | grep -q "peer peer-user" && printf '%s' "$out" | grep -q "Send"; then
  ok "look keeps the things you can act on"
else bad "look dropped actionable elements (got: $out)"; fi
if printf '%s' "$out" | grep -qE "^\s*[0-9]+,[0-9]+"; then
  ok "look keeps coordinates so a tap needs no second call"
else bad "look has no coordinates (got: $out)"; fi
# The container ids ui emits (conversation, drag_layer, scrim_view) are
# scaffolding — an agent can do nothing with them and they dominate the dump.
if ! printf '%s' "$out" | grep -q "scrim_view"; then
  ok "look drops container scaffolding"
else bad "look kept scaffolding"; fi
ui_lines=$("$PHONE" ui --from "$T/ui.xml" 2>/dev/null | wc -l | tr -d ' ')
look_lines=$("$PHONE" look --from "$T/ui.xml" 2>/dev/null | wc -l | tr -d ' ')
if [ "$look_lines" -le "$ui_lines" ]; then
  ok "look is no larger than ui ($look_lines vs $ui_lines lines)"
else bad "look is bigger than ui ($look_lines vs $ui_lines)"; fi

echo "== the skill and the CLI must agree"
r="$(python3 "$HERE/scripts/check-skill-verbs.py" 2>&1)"
if [ "$r" = "agree" ]; then
  ok "every verb the skill teaches exists in the CLI"
else bad "$r"; fi

echo "== the skill states the habits that keep an agent honest"
for must in "look" "lease" "Never guess" "SPOKEN"; do
  if grep -qi -- "$must" "$HERE/docs/phone-skill.md"; then
    ok "skill covers: $must"
  else bad "skill missing: $must"; fi
done

echo "== --device: drive a phone from somewhere else, over a forwarded socket"
# The on-phone agent is pinned to an old claude on a slow CPU and keeps
# losing its session. The controller belongs off-device — which only works
# if the CLI can address a remote phone. It does that by forwarding shelld's
# socket, so sshd never forks a shell (the ~0.45s that made this feel slow).
out="$("$PHONE" --device aadarshs-pixel-10 --print-link 2>&1)"
if printf '%s' "$out" | grep -q "ssh" && printf '%s' "$out" | grep -q -- "-L"; then
  ok "--device knows how to build the forward"
else bad "--print-link (got: $out)"; fi
if printf '%s' "$out" | grep -q "phone-shell.sock"; then
  ok "the forward targets the device's shelld socket"
else bad "forward target wrong (got: $out)"; fi
# A stream-local forward must NOT be handed to an existing mux master: it is
# accepted and silently never created. Measured, and it cost real time.
if printf '%s' "$out" | grep -q "ControlPath=none"; then
  ok "the forward uses a dedicated connection, not the mux"
else bad "forward would be swallowed by an existing master (got: $out)"; fi
out="$("$PHONE" --device "bad name; rm -rf" --print-link 2>&1)"; rc=$?
if [ $rc -ne 0 ]; then ok "a hostile device name is refused"
else bad "device name not validated (out=$out)"; fi

echo "== two drivers racing for the lease: exactly one wins"
# The lease was correct only because shelld's accept loop happens to be
# single-threaded — correctness resting on a distant, unrelated property.
# Make shelld concurrent (the obvious future optimisation) and it goes racy
# with no test failing and nothing erroring; you just occasionally get two
# drivers, which is precisely what it exists to prevent. This asserts the
# invariant itself rather than the property it used to lean on.
SOCK6="$T/race.sock"
PHONE_SHELL_SOCK="$SOCK6" PHONE_SHELL_ARGV=sh "$PHONE" shelld --start >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCK6" ] && break; sleep 0.4; done
wins="$(python3 -c "
import subprocess, threading, os
env = dict(os.environ); env['PHONE_SHELL_SOCK'] = '$SOCK6'
won = []
lock = threading.Lock()
def go(name):
    r = subprocess.run(['$PHONE', 'lease', 'acquire', '--as', name],
                       capture_output=True, env=env)
    if r.returncode == 0:
        with lock:
            won.append(name)
threads = [threading.Thread(target=go, args=('driver%d' % i,)) for i in range(8)]
[t.start() for t in threads]; [t.join() for t in threads]
print(len(won))
")"
if [ "$wins" = "1" ]; then ok "8 simultaneous acquires, exactly 1 winner"
else bad "the lease is racy: $wins winners out of 8"; fi
"$PHONE" shelld --stop >/dev/null 2>&1

echo "== --device must carry the termux-api verbs too, not just shell ones"
# The forwarded socket runs commands at SHELL uid. Termux's own binaries live
# in an app-private prefix that shell uid cannot read, so notifs/say/battery
# cannot ride that path — they have to run as the app, on the device. A
# --device that silently only covered half the verbs would be worse than one
# that covered none, because the failure looks like a broken phone.
out="$("$PHONE" --device aadarshs-pixel-10 --print-plan notifs --limit 2 2>&1)"
if printf '%s' "$out" | grep -q "ssh" && printf '%s' "$out" | grep -q "notifs"; then
  ok "a termux-api verb is planned to run ON the device"
else bad "termux verb routing (got: $out)"; fi
out="$("$PHONE" --device aadarshs-pixel-10 --print-plan tap 1 2 2>&1)"
if printf '%s' "$out" | grep -qi "socket\|forward\|local"; then
  ok "a shell verb still takes the fast forwarded path"
else bad "shell verb routing (got: $out)"; fi

echo "== the actor has to cross the ssh hop, or the trace says anon"
# The ledger's whole job is WHO. A termux verb runs on the DEVICE, so it is
# the device's `phone` that writes the record -- and PHONE_ACTOR is an
# environment variable on the CONTROLLER, which ssh does not forward. So
# every notifs/clip/where/photo/listen from a remote controller logged as
# "anon": the reads of personal data, which are exactly the ones the record
# exists for, and exactly the ones that cannot be traced back to who asked.
out="$(PHONE_ACTOR=tongs "$PHONE" --device aadarshs-pixel-10 --print-plan notifs 2>&1)"
if printf '%s' "$out" | grep -q "PHONE_ACTOR=tongs"; then
  ok "the remote command carries the actor"
else bad "actor lost across the hop (got: $out)"; fi

# ...and it must survive being a hostile string, since it lands in a shell.
# Grepping the plan is not enough here, and getting this wrong is subtle:
# shlex.quote("PHONE_ACTOR=a; rm") yields "'PHONE_ACTOR=a; rm'", which reads
# as a fully quoted word -- and a word whose NAME part is quoted stops being
# an assignment and becomes a command name. The far shell would hunt for a
# binary called that and never run phone at all. It greps fine and is
# silently broken, so ask a real shell instead: run the planned remote
# string with a stub `phone` on PATH and see what it actually receives.
STUB="$(mktemp -d)"
cat > "$STUB/phone" <<'STUBEOF'
#!/bin/sh
printf 'ACTOR=[%s]\n' "$PHONE_ACTOR"
printf 'ARGV=[%s]\n' "$*"
STUBEOF
chmod +x "$STUB/phone"
EVIL='a; rm -rf /'
plan="$(PHONE_ACTOR="$EVIL" "$PHONE" --device aadarshs-pixel-10 --print-plan notifs 2>&1)"
# The plan line is "run on the device (...): ssh <opts> <host> <remote>".
# The remote command is everything after the hostname.
remote="${plan#*aadarshs-pixel-10 }"
got="$(PATH="$STUB:$PATH" sh -c "$remote" 2>&1)"
rm -rf "$STUB"
if [ "$got" = "ACTOR=[$EVIL]
ARGV=[notifs]" ]; then
  ok "a real shell sees the actor as a value and still runs phone"
else bad "hostile actor mangles the far command (got: $got)"; fi

# An unset actor must not send a bare PHONE_ACTOR= that overrides whatever
# the device itself would have said.
out="$(env -u PHONE_ACTOR "$PHONE" --device aadarshs-pixel-10 --print-plan notifs 2>&1)"
if ! printf '%s' "$out" | grep -q "PHONE_ACTOR"; then
  ok "no actor set means no actor forced on the device"
else bad "empty actor forced across (got: $out)"; fi

echo "== play must not report playing when it opened a web page"
# The third sibling of the wa.me shape. play hands a https URL to launch(),
# which opens a BROWSER when the app is absent and returns 0 -- and play
# recorded "act" BEFORE even attempting, so a launch that died outright still
# left a row asserting music was playing.
#
# The trap in fixing it: `apps` lists THIRD-PARTY packages only, and YouTube
# and YouTube Music are SYSTEM packages on a Pixel. Checking against that
# list would refuse ytmusic on a phone that has it -- swapping a false
# success for a false refusal, which is not an improvement.
PT="$(mktemp -d)"
mkplaystub() {   # $1 = packages `pm list packages <filter>` should report
  cat > "$PT/adb" <<EOF
#!/bin/sh
case "\$1" in
  devices) printf 'List of devices attached\nemulator-5554\tdevice\n' ;;
  shell)
    case "\$*" in
      *"pm list packages -3"*) printf 'package:com.spotify.music\n' ;;
      *"pm list packages"*)
        for p in $1; do
          case "\$*" in *"\$p"*) printf 'package:%s\n' "\$p" ;; esac
        done ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$PT/adb"
}

# A phone with YouTube Music as a SYSTEM app and no Spotify.
mkplaystub "com.google.android.apps.youtube.music com.google.android.youtube"
out="$(PATH="$PT:$PATH" TMPDIR="$PT" PHONE_LEDGER="$PT/p1.jsonl" \
       "$PHONE" play "some song" --app spotify 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "not installed\|is not on"; then
  ok "play refuses when the music app is absent"
else bad "play opened a browser and called it playing (rc=$rc out=$out)"; fi
# Assert on the ABSENCE of a success row, not on a string that does not
# exist yet -- otherwise this passes today for no reason at all.
if [ ! -s "$PT/p1.jsonl" ] || python3 -c "
import json
rows=[json.loads(l) for l in open('$PT/p1.jsonl') if l.strip()]
ok=[r for r in rows if r.get('verb')=='play' and not r.get('rc')]
assert not ok, ok
" 2>/dev/null; then
  ok "and no successful play was claimed in the ledger"
else bad "ledger claims music that never played"; fi

# The false-refusal guard: ytmusic is a system package, absent from `apps`.
out="$(PATH="$PT:$PATH" TMPDIR="$PT" PHONE_LEDGER="$PT/p2.jsonl" \
       "$PHONE" play "some song" --app ytmusic 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  ok "a SYSTEM music app is not mistaken for a missing one"
else bad "system app wrongly refused (rc=$rc out=$out)"; fi

# A launch that fails outright must leave a FAILURE row, not silence and not
# the success row play used to write before it had any evidence.
out="$(TMPDIR="$PT" PHONE_NO_DEVICE=1 PHONE_LEDGER="$PT/p3.jsonl" \
       "$PHONE" play "some song" --app ytmusic 2>&1)"; rc=$?
if [ $rc -ne 0 ] && [ -s "$PT/p3.jsonl" ] && \
   python3 -c "
import json,sys
rows=[json.loads(l) for l in open('$PT/p3.jsonl') if l.strip()]
a=rows[-1]
assert a['verb']=='play', a
assert a.get('rc'), a
assert 'fail' in str(a.get('result','')).lower(), a
" 2>/dev/null; then
  ok "a failed launch is recorded as a failure, not as playing"
else bad "failed launch record (rc=$rc ledger=$(cat "$PT/p3.jsonl" 2>/dev/null))"; fi
rm -rf "$PT"

echo "== msg must not report a draft on a phone without the app"
# cmd_open already learned this: "asking for whatsapp and getting a browser
# open on wa.me is not what anyone means". msg never did. It hands
# https://wa.me/<n> straight to launch(), which opens a BROWSER when
# WhatsApp is absent, returns 0, and records "drafted" -- so the agent tells
# the human a message is waiting to be tapped, and there is no message. In
# the one verb where the whole point is that it can really send.
AT="$(mktemp -d)"
mkstub() {   # $1 = package list emitted by `pm list packages -3`
  cat > "$AT/adb" <<EOF
#!/bin/sh
case "\$1" in
  devices) printf 'List of devices attached\nemulator-5554\tdevice\n' ;;
  shell)   case "\$*" in
             *"pm list packages"*) printf '$1' ;;
             *) exit 0 ;;
           esac ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$AT/adb"
}

mkstub 'package:com.google.android.keep\npackage:com.spotify.music\n'
out="$(PATH="$AT:$PATH" TMPDIR="$AT" PHONE_LEDGER="$AT/led.jsonl" \
       "$PHONE" msg whatsapp --to "+16175551234" --text "hi" 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "not installed\|no whatsapp\|is not on"; then
  ok "msg refuses honestly when WhatsApp is not on the device"
else bad "msg drafted into thin air (rc=$rc out=$out)"; fi
if [ ! -s "$AT/led.jsonl" ] || ! grep -q '"drafted"' "$AT/led.jsonl"; then
  ok "and no draft was claimed in the ledger"
else bad "ledger claims a draft that cannot exist"; fi
# --send is the dangerous half: it must refuse on the same evidence, and
# BEFORE anything is launched, not discover it at the send button.
out="$(PATH="$AT:$PATH" TMPDIR="$AT" PHONE_LEDGER="$AT/led3.jsonl" \
       "$PHONE" msg whatsapp --to "+16175551234" --text "hi" --send 2>&1)"; rc=$?
if [ $rc -ne 0 ] && printf '%s' "$out" | grep -qi "not installed"; then
  ok "--send refuses on the same evidence"
else bad "--send proceeded without the app (rc=$rc out=$out)"; fi

mkstub 'package:com.whatsapp\npackage:com.google.android.keep\n'
out="$(PATH="$AT:$PATH" TMPDIR="$AT" PHONE_LEDGER="$AT/led2.jsonl" \
       "$PHONE" msg whatsapp --to "+16175551234" --text "hi" 2>&1)"; rc=$?
if [ $rc -eq 0 ] && grep -q '"drafted"' "$AT/led2.jsonl" 2>/dev/null; then
  ok "with WhatsApp installed it still drafts, and says so"
else bad "installed-app draft path broke (rc=$rc out=$out)"; fi
# msg has the same die-before-record gap play had: launch() dies loudly when
# it cannot open anything, and the record was written only after it returned.
# A message the agent tried and failed to draft left no trace at all, which
# reads as never having been asked.
out="$(TMPDIR="$AT" PHONE_NO_DEVICE=1 PHONE_LEDGER="$AT/led4.jsonl" \
       "$PHONE" msg whatsapp --to "+16175551234" --text "hi" 2>&1)"; rc=$?
if [ $rc -ne 0 ] && [ -s "$AT/led4.jsonl" ] && python3 -c "
import json
rows=[json.loads(l) for l in open('$AT/led4.jsonl') if l.strip()]
a=rows[-1]
assert a['verb']=='msg', a
assert a.get('rc'), a
assert 'fail' in str(a.get('result','')).lower(), a
" 2>/dev/null; then
  ok "a draft that could not be opened is recorded as a failure"
else bad "msg failed launch record (rc=$rc led=$(cat "$AT/led4.jsonl" 2>/dev/null))"; fi
rm -rf "$AT"

echo "== a remote hop is the LAST hop"
# If the device's own environment names a device (a two-phone fleet, a
# stray export), the far `phone` would hop onward -- and every row it
# returned would still be labelled with THIS device's name, because that is
# the only name the caller knows. The origin column would then quietly
# attribute a third machine's actions to the phone in your hand, which is
# worse than not having the column. Cheaper to make the far side incapable
# of hopping than to teach the labelling about hops it cannot see.
HT="$(mktemp -d)"
cat > "$HT/phone" <<'HEOF'
#!/bin/sh
printf 'DEVICE=[%s]\n' "$PHONE_DEVICE"
HEOF
chmod +x "$HT/phone"
plan="$(PHONE_ACTOR=tongs "$PHONE" --device aadarshs-pixel-10 --print-plan notifs 2>&1)"
remote="${plan#*aadarshs-pixel-10 }"
got="$(PATH="$HT:$PATH" PHONE_DEVICE=some-other-phone sh -c "$remote" 2>&1)"
rm -rf "$HT"
if [ "$got" = "DEVICE=[]" ]; then
  ok "the far side cannot hop onward (PHONE_DEVICE cleared across the wire)"
else bad "far side could re-hop (got: $got)"; fi

echo "== the trace is split across two machines, so log has to merge it"
# Shell-tier verbs (tap, type, look, key) execute through the forwarded
# socket, so the CONTROLLER's process writes their ledger line -- on the
# controller. Termux verbs run on the device, so the DEVICE writes theirs.
# Two ledgers, on two machines, each holding half the story: `phone log`
# here showed taps with no reads, `phone log` there showed reads with no
# taps, and neither could answer "what has been done to my phone, by whom".
MT="$(mktemp -d)"
cat > "$MT/ledger.jsonl" <<'LEOF'
{"ts": 100.0, "verb": "tap", "kind": "act", "actor": "tongs", "ms": 12, "rc": 0, "detail": "933 2119"}
{"ts": 300.0, "verb": "type", "kind": "act", "actor": "tongs", "ms": 20, "rc": 0, "detail": "hello"}
LEOF
# A stub ssh standing in for the device's own ledger.
cat > "$MT/ssh" <<'SEOF'
#!/bin/sh
echo '[{"ts": 200.0, "verb": "notifs", "kind": "read", "actor": "tongs", "ms": 9, "rc": 0, "detail": "all"}]'
SEOF
chmod +x "$MT/ssh"
# TMPDIR too, so no forwarded socket left over from a real run can be
# found and make this pass for a reason the test did not arrange.
out="$(PATH="$MT:$PATH" TMPDIR="$MT" PHONE_LEDGER="$MT/ledger.jsonl" \
       "$PHONE" --device aadarshs-pixel-10 log --json 2>&1)"
if printf '%s' "$out" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
verbs = [r["verb"] for r in rows]
assert verbs == ["type", "notifs", "tap"], verbs      # newest first, interleaved
o = {r["verb"]: r.get("origin") for r in rows}
assert o["notifs"] == "aadarshs-pixel-10", o
assert o["tap"] == "here" and o["type"] == "here", o
' 2>/dev/null; then
  ok "log merges both ledgers in time order, each row saying where it came from"
else bad "split ledger not merged (got: $out)"; fi

# The device being unreachable must not make the local half vanish -- a
# partial answer is worth having, an empty one that looks complete is not.
cat > "$MT/ssh" <<'SEOF'
#!/bin/sh
echo "ssh: connect to host aadarshs-pixel-10 port 22: Host is down" >&2
exit 255
SEOF
chmod +x "$MT/ssh"
out="$(PATH="$MT:$PATH" TMPDIR="$MT" PHONE_LEDGER="$MT/ledger.jsonl" \
       "$PHONE" --device aadarshs-pixel-10 log 2>&1)"
rc=$?
if printf '%s' "$out" | grep -q "tap" && printf '%s' "$out" | grep -qi "could not read\|unreachable"; then
  ok "an unreachable device still shows local rows, and says the half is missing"
else bad "unreachable device handling (got: $out)"; fi
# ...and it must not exit 0. A caller asking what was done to the phone would
# read success as "and that was all of it".
if [ "$rc" -ne 0 ]; then
  ok "a half-trace exits non-zero"
else bad "partial trace exited 0 (looked complete)"; fi
rm -rf "$MT"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
