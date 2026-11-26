#!/usr/bin/env bash
# Puts a release APK on GitHub, where boxes and phones download the Android app:
#   https://github.com/brainbot-com/indie-minutes-pro/releases/download/android/minutes-pro.apk
#
#   android/publish-github.sh <app-release.apk>
#
# One rolling release, tagged "android", whose file each version replaces: a link
# that stays the same, whatever else the repository releases (the Indie Box app's
# own versions are tags too). Build and sign the APK on aibox1 (android/build.sh,
# the key in secrets/android-release.jks); this runs wherever `gh` is signed in.
set -euo pipefail
APK=${1:?usage: android/publish-github.sh <app-release.apk>}
REPO=brainbot-com/indie-minutes-pro
command -v aapt >/dev/null 2>&1 && aapt dump badging "$APK" | grep -E "^package:|^application-label:" || true
SHA=$(sha256sum "$APK" | cut -d' ' -f1)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp "$APK" "$tmp/minutes-pro.apk"
NOTES="The Android app of Minutes Pro, built and signed by brainbot. Install it from the phone's browser (allow installs from the browser once); updates install over it.

SHA-256 of minutes-pro.apk: $SHA"
if gh release view android -R "$REPO" >/dev/null 2>&1; then
  gh release upload android "$tmp/minutes-pro.apk" --clobber -R "$REPO"
  gh release edit android -R "$REPO" --notes "$NOTES" ${TITLE:+--title "$TITLE"}
else
  gh release create android "$tmp/minutes-pro.apk" -R "$REPO" --title "${TITLE:-Minutes Pro for Android}" --notes "$NOTES" --latest=false
fi
echo "published: https://github.com/$REPO/releases/download/android/minutes-pro.apk ($SHA)"
