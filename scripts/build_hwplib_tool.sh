#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-${HWPLIB_DIR:-$ROOT_DIR/.cache/hwplib}}"
JAR_NAME="hwplib-1.1.10.jar"
JAR_URL="https://repo1.maven.org/maven2/kr/dogfoot/hwplib/1.1.10/${JAR_NAME}"
EXPECTED_SHA256="fce557a7b4aff0c626c544558e0939459601a008d9378457b6183f7f3ae9d1b9"

mkdir -p "$OUT_DIR/classes"
JAR_PATH="$OUT_DIR/$JAR_NAME"

verify_sha256() {
  local file="$1"
  local actual
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  fi
  [ "$actual" = "$EXPECTED_SHA256" ]
}

if [ ! -f "$JAR_PATH" ] || ! verify_sha256 "$JAR_PATH"; then
  if [ -f "/tmp/bokji-poc-tools/jars/$JAR_NAME" ] && verify_sha256 "/tmp/bokji-poc-tools/jars/$JAR_NAME"; then
    cp "/tmp/bokji-poc-tools/jars/$JAR_NAME" "$JAR_PATH"
  else
    curl -fsSL "$JAR_URL" -o "$JAR_PATH"
    if ! verify_sha256 "$JAR_PATH"; then
      echo "SHA-256 mismatch for $JAR_PATH" >&2
      rm -f "$JAR_PATH"
      exit 1
    fi
  fi
fi

javac -encoding UTF-8 -cp "$JAR_PATH" -d "$OUT_DIR/classes" "$ROOT_DIR/java/HwpLibTool.java"
echo "Built HwpLibTool in $OUT_DIR"
