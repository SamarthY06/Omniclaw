#!/usr/bin/env bash
# Lay out nodejs-mobile pre-builts into app/libnode/ so CMake + Gradle can pick
# them up. Idempotent: skips download if app/libnode/bin/arm64-v8a/libnode.so is
# already present.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIBNODE="$DIR/app/libnode"
BIN_OUT="$LIBNODE/bin"
INC_OUT="$LIBNODE/include"
SENTINEL="$BIN_OUT/arm64-v8a/libnode.so"
mkdir -p "$BIN_OUT" "$INC_OUT"

VERSION="${NODEJS_MOBILE_VERSION:-v18.20.4}"

if [[ -f "$SENTINEL" ]]; then
  echo "[fetch-nodejs-mobile] $SENTINEL already present (version=$VERSION)"
else
  URL="https://github.com/nodejs-mobile/nodejs-mobile/releases/download/${VERSION}/nodejs-mobile-${VERSION}-android.zip"
  TMP="$(mktemp -d)"
  trap "rm -rf '$TMP'" EXIT

  echo "[fetch-nodejs-mobile] downloading $URL"
  curl -fL -o "$TMP/nodejs-mobile.zip" "$URL"
  echo "[fetch-nodejs-mobile] extracting"
  unzip -q "$TMP/nodejs-mobile.zip" -d "$TMP/extracted"

  # Locate the bin/ tree (one level deep).
  BIN_ROOT="$(find "$TMP/extracted" -type d -name 'bin' -mindepth 1 -maxdepth 3 | head -n1)"
  if [[ -z "$BIN_ROOT" ]]; then
    echo "[fetch-nodejs-mobile] could not find bin/ in archive" >&2
    exit 1
  fi
  for abi in arm64-v8a armeabi-v7a x86_64; do
    if [[ -f "$BIN_ROOT/$abi/libnode.so" ]]; then
      mkdir -p "$BIN_OUT/$abi"
      cp "$BIN_ROOT/$abi/libnode.so" "$BIN_OUT/$abi/libnode.so"
      echo "[fetch-nodejs-mobile]   -> $BIN_OUT/$abi/libnode.so"
    fi
  done

  # Locate the include/ tree.
  INC_ROOT="$(find "$TMP/extracted" -type d -name 'include' -mindepth 1 -maxdepth 3 | head -n1)"
  if [[ -z "$INC_ROOT" ]]; then
    echo "[fetch-nodejs-mobile] could not find include/ in archive" >&2
    exit 1
  fi
  rm -rf "$INC_OUT"
  cp -R "$INC_ROOT" "$INC_OUT"
  echo "[fetch-nodejs-mobile]   -> $INC_OUT/"
fi

# Pre-install npm deps inside assets/node so the embedded runtime can require() them
# without network at runtime (nodejs-mobile has no npm at runtime).
NODE_ASSETS="$DIR/app/src/main/assets/node"
if [[ -f "$NODE_ASSETS/package.json" && ! -d "$NODE_ASSETS/node_modules" ]]; then
  echo "[fetch-nodejs-mobile] installing embedded node deps"
  (cd "$NODE_ASSETS" && npm install --omit=dev --no-audit --no-fund)
fi

echo "[fetch-nodejs-mobile] done"
