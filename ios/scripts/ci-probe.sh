#!/usr/bin/env bash
set -euo pipefail

: "${XCODE_APP:?XCODE_APP must name the selected Xcode application}"
: "${SIM_DEVICE:?SIM_DEVICE must name the target simulator}"
: "${SIM_OS:?SIM_OS must name the target iOS runtime}"

echo "== Xcode =="
xcodebuild -version
echo "== Swift =="
swift --version
echo "== iOS Simulator SDK =="
xcrun --show-sdk-version --sdk iphonesimulator
echo "== Simulator runtimes =="
xcrun simctl list runtimes
echo "== Available simulators =="
xcrun simctl list devices available

if ! xcrun simctl list devices available --json |
  python3 -c '
import json
import sys

device_name, os_version = sys.argv[1:]
runtime_suffix = "iOS-" + os_version.replace(".", "-")
devices = json.load(sys.stdin).get("devices", {})
found = any(
    runtime.endswith(runtime_suffix)
    and any(
        device.get("name") == device_name
        and device.get("isAvailable", True)
        for device in runtime_devices
    )
    for runtime, runtime_devices in devices.items()
)
sys.exit(0 if found else 1)
' "$SIM_DEVICE" "$SIM_OS"; then
  echo "Required simulator not available: $SIM_DEVICE / iOS $SIM_OS" >&2
  xcrun simctl list devices available >&2
  exit 1
fi
