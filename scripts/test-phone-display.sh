#!/usr/bin/env bash
# Multi-display: one phone, several screens, one driver each.
#
# Two halves. The first runs anywhere — the tree merging, the device-side
# filter, the flag threading and the per-display lease all work against
# fixtures and a local `sh` daemon, because that is where the logic is. The
# second half needs the phone and is skipped without it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHONE="$HERE/lib/phone"
T="$(mktemp -d /tmp/phone-display-test.XXXXXX)"
export PHONE_LEDGER="$T/ledger.jsonl"
pass=0; fail=0
ok(){ pass=$((pass+1)); printf 'ok   %s\n' "$*"; }
bad(){ fail=$((fail+1)); printf 'FAIL %s\n' "$*"; }
cleanup(){ "$PHONE" shelld --stop >/dev/null 2>&1; rm -rf "$T"; }
trap cleanup EXIT

# Load `phone` as a module. It has no .py suffix and is meant to stay one
# file (the device runs a single deployed copy, not a checkout), so the
# import is by path.
py(){ PHONE_PATH="$PHONE" python3 - "$@"; }
PRELUDE='
import importlib.util, importlib.machinery, os, sys
spec = importlib.util.spec_from_loader(
    "phonemod", importlib.machinery.SourceFileLoader(
        "phonemod", os.environ["PHONE_PATH"]))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
'

# A --windows dump with two displays. Window order is TOP-FIRST, as the real
# thing produces it: the status bar (layer 1) before the app (layer 0).
cat > "$T/win.xml" <<'XML'
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><displays><display id="0"><window index="0" title="" bounds="[0,0][1080,63]" active="false" focused="false" id="1" layer="1" type="TYPE_SYSTEM"><hierarchy rotation="0"><node index="0" text="statusbar-zero" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,63]" /></hierarchy></window><window index="1" title="" bounds="[0,0][1080,2400]" active="true" focused="true" id="2" layer="0" type="TYPE_APPLICATION"><hierarchy rotation="0"><node index="0" text="launcher-zero" resource-id="" class="android.widget.FrameLayout" package="com.google.android.apps.nexuslauncher" content-desc="" clickable="true" enabled="true" bounds="[0,0][1080,2400]" /></hierarchy></window></display><display id="7"><window index="0" title="" bounds="[0,0][1080,63]" active="false" focused="false" id="3" layer="1" type="TYPE_SYSTEM"><hierarchy rotation="0"><node index="0" text="statusbar-seven" resource-id="" class="android.widget.FrameLayout" package="com.android.systemui" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,63]" /></hierarchy></window><window index="1" title="" bounds="[0,0][1080,2400]" active="true" focused="true" id="4" layer="0" type="TYPE_APPLICATION"><hierarchy rotation="0"><node index="0" text="calendar-seven" resource-id="" class="android.widget.FrameLayout" package="com.google.android.calendar" content-desc="" clickable="true" enabled="true" bounds="[0,0][1080,2400]" /></hierarchy></window></display></displays>
XML

# ---------------------------------------------------------------- merging
out="$(py <<PY
$PRELUDE
x = open("$T/win.xml").read()
h = m._hierarchy_for_display(x, 7)
print("root_ok" if h.startswith("<hierarchy") else "root_bad")
print("has_seven" if "calendar-seven" in h else "no_seven")
print("no_zero" if "launcher-zero" not in h else "leaked_zero")
PY
)"
[ "$(echo "$out" | sed -n 1p)" = "root_ok" ] && ok "merge yields a <hierarchy> root" || bad "merge root: $out"
echo "$out" | grep -q has_seven && ok "merge keeps the asked-for display" || bad "merge lost display 7"
echo "$out" | grep -q no_zero && ok "merge excludes every other display" || bad "merge leaked display 0"

# The ordering is the whole reason this function exists: readers below it
# treat a LATER sibling as drawn on top, and --windows lists windows the
# other way round. Status bar (layer 1) must end up AFTER the app.
out="$(py <<PY
$PRELUDE
h = m._hierarchy_for_display(open("$T/win.xml").read(), 7)
print("bottom_first" if h.index("calendar-seven") < h.index("statusbar-seven")
      else "top_first")
PY
)"
[ "$out" = "bottom_first" ] && ok "windows merge bottom-first (occlusion stays sane)" \
  || bad "window order is $out — topmost_clickable_at would invert"

# A plain single-display dump has to pass through untouched.
out="$(py <<PY
$PRELUDE
plain = '<hierarchy rotation="0"><node index="0" text="x" /></hierarchy>'
print("passthrough" if m._hierarchy_for_display(plain, 0) == plain else "mangled")
PY
)"
[ "$out" = "passthrough" ] && ok "a single-display dump is left alone" || bad "passthrough: $out"

# --------------------------------------------------------- device filter
# The sed has to survive a real shell and cut exactly one display out of a
# one-line document — this is what keeps the read under the corruption
# threshold, so it is not cosmetic.
filter="$(py <<PY
$PRELUDE
print(m.display_filter("$T/win.xml", 7))
PY
)"
# sed emits a trailing newline, so compare with whitespace squeezed out —
# the XML is valid either way and the merger below proves it parses.
got="$(eval "$filter" 2>/dev/null)"
flat="$(printf '%s' "$got" | tr -d '\n')"
case "$flat" in
  "<displays><display id=\"7\">"*"</display></displays>") ok "device filter brackets one display" ;;
  *) bad "device filter produced: ${flat:0:90}" ;;
esac
echo "$got" | grep -q calendar-seven && ok "device filter keeps display 7's tree" || bad "filter dropped 7"
if [ -z "$got" ]; then bad "device filter produced nothing"
elif echo "$got" | grep -q launcher-zero; then bad "filter leaked display 0"
else ok "device filter drops display 0"; fi
# And what it emits must be what the merger accepts.
out="$(printf '%s' "$got" > "$T/filtered.xml"; py <<PY
$PRELUDE
h = m._hierarchy_for_display(open("$T/filtered.xml").read(), 7)
print("chains" if "calendar-seven" in h else "broken")
PY
)"
[ "$out" = "chains" ] && ok "filter output feeds the merger" || bad "filter/merger mismatch: $out"

# ----------------------------------------------------------- flag shapes
out="$(py <<PY
$PRELUDE
m._DISPLAY[0] = 0
print("input[%s] am[%s]" % (m._dflag(), m._amflag()))
m._DISPLAY[0] = 4
print("input[%s] am[%s]" % (m._dflag(), m._amflag()))
PY
)"
[ "$(echo "$out" | sed -n 1p)" = "input[] am[]" ] \
  && ok "display 0 issues byte-identical commands" || bad "display 0 flags: $out"
[ "$(echo "$out" | sed -n 2p)" = "input[ -d 4] am[ --display 4]" ] \
  && ok "input takes -d, am start takes --display" || bad "display 4 flags: $out"

# `type` counts delivered characters from the plan, not from a string
# prefix — the prefix stopped matching once a flag went in front of `text`.
out="$("$PHONE" --display 4 type --print-only "ab cd" 2>&1)"
echo "$out" | grep -q -- "input -d 4 text ab" && ok "type carries the display" \
  || bad "type plan: $out"
echo "$out" | grep -q -- "input -d 4 keyevent 62" && ok "the space keyevent carries it too" \
  || bad "type space: $out"

# ------------------------------------------------------ lease per display
export PHONE_SHELL_SOCK="$T/shell.sock"
export PHONE_SHELL_ARGV=sh
"$PHONE" shelld --start >/dev/null 2>&1
if [ -S "$T/shell.sock" ]; then
  "$PHONE" --display 3 lease acquire --as driverA >/dev/null 2>&1
  "$PHONE" --display 4 lease acquire --as driverB >/dev/null 2>&1
  a="$("$PHONE" --display 3 lease status 2>&1 | awk '{print $1}')"
  b="$("$PHONE" --display 4 lease status 2>&1 | awk '{print $1}')"
  [ "$a" = "driverA" ] && [ "$b" = "driverB" ] \
    && ok "two displays, two holders, at the same time" \
    || bad "holders were '$a' and '$b'"

  "$PHONE" --display 3 lease acquire --as driverB >/dev/null 2>&1
  [ $? -eq 5 ] && ok "a held display refuses a second driver" \
    || bad "driverB took a display driverA holds"

  # The gate in main() is what actually stops a verb.
  PHONE_ACTOR=driverC "$PHONE" --display 3 tap 5 5 >/dev/null 2>&1
  [ $? -eq 5 ] && ok "a mutating verb is refused on someone else's display" \
    || bad "tap was not gated on display 3"

  # The built-in screen is a display like any other, and must not have been
  # taken by either of the above.
  z="$("$PHONE" lease status 2>&1)"
  [ "$z" = "free" ] && ok "holding a virtual display leaves display 0 free" \
    || bad "display 0 says: $z"

  "$PHONE" --display 3 lease release --as driverA >/dev/null 2>&1
  z="$("$PHONE" --display 3 lease status 2>&1)"
  [ "$z" = "free" ] && ok "release frees only that display" || bad "after release: $z"
  b="$("$PHONE" --display 4 lease status 2>&1 | awk '{print $1}')"
  [ "$b" = "driverB" ] && ok "the other display's lease survives" || bad "display 4: $b"
else
  echo "skip lease tests (no local daemon)"
fi
"$PHONE" shelld --stop >/dev/null 2>&1

# ------------------------------------------------- move / visible surface
h="$("$PHONE" display --help 2>&1)"
echo "$h" | grep -q -- "--visible" && ok "create has a --visible mode" \
  || bad "no --visible in display --help"
echo "$h" | grep -q "move" && ok "display can move an app between screens" \
  || bad "no move action"
# `;` separates overlay displays and `,` separates one display's flags — a
# parser that gets this backwards silently merges two screens into one.
out="$(py <<PY
$PRELUDE
m.dev_shell = lambda *a, **k: (0, "1080x2400/420;800x600/240")
print("|".join(m.overlay_specs()))
m.dev_shell = lambda *a, **k: (0, "null")
print("empty" if m.overlay_specs() == [] else "not-empty")
PY
)"
[ "$(echo "$out" | sed -n 1p)" = "1080x2400/420|800x600/240" ] \
  && ok "overlay specs split on ; not ," || bad "overlay parse: $out"
[ "$(echo "$out" | sed -n 2p)" = "empty" ] \
  && ok "an unset overlay setting reads as none" || bad "null parse: $out"

# ------------------------------------------------------------- the lanes
# A lane is a driver's own shelld. The default lane must stay exactly where
# it was, or every existing caller moves house.
out="$(py <<PY
$PRELUDE
os.environ.pop("PHONE_SHELL_SOCK", None)
os.environ["PREFIX"] = "/tmp"
os.environ.pop("PHONE_LANE", None)
print(m.shell_sock())
os.environ["PHONE_LANE"] = "alpha"
print(m.shell_sock())
print(m._laned("/a/b/phone-shell.sock"))
PY
)"
[ "$(echo "$out" | sed -n 1p)" = "/tmp/tmp/phone-shell.sock" ]   && ok "the default lane's socket is unchanged" || bad "default sock: $out"
[ "$(echo "$out" | sed -n 2p)" = "/tmp/tmp/phone-shell-alpha.sock" ]   && ok "a lane gets its own socket" || bad "lane sock: $out"
[ "$(echo "$out" | sed -n 3p)" = "/a/b/phone-shell-alpha.sock" ]   && ok "the remote socket is laned the same way" || bad "laned: $out"

out="$(py <<PY
$PRELUDE
os.environ["PHONE_LANE"] = "no spaces please"
try:
    m.lane()
    print("accepted")
except SystemExit:
    print("refused")
PY
)"
[ "$out" = "refused" ] && ok "a malformed lane name is refused" || bad "lane validation: $out"

# Two local daemons, two lanes, at once.
export PHONE_SHELL_ARGV=sh
unset PHONE_SHELL_SOCK
export PREFIX="$T"
mkdir -p "$T/tmp"
PHONE_LANE=alpha "$PHONE" shelld --start >/dev/null 2>&1
PHONE_LANE=beta  "$PHONE" shelld --start >/dev/null 2>&1
if [ -S "$T/tmp/phone-shell-alpha.sock" ] && [ -S "$T/tmp/phone-shell-beta.sock" ]; then
  ok "two lanes stand up two daemons"
  # Each lane's lease is its own, which is the trade lanes make: a driver
  # that takes a lane has taken itself out of the shared arbiter.
  PHONE_LANE=alpha "$PHONE" --display 3 lease acquire --as driverA >/dev/null 2>&1
  z="$(PHONE_LANE=beta "$PHONE" --display 3 lease status 2>&1)"
  [ "$z" = "free" ] && ok "a lane does not see another lane's lease"     || bad "lane beta saw: $z"
  z="$(PHONE_LANE=alpha "$PHONE" --display 3 lease status 2>&1 | awk '{print $1}')"
  [ "$z" = "driverA" ] && ok "and still holds its own" || bad "lane alpha: $z"
else
  bad "two lanes did not produce two sockets"
fi
PHONE_LANE=alpha "$PHONE" shelld --stop >/dev/null 2>&1
PHONE_LANE=beta  "$PHONE" shelld --stop >/dev/null 2>&1
unset PREFIX PHONE_SHELL_ARGV

# ------------------------------------------------------------ the device
DEV="${PHONE_TEST_DEVICE:-}"
if [ -z "$DEV" ]; then
  echo "skip device tests (set PHONE_TEST_DEVICE=<tailscale name> to run them)"
else
  unset PHONE_SHELL_SOCK PHONE_SHELL_ARGV
  ids="$("$PHONE" --device "$DEV" display ls 2>&1 | awk '{print $1}' | tr '\n' ' ')"
  echo "$ids" | grep -q 0 && ok "display ls finds the built-in screen" \
    || bad "display ls: $ids"

  fg_before="$("$PHONE" --device "$DEV" foreground 2>&1)"
  d="$("$PHONE" --device "$DEV" display create 2>&1 | tail -1)"
  case "$d" in
    ''|*[!0-9]*) bad "display create returned '$d'" ;;
    *)
      ok "display create -> id $d"
      # Pick an app that is not already somewhere. The suite used to hardcode
      # the clock and then fail whenever a previous run had left one open —
      # reporting the one-app-one-display guard, working exactly as designed,
      # as a broken launch.
      app=""
      for cand in clock calculator contacts photos; do
        if "$PHONE" --device "$DEV" --display "$d" open "$cand" >/dev/null 2>&1
        then app="$cand"; break; fi
      done
      if [ -z "$app" ]; then
        bad "no candidate app was free to launch on display $d"
      else
        fg="$("$PHONE" --device "$DEV" --display "$d" foreground 2>&1)"
        case "$fg" in
          *SecondaryDisplayLauncher*|*nexuslauncher*)
            bad "display $d still shows its launcher: $fg" ;;
          *) ok "$app launches onto display $d" ;;
        esac

        fg_after="$("$PHONE" --device "$DEV" foreground 2>&1)"
        [ "$fg_after" = "$fg_before" ] \
          && ok "the built-in screen was not disturbed" \
          || bad "display 0 changed: '$fg_before' -> '$fg_after'"

        "$PHONE" --device "$DEV" open "$app" >/dev/null 2>&1
        [ $? -eq 5 ] && ok "the same app is refused a second display" \
          || bad "one-app-one-display was not enforced"

        "$PHONE" --device "$DEV" --display "$d" look >/dev/null 2>&1 \
          && ok "look reads display $d" || bad "look failed on display $d"

        "$PHONE" --device "$DEV" --display "$d" screen --out "$T/d.png" \
          >/dev/null 2>&1
        # The magic bytes, not `grep -q PNG`: BSD grep exits 1 on binary
        # input even when the pattern is there, so that check reported every
        # good screenshot as a failure.
        magic="$(head -c 4 "$T/d.png" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
        [ "$magic" = "89504e47" ] \
          && ok "screen captures display $d ($(wc -c < "$T/d.png" | tr -d ' ') bytes)" \
          || bad "display $d produced no PNG (magic '$magic')"
      fi
      # move is the sanctioned way past the one-app-one-display guard.
      if [ -n "$app" ]; then
        "$PHONE" --device "$DEV" display move "$app" --to 0 >/dev/null 2>&1 \
          && ok "display move brings an app to the built-in screen" \
          || bad "display move to 0 failed"
        "$PHONE" --device "$DEV" display move "$app" --to "$d" >/dev/null 2>&1 \
          && ok "and moves it back off again" || bad "display move back failed"
      fi
      "$PHONE" --device "$DEV" display rm "$d" >/dev/null 2>&1 \
        && ok "display rm releases it" || bad "display rm failed"

      v="$("$PHONE" --device "$DEV" display create --visible 2>&1 | tail -1)"
      case "$v" in
        ''|*[!0-9]*) bad "display create --visible returned '$v'" ;;
        *) ok "a visible display appears (id $v)"
           "$PHONE" --device "$DEV" --display "$v" look >/dev/null 2>&1 \
             && ok "a visible display can still be read" \
             || bad "look failed on visible display $v"
           # It has no framebuffer of its own, and saying so is better than
           # handing back the built-in screen and calling it that display.
           err="$("$PHONE" --device "$DEV" --display "$v" screen --out "$T/v.png" 2>&1)"
           case "$err" in
             *"no framebuffer"*) ok "and says why it cannot be screenshotted" ;;
             *) bad "visible-display screen said: ${err:0:70}" ;;
           esac
           "$PHONE" --device "$DEV" display rm "$v" >/dev/null 2>&1 \
             && ok "a visible display is released too" || bad "rm visible failed"
           ;;
      esac
      "$PHONE" --device "$DEV" display rm --all >/dev/null 2>&1
      ;;
  esac
fi

echo
echo "passed $pass, failed $fail"
[ "$fail" -eq 0 ]
