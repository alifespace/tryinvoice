#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd -- "$SCRIPT_DIR/../.." && pwd)}"
cd "$ROOT"

if [[ ! -f ios/project.yml ]]; then
  echo "skip: ios/project.yml not found"
  exit 0
fi

: "${XCODEGEN_VERSION:?XCODEGEN_VERSION must pin XcodeGen}"
: "${SIM_DEVICE:?SIM_DEVICE must name the target simulator}"
: "${SIM_OS:?SIM_OS must name the target iOS runtime}"
RUNNER_TEMP="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
OUTPUT_DIR="ios/build/ci"
LOG_DIR="$OUTPUT_DIR/logs"
TOOL_DIR="$RUNNER_TEMP/xcodegen-$XCODEGEN_VERSION"
ARCHIVE="$RUNNER_TEMP/xcodegen-$XCODEGEN_VERSION.zip"
PROJECT="ios/TryInvoice.xcodeproj"

case "$XCODEGEN_VERSION" in
  2.42.0)
    EXPECTED_SHA256="a760b4bf97460341316a2c00d8c87b2946c6c2729eb57e9a72abb3ef7bc0ef82"
    ;;
  *)
    echo "Unsupported XcodeGen version: $XCODEGEN_VERSION" >&2
    exit 1
    ;;
esac

mkdir -p "$TOOL_DIR" "$LOG_DIR"
curl -fsSL \
  "https://github.com/yonaskolb/XcodeGen/releases/download/$XCODEGEN_VERSION/xcodegen.zip" \
  -o "$ARCHIVE"
ACTUAL_SHA256="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "XcodeGen archive SHA-256 mismatch" >&2
  exit 1
fi
unzip -q -o "$ARCHIVE" -d "$TOOL_DIR"
chmod +x "$TOOL_DIR/xcodegen/bin/xcodegen"
export PATH="$TOOL_DIR/xcodegen/bin:$PATH"
XCODEGEN_OUTPUT="$(xcodegen --version)"
if [[ "$XCODEGEN_OUTPUT" != *"$XCODEGEN_VERSION"* ]]; then
  echo "Unexpected XcodeGen version: $XCODEGEN_OUTPUT" >&2
  exit 1
fi

xcodegen generate --spec ios/project.yml
if ! xcodebuild -list -project "$PROJECT" -json | tee "$LOG_DIR/list.json"; then
  echo "xcodebuild -list failed" >&2
  exit 1
fi
python3 - "$LOG_DIR/list.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
schemes = data.get("project", {}).get("schemes", [])
if "TryInvoice" not in schemes:
    raise SystemExit("Shared scheme TryInvoice not found")
PY

mkdir -p "$RUNNER_TEMP/DerivedData"
rm -rf "$OUTPUT_DIR/build.xcresult" "$OUTPUT_DIR/test.xcresult"
xcodebuild build-for-testing \
  -project "$PROJECT" \
  -scheme TryInvoice \
  -destination "platform=iOS Simulator,name=$SIM_DEVICE,OS=$SIM_OS" \
  -derivedDataPath "$RUNNER_TEMP/DerivedData" \
  -resultBundlePath "$OUTPUT_DIR/build.xcresult" \
  CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=YES CODE_SIGN_IDENTITY=- 2>&1 | tee "$LOG_DIR/build.log"

if xcodebuild test-without-building \
  -project "$PROJECT" \
  -scheme TryInvoice \
  -destination "platform=iOS Simulator,name=$SIM_DEVICE,OS=$SIM_OS" \
  -derivedDataPath "$RUNNER_TEMP/DerivedData" \
  -resultBundlePath "$OUTPUT_DIR/test.xcresult" \
  CODE_SIGNING_ALLOWED=YES CODE_SIGNING_REQUIRED=YES CODE_SIGN_IDENTITY=- 2>&1 | tee "$LOG_DIR/test.log"; then
  exit 0
else
  TEST_STATUS=$?
  mkdir -p "$OUTPUT_DIR/screenshots"
  if ! xcrun xcresulttool export attachments \
    --path "$OUTPUT_DIR/test.xcresult" \
    --output-path "$OUTPUT_DIR/screenshots" \
    --only-failures; then
    echo "Warning: failed to export test attachments" >&2
  fi
  exit "$TEST_STATUS"
fi
