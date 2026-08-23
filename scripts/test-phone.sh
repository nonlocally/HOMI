#!/usr/bin/env bash
# The `phone` verb layer: one CLI that turns an Android device (Termux + adb,
# no root) into a surface any fabric agent can drive. The testable core is the
# PARSING — the UI tree and the notification inbox — because that is what an
# LLM actually reasons over. Device I/O is shelled out and stubbed here.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHONE="$HERE/lib/phone"
T="$(mktemp -d /tmp/phone-test.XXXXXX)"
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

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
