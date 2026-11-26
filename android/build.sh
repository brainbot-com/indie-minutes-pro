#!/usr/bin/env bash
# Builds the Android app on the box (aibox1) and puts the release APK where its
# phones download it (secrets/tls/public, which Caddy serves on its CA port).
#
#   android/build.sh            (from ~/indie-recorder)
#   PUBLIC=1 android/build.sh   the build for GitHub (android/publish-github.sh)
#
# Typed connect codes are looked up at the box's CONNECT_URL (from .env, or
# CONNECT_BASE). The public build has none: its connect screen asks for the
# box's address, and the box's own APK stays as it is.
#
# Unit tests, a debug build (with the test hooks android/e2e/emulator-test.sh
# uses) and a release build signed with secrets/android-release.jks. The key is
# made on the first run: keep it with the rest of secrets/, because Android
# installs an update only when it is signed with the same key.
set -euo pipefail
cd "$(dirname "$0")/.."
docker image inspect indie-recorder-android > /dev/null 2>&1 || docker build -t indie-recorder-android android
mkdir -p "$HOME/android-build/cache/gradle" "$HOME/android-build/cache/home"
if [ ! -f secrets/android-release.jks ]; then
	(umask 077; openssl rand -hex 24 > secrets/android-release.pass)
	docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/secrets:/secrets" indie-recorder-android bash -c \
		'P=$(cat /secrets/android-release.pass); keytool -genkeypair -keystore /secrets/android-release.jks \
		-storetype PKCS12 -alias indie-recorder -keyalg RSA -keysize 3072 -validity 10000 \
		-dname "CN=Minutes Pro, O=brainbot" -storepass "$P" -keypass "$P"' > /dev/null
	chmod 600 secrets/android-release.jks
fi
if [ -n "${PUBLIC:-}" ]; then
	CONNECT_BASE=""
else
	CONNECT_BASE=${CONNECT_BASE-$(sed -n 's#^CONNECT_URL=\(.*\)/connect/*$#\1#p' .env 2>/dev/null | tail -1)}
fi
docker run --rm --cpus 8 --user "$(id -u):$(id -g)" -e HOME=/cache/home -e GRADLE_USER_HOME=/cache/gradle \
	-e RELEASE_KEYSTORE=/secrets/android-release.jks -e RELEASE_KEYSTORE_PASSWORD_FILE=/secrets/android-release.pass \
	-e CONNECT_BASE="$CONNECT_BASE" \
	-v "$PWD/android:/src" -v "$PWD/secrets:/secrets:ro" -v "$HOME/android-build/cache:/cache" -w /src \
	indie-recorder-android ./gradlew --no-daemon --max-workers 4 -q testDebugUnitTest assembleDebug assembleRelease
if [ -n "${PUBLIC:-}" ]; then
	echo "public build: android/app/build/outputs/apk/release/app-release.apk (android/publish-github.sh)"
	sha256sum android/app/build/outputs/apk/release/app-release.apk
	exit 0
fi
install -m 644 android/app/build/outputs/apk/release/app-release.apk secrets/tls/public/indie-recorder.apk
echo "release: secrets/tls/public/indie-recorder.apk"
sha256sum secrets/tls/public/indie-recorder.apk
