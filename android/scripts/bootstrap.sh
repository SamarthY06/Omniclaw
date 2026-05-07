#!/usr/bin/env bash
# One-shot setup: install Gradle wrapper, fetch nodejs-mobile + npm deps, run a debug build.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

GRADLE_VERSION="${GRADLE_VERSION:-8.10.2}"
GRADLE_DIR="$DIR/.gradle-toolchain"
GRADLE_BIN="$GRADLE_DIR/gradle-$GRADLE_VERSION/bin/gradle"

# 1. Local Gradle (so we don't depend on a system install)
if [[ ! -x "$GRADLE_BIN" ]]; then
  mkdir -p "$GRADLE_DIR"
  ZIP="$GRADLE_DIR/gradle-$GRADLE_VERSION-bin.zip"
  if [[ ! -f "$ZIP" ]]; then
    echo "[bootstrap] downloading gradle $GRADLE_VERSION"
    curl -L -o "$ZIP" "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
  fi
  echo "[bootstrap] extracting gradle"
  unzip -q -o "$ZIP" -d "$GRADLE_DIR"
fi

# 2. Generate the wrapper if it doesn't exist
if [[ ! -f "$DIR/gradlew" ]]; then
  echo "[bootstrap] generating gradle wrapper"
  "$GRADLE_BIN" wrapper --gradle-version "$GRADLE_VERSION"
fi

# 3. Android SDK check / auto-install on macOS via Homebrew cask if missing.
if [[ -z "${ANDROID_HOME:-}" && -z "${ANDROID_SDK_ROOT:-}" ]]; then
  CANDIDATES=(
    "$HOME/Library/Android/sdk"
    "/Library/Android/sdk"
    "/opt/homebrew/share/android-commandlinetools"
    "/usr/local/share/android-commandlinetools"
  )
  for c in "${CANDIDATES[@]}"; do
    if [[ -d "$c/cmdline-tools" || -d "$c/platform-tools" ]]; then
      export ANDROID_HOME="$c"
      echo "[bootstrap] using ANDROID_HOME=$ANDROID_HOME"
      break
    fi
  done
fi

if [[ -z "${ANDROID_HOME:-}" && -z "${ANDROID_SDK_ROOT:-}" ]]; then
  if command -v brew >/dev/null 2>&1; then
    echo "[bootstrap] installing android-commandlinetools via brew (one-time, ~5 min)"
    brew install --cask android-commandlinetools >&2 || true
    export ANDROID_HOME="/opt/homebrew/share/android-commandlinetools"
    [[ -d "$ANDROID_HOME" ]] || export ANDROID_HOME="/usr/local/share/android-commandlinetools"
  fi
fi

if [[ -z "${ANDROID_HOME:-}" && -z "${ANDROID_SDK_ROOT:-}" ]]; then
  cat <<EOF >&2
[bootstrap] Android SDK not found.
            Install Android Studio OR run:
              brew install --cask android-commandlinetools
            Then re-run this script. (See BEN_ANDROID_SETUP.md for details.)
EOF
  exit 1
fi

# Accept SDK licenses and install the build tools we need (idempotent).
SDKMANAGER="${ANDROID_HOME:-$ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager"
if [[ ! -x "$SDKMANAGER" ]]; then
  SDKMANAGER="$(command -v sdkmanager || true)"
fi
if [[ -x "$SDKMANAGER" ]]; then
  yes 2>/dev/null | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
  # platforms/build-tools for compileSdk=35, plus NDK + cmake for the JNI shim
  # that hosts libnode.so.
  "$SDKMANAGER" \
    "platform-tools" \
    "platforms;android-35" \
    "build-tools;35.0.0" \
    "ndk;26.1.10909125" \
    "cmake;3.22.1" >/dev/null
fi

# 4. Fetch nodejs-mobile AAR + embedded node deps
"$DIR/scripts/fetch-nodejs-mobile.sh"

# 5. Pin SDK location for Gradle / Android Studio so they don't fall back to
# auto-detection that varies per machine.
ROOT="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
LP="$DIR/local.properties"
if [[ ! -f "$LP" ]] || ! grep -q "^sdk.dir=" "$LP" 2>/dev/null; then
  echo "sdk.dir=$ROOT" >> "$LP"
fi

# 6. First debug build (export envs so Gradle picks them up too)
export ANDROID_HOME="$ROOT"
export ANDROID_SDK_ROOT="$ROOT"
"$DIR/gradlew" assembleDebug

# 6. Surface the APK at two locations:
#    a) $HOME/Desktop/Ben.apk      - convenient stable handle that any external
#                                    distribution channel (WhatsApp, Drive,
#                                    serve_apk.py) can point at without
#                                    needing to know today's filename.
#    b) android/dist/Ben-<ver>-<sha>[-dirty]-<YYYYMMDD-HHMM>.apk
#                                    a versioned copy that survives subsequent
#                                    builds, alongside the CHANGELOG.md in
#                                    that directory. Tracked in git via
#                                    android/dist/.gitignore (CHANGELOG only;
#                                    APKs themselves are gitignored).
APK_CANDIDATES=(
  "$DIR/app/build/outputs/apk/debug/app-debug.apk"
  "$DIR/app/build/outputs/apk/debug/Ben-debug.apk"
)

# Read versionName from app/build.gradle.kts (single line, simple regex).
VERSION="$(grep -E '^\s*versionName\s*=' "$DIR/app/build.gradle.kts" | head -n1 | sed -E 's/.*"([^"]+)".*/\1/')"
[[ -z "$VERSION" ]] && VERSION="dev"

GIT_SHA="$(git -C "$DIR/.." rev-parse --short HEAD 2>/dev/null || echo nogit)"
if git -C "$DIR/.." diff --quiet 2>/dev/null && git -C "$DIR/.." diff --cached --quiet 2>/dev/null; then
  GIT_SUFFIX="$GIT_SHA"
else
  GIT_SUFFIX="${GIT_SHA}-dirty"
fi
STAMP="$(date +%Y%m%d-%H%M)"

DIST_DIR="$DIR/dist"
mkdir -p "$DIST_DIR"
DIST_NAME="Ben-${VERSION}-${GIT_SUFFIX}-${STAMP}.apk"

for A in "${APK_CANDIDATES[@]}"; do
  if [[ -f "$A" ]]; then
    cp "$A" "$HOME/Desktop/Ben.apk"
    cp "$A" "$DIST_DIR/$DIST_NAME"
    echo "[bootstrap] -> $HOME/Desktop/Ben.apk"
    echo "[bootstrap] -> $DIST_DIR/$DIST_NAME"
    echo "[bootstrap] (remember to add a row to android/dist/CHANGELOG.md)"
    exit 0
  fi
done
echo "[bootstrap] WARNING: expected APK not found in $DIR/app/build/outputs/apk/debug/" >&2
exit 1
