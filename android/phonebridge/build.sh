#!/usr/bin/env bash
# Build, sign and (optionally) install phonebridge.
#
# No Gradle and no Android Studio. The app has no resources and no UI, which
# is what makes that practical: aapt2 link needs only a manifest, javac needs
# only android.jar, and the rest is d8 + apksigner. Roughly 200 lines of Java
# and four tool invocations.
#
#   ./build.sh              build and sign -> build/phonebridge.apk
#   ./build.sh install      also push it to the phone and grant listener access
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/build"
PKG="com.aadarwal.phonebridge"
LISTENER="$PKG/.Listener"
DEVICE="${PHONE_DEVICE:-aadarshs-pixel-10}"
API="${ANDROID_API:-36}"

SDK="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"
BT="$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1 || true)"
PLATFORM="$SDK/platforms/android-$API/android.jar"

need() {
  [ -e "$1" ] || { echo "missing: $1" >&2
                   echo "run: sdkmanager 'build-tools;${API}.0.0' 'platforms;android-${API}'" >&2
                   exit 1; }
}
need "$BT"
need "$PLATFORM"

rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/dex"

# 1. Manifest -> a resource-less APK skeleton. No res/ dir at all: aapt2 link
#    is happy without one, and every resource we do not have is a build step,
#    an ID space and a compat library we do not have to carry.
"$BT/aapt2" link \
  -I "$PLATFORM" \
  --manifest "$HERE/AndroidManifest.xml" \
  --min-sdk-version 31 --target-sdk-version "$API" \
  -o "$OUT/base.apk"

# 2. Compile against android.jar on the CLASSPATH.
#
#    Not -bootclasspath: JDK 12 removed it for -source >= 9, and JDK 21
#    rejects it outright ("option --boot-class-path not allowed with target
#    11"). android.jar on the classpath supplies android.*; java.* comes from
#    the JDK, which is fine for code this small but does mean the compiler
#    will happily accept a java.* API that Android lacks. d8 --min-api is the
#    net under that.
#
#    NO `|| true` here. The first version of this script piped javac through
#    grep and swallowed the exit status, so a compile error printed one line
#    and the build carried on to zip a dex that did not exist — reporting a
#    zip problem for a Java problem. A build step that cannot fail is not a
#    build step.
find "$HERE/src" -name '*.java' > "$OUT/sources.txt"
javac -source 11 -target 11 -nowarn -Xlint:-options \
  -classpath "$PLATFORM" \
  -d "$OUT/classes" \
  @"$OUT/sources.txt"

# 3. Dex.
"$BT/d8" --min-api 31 --output "$OUT/dex" \
  $(find "$OUT/classes" -name '*.class')
[ -f "$OUT/dex/classes.dex" ] || { echo "d8 produced no classes.dex" >&2; exit 1; }

# 4. Fold the dex in, align, sign. A debug keystore is generated on first run
#    — this app is sideloaded onto one phone by its owner and never
#    distributed, so a throwaway key is the honest amount of ceremony.
(cd "$OUT/dex" && zip -q "$OUT/base.apk" classes.dex)
KS="$HERE/debug.keystore"
if [ ! -f "$KS" ]; then
  keytool -genkeypair -v -keystore "$KS" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=phonebridge, OU=personal, O=none, L=none, ST=none, C=US" >/dev/null 2>&1
fi
"$BT/zipalign" -f -p 4 "$OUT/base.apk" "$OUT/phonebridge-aligned.apk"
"$BT/apksigner" sign \
  --ks "$KS" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/phonebridge.apk" "$OUT/phonebridge-aligned.apk"
rm -f "$OUT/base.apk" "$OUT/phonebridge-aligned.apk"
"$BT/apksigner" verify "$OUT/phonebridge.apk" >/dev/null

echo "built: $OUT/phonebridge.apk"

[ "${1:-}" = "install" ] || exit 0

# ---------------------------------------------------------------- install
# TWO different identities on the phone, and the install crosses between them:
#
#   ssh lands as the TERMUX APP (uid 10xxx). It can write /sdcard. It cannot
#   write /data/local/tmp, which is shell-owned — the first version of this
#   script tried and got "Permission denied".
#   `phone sh` runs at SHELL uid (2000) through Shizuku. It can run pm and
#   cmd. It can read /sdcard.
#
# So the APK travels through /sdcard, the one place both can reach.
PHONE_BIN="${PHONE_BIN:-$HERE/../../lib/phone}"
STAGE="/sdcard/.phone/phonebridge.apk"

echo "→ copying to $DEVICE (as the app)"
ssh "$DEVICE" "mkdir -p /sdcard/.phone && cat > $STAGE" < "$OUT/phonebridge.apk"

# THEN a second hop, shell-side, because /sdcard is a fuse mount and
# system_server is not allowed to read it:
#
#   avc: denied { read } scontext=u:r:system_server:s0 tcontext=u:object_r:fuse:s0
#   Error: Unable to open file: /sdcard/.phone/phonebridge.apk
#   Consider using a file under /data/local/tmp/
#
# The installer runs inside system_server, so handing it a /sdcard path fails
# no matter which UID typed the command. Shell can read /sdcard AND write
# /data/local/tmp, so it is the only identity that can move it across.
echo "→ staging where the installer can read it (as shell)"
"$PHONE_BIN" --device "$DEVICE" sh \
  "cp $STAGE /data/local/tmp/phonebridge.apk && chmod 644 /data/local/tmp/phonebridge.apk" >/dev/null

echo "→ installing (as shell)"
"$PHONE_BIN" --device "$DEVICE" sh \
  "pm install -r -g /data/local/tmp/phonebridge.apk" | tail -2

# Also the documented way past Android 13's Restricted-settings wall, which
# greys out the notification-access toggle for sideloaded apps.
echo "→ granting notification access"
"$PHONE_BIN" --device "$DEVICE" sh "cmd notification allow_listener $LISTENER" | tail -1
"$PHONE_BIN" --device "$DEVICE" sh "cmd appops set $PKG ACCESS_RESTRICTED_SETTINGS allow" >/dev/null 2>&1 || true
"$PHONE_BIN" --device "$DEVICE" sh \
  "rm -f $STAGE /data/local/tmp/phonebridge.apk" >/dev/null 2>&1 || true

# Start the mic service through the no-display activity. Shell can start an
# activity from any state, which is the exemption a background app lacks.
echo "→ starting the voice service (foreground exemption via Boot)"
"$PHONE_BIN" --device "$DEVICE" sh "am start -n $PKG/.Boot" >/dev/null 2>&1 || true

echo "→ enabled listeners now:"
if "$PHONE_BIN" --device "$DEVICE" sh "settings get secure enabled_notification_listeners" \
     | tr ':' '\n' | grep -i phonebridge; then
  :
else
  echo "   phonebridge is NOT in the list — the grant did not take" >&2
  exit 1
fi

echo
echo "forward the bridge to this machine with:"
echo "  ssh -N -L 8127:127.0.0.1:8127 $DEVICE"
