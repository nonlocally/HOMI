#!/usr/bin/env bash
# Build the vdisplay holder dex, and optionally stage it on the phone.
#
#   ./build.sh            build -> build/vdisplay.jar
#   ./build.sh install    also copy it to /data/local/tmp on the device
#
# NOT an APK. app_process wants a jar with a classes.dex in it and nothing
# else — no manifest, no resources, no signing. Two tool invocations.
#
# THE BUILD RUNS WHEREVER THE SDK IS. This laptop has neither a JDK nor the
# Android SDK, and installing them to compile 380 lines of Java is the wrong
# trade when a machine on the tailnet already has both. If ANDROID_HOME
# resolves locally we build here; otherwise the source is shipped to
# $VD_BUILD_HOST, built there, and the jar comes back. Same output either way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/build"
API="${ANDROID_API:-36}"
DEVICE="${PHONE_DEVICE:-aadarshs-pixel-10}"
BUILD_HOST="${VD_BUILD_HOST:-aadarshs-mac-mini-2}"
SDK="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}"

# The device path is /data/local/tmp, not /sdcard. /sdcard is a fuse mount
# and the runtime will not load a dex from it; /data/local/tmp is shell-owned,
# which is exactly the uid app_process runs the holder as.
STAGE_SD="/sdcard/.phone/vdisplay.jar"
STAGE="/data/local/tmp/vdisplay.jar"

have_sdk() {
  [ -d "$SDK/build-tools" ] && command -v javac >/dev/null 2>&1 \
    && javac -version >/dev/null 2>&1
}

build_here() {
  local bt platform
  bt="$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1 || true)"
  platform="$SDK/platforms/android-$API/android.jar"
  [ -n "$bt" ] || { echo "no build-tools under $SDK" >&2; exit 1; }
  [ -f "$platform" ] || { echo "missing $platform" >&2; exit 1; }

  rm -rf "$OUT"; mkdir -p "$OUT/classes" "$OUT/dex"
  find "$HERE/src" -name '*.java' > "$OUT/sources.txt"
  # android.jar on the CLASSPATH, not -bootclasspath: same reason as
  # phonebridge/build.sh — modern javac rejects -bootclasspath with a
  # release >= 9.
  javac -source 11 -target 11 -nowarn -Xlint:-options \
    -classpath "$platform" -d "$OUT/classes" @"$OUT/sources.txt"
  "$bt/d8" --min-api 31 --output "$OUT/dex" \
    $(find "$OUT/classes" -name '*.class')
  [ -f "$OUT/dex/classes.dex" ] || { echo "d8 produced no classes.dex" >&2; exit 1; }
  (cd "$OUT/dex" && zip -q "$OUT/vdisplay.jar" classes.dex)
  echo "built: $OUT/vdisplay.jar"
}

build_remote() {
  echo "→ no local SDK; building on $BUILD_HOST"
  local remote="/tmp/vdisplay-build-$$"
  # -p on tar to keep the exec bit on this script; the remote copy runs it.
  tar -C "$HERE" -cf - src build.sh \
    | ssh -o BatchMode=yes "$BUILD_HOST" "mkdir -p $remote && tar -C $remote -xf -"
  ssh -o BatchMode=yes "$BUILD_HOST" \
    "cd $remote && ANDROID_API=$API bash build.sh" >&2
  mkdir -p "$OUT"
  ssh -o BatchMode=yes "$BUILD_HOST" "cat $remote/build/vdisplay.jar" > "$OUT/vdisplay.jar"
  ssh -o BatchMode=yes "$BUILD_HOST" "rm -rf $remote" || true
  [ -s "$OUT/vdisplay.jar" ] || { echo "remote build produced nothing" >&2; exit 1; }
  echo "built: $OUT/vdisplay.jar (on $BUILD_HOST)"
}

if have_sdk; then build_here; else build_remote; fi

[ "${1:-}" = "install" ] || exit 0

# ------------------------------------------------------------------ install
# The same two-hop the APK takes, for the same reason: ssh lands as the TERMUX
# app, which cannot write /data/local/tmp; `phone sh` is shell, which can.
# /sdcard is the one place both identities can reach.
PHONE_BIN="${PHONE_BIN:-$HERE/../../lib/phone}"

echo "→ copying to $DEVICE (as the app)"
ssh -o BatchMode=yes "$DEVICE" "mkdir -p /sdcard/.phone && cat > $STAGE_SD" < "$OUT/vdisplay.jar"

echo "→ staging where app_process can load it (as shell)"
"$PHONE_BIN" --device "$DEVICE" sh \
  "cp $STAGE_SD $STAGE && chmod 644 $STAGE && rm -f $STAGE_SD" >/dev/null

"$PHONE_BIN" --device "$DEVICE" sh "ls -l $STAGE"
echo "staged: $STAGE"
