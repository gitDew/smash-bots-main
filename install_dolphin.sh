#!/bin/sh
# Install the official recording-capable Slippi beta alongside other Slippi installs.
set -eu
cd "$(dirname "$0")"

base="$PWD/.dolphin/netplay-beta"
if [ -e "$base/Slippi_Dolphin.app" ]; then
    echo "Project-local Slippi beta is already installed."
    exit 0
fi
if [ "$(uname -s)" != Darwin ]; then
    echo "This installer is for macOS." >&2
    exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/smash-slippi.XXXXXX")
mounted=0
cleanup() {
    if [ "$mounted" = 1 ]; then hdiutil detach "$work/mount" -quiet || true; fi
    rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM
url='https://github.com/project-slippi/dolphin/releases/download/v4.0.0-mainline-beta.19/Mainline-Slippi-4.0.0-mainline-beta.19-Mac.dmg'
sha256='6aa4af4c0835cc2a6660cd5d14b5bd1623da9a895518401fe4de9ff0797f5bb1'
curl -fL --silent --show-error "$url" -o "$work/slippi.dmg"
printf '%s  %s\n' "$sha256" "$work/slippi.dmg" | shasum -a 256 -c -
mkdir "$work/mount"
hdiutil attach -readonly -nobrowse -mountpoint "$work/mount" "$work/slippi.dmg" > /dev/null
mounted=1
mkdir -p "$base"
ditto "$work/mount/Slippi_Dolphin.app" "$base/Slippi_Dolphin.app.tmp"
mv "$base/Slippi_Dolphin.app.tmp" "$base/Slippi_Dolphin.app"
echo "Installed project-local Slippi beta in $base"
