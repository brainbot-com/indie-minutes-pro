#!/usr/bin/env bash
# End to end on aibox1: an Android emulator (KVM) with the debug app pairs
# through the Devices page's link, records a speech file in place of the
# microphone, uploads it to the running recorder, and the box transcribes it.
# Then the same with the network cut during the recording, and with the app
# killed in the middle of one. From ~/indie-recorder on aibox1:
#
#   android/e2e/emulator-test.sh
#
# Needs the image indie-recorder-android (android/Dockerfile), a debug APK
# (./gradlew assembleDebug) and the recorder stack. Screenshots and the log go
# to data/test/android/. The emulator's token is revoked at the end.
set -euo pipefail
cd "$(dirname "$0")/../.."
PKG=com.brainbot.indierecorder
OUT=data/test/android
SERIAL=emulator-5560
# The plain Android 14 image, which draws the screen for the screenshots; the
# lighter test image (SYSIMG="system-images;android-34;aosp_atd;x86_64") runs
# the same steps but its screenshots stay black.
SYSIMG=${SYSIMG:-"system-images;android-34;default;x86_64"}
mkdir -p $OUT
rm -f $OUT/*.png

say() { echo "== $(date -u +%H:%M:%S) $*"; }
A() { docker exec android-emu adb -s $SERIAL "$@"; }
# The emulator's own capture: adb's screencap gives black frames without a window.
shot() { A emu screenrecord screenshot "/out/$1.png" > /dev/null; }
box() { docker compose exec -T recorder python -c "$1" < /dev/null; }

say "test audio: 75 s of German conversation (MLS, stitched)"
docker compose exec -T recorder ffmpeg -nostdin -loglevel error -y -t 75 -i /data/bench/datasets/mls-de/conv1/audio.wav \
	-ac 1 -ar 16000 -c:a pcm_s16le /$OUT/test.wav < /dev/null

say "a device token for the emulator and its pairing link"
TOKEN=$(docker compose exec -T recorder python -m recorder.cli token add --name "Android emulator test" --role device < /dev/null | tail -1 | tr -d '\r')
LINK=$(python3 - "$TOKEN" <<'PY'
import base64, sys
from urllib.parse import quote
pem = open("secrets/tls/ca.crt").read()
der = base64.b64decode("".join(l for l in pem.splitlines() if l and not l.startswith("-----")))
ca = base64.urlsafe_b64encode(der).decode().rstrip("=")
print(f"indierecorder://pair?u={quote('https://10.1.9.7:8443', safe='')}&t={sys.argv[1]}&ca={ca}&n={quote('Android emulator test', safe='')}")
PY
)
TOKEN_ID=$(box "
from recorder.config import Settings; from recorder.db import DB
print(DB(Settings().db_path).one(\"SELECT id FROM tokens WHERE name = 'Android emulator test' AND revoked_at IS NULL ORDER BY created_at DESC\")['id'])")

say "emulator (Android 14, x86_64, KVM)"
docker rm -f android-emu > /dev/null 2>&1 || true
docker run -d --name android-emu --device /dev/kvm --network host \
	-v "$PWD/$OUT:/out" -v "$PWD/android:/src:ro" indie-recorder-android bash -c '
	echo no | avdmanager -s create avd -n t -k "'"$SYSIMG"'" -d pixel_6 --force > /dev/null &&
	exec emulator -avd t -no-window -no-audio -gpu swiftshader_indirect -no-snapshot -no-boot-anim -port 5560' > /dev/null
# KEEP=1 leaves the emulator running and the token valid, for a look afterwards.
[ -n "${KEEP:-}" ] || trap 'docker rm -f android-emu > /dev/null 2>&1 || true' EXIT
timeout 600 docker exec android-emu bash -c "until adb -s $SERIAL shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do sleep 3; done"
A install -r -g /src/app/build/outputs/apk/debug/app-debug.apk > /dev/null
A shell settings put global window_animation_scale 0
A root > /dev/null; sleep 2

say "pairing through the deep link"
A shell "am start -W -a android.intent.action.VIEW -d '$LINK' --ez auto true $PKG" > /dev/null
sleep 6
A shell run-as $PKG ls shared_prefs | grep -q box.xml && echo "paired" || { echo "NOT paired"; shot pair-failed; exit 1; }
shot 1-home
A push /out/test.wav /data/local/tmp/test.wav > /dev/null
A shell run-as $PKG cp /data/local/tmp/test.wav files/test.wav

record() {  # title
	A shell "am start -n $PKG/.MainActivity --es test_wav /data/data/$PKG/files/test.wav --es test_title '$1' --es test_storage local" > /dev/null
}
recording_of() {  # title -> "state chunks meeting_id"
	box "
from recorder.config import Settings; from recorder.db import DB
db = DB(Settings().db_path)
r = db.one(\"SELECT * FROM recordings WHERE token_id = ? AND title = ? ORDER BY created_at DESC\", '$TOKEN_ID', '$1')
n = db.one(\"SELECT COUNT(*) n FROM chunks WHERE recording_id = ?\", r['id'])['n'] if r else 0
print(r['state'] if r else 'none', r['total_chunks'] if r and r['total_chunks'] else n, r['meeting_id'] if r else '')"
}
wait_final() {  # title, seconds
	for _ in $(seq $(( $2 / 5 ))); do
		set -- "$1" "$2" $(recording_of "$1")
		[ "$3" = finalized ] && { echo "finalized: $4 chunks, meeting $5"; return 0; }
		sleep 5
	done
	echo "not finalized: $(recording_of "$1")"; return 1
}

say "1. record 75 s, upload while recording, finalize"
record "Emulator test"
sleep 20; shot 2-recording
wait_final "Emulator test" 240
shot 3-list

say "2. network cut during the recording: chunks wait on the phone"
record "Emulator offline test"
sleep 8
A shell cmd connectivity airplane-mode enable
sleep 80
echo "box while offline: $(recording_of 'Emulator offline test')"
A shell "run-as $PKG sh -c 'cat files/recordings/*/meta.json'" | python3 -c "
import json, sys
for line in sys.stdin.read().replace('}{', '}\n{').splitlines():
    r = json.loads(line)
    if r['title'] == 'Emulator offline test': print('phone while offline:', r['state'], r['uploaded'], 'of', r['chunks'], 'uploaded')"
shot 4-offline
A shell cmd connectivity airplane-mode disable
wait_final "Emulator offline test" 300

say "3. app killed after 40 s: the whole chunks are kept and sent"
record "Emulator kill test"
sleep 42
A shell "kill -9 \$(pidof $PKG)"
sleep 3
A shell am start -n $PKG/.MainActivity > /dev/null
wait_final "Emulator kill test" 240
shot 5-after-kill

say "the meetings on the box"
MID=$(recording_of "Emulator test" | awk '{print $3}')
for _ in $(seq 120); do
	S=$(box "
import json; from recorder.config import Settings; from recorder.store import Store; from recorder.db import DB
s = Settings(); m = Store(s, DB(s.db_path)).meta('$MID'); print(m['state'])")
	[ "$S" = done ] || [ "$S" = failed ] && break
	sleep 10
done
for t in "Emulator test" "Emulator offline test" "Emulator kill test"; do
	m=$(recording_of "$t" | awk '{print $3}')
	box "
import json; from recorder.config import Settings; from recorder.store import Store; from recorder.db import DB
s = Settings(); st = Store(s, DB(s.db_path)); m = st.meta('$m'); t = st.transcript('$m') or {}
print('$t:', m['state'], round(m.get('duration_s') or 0, 1), 's,', len(t.get('words', [])), 'words,', m.get('speakers'), 'speakers')"
done

say "the summary on the phone"
A shell am start -n $PKG/.MainActivity > /dev/null
sleep 3
A shell uiautomator dump /sdcard/ui.xml > /dev/null
XY=$(A shell cat /sdcard/ui.xml | python3 -c "
import re, sys
x = sys.stdin.read()
m = re.search(r'text=\"Emulator test\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', x)
print((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else print('')")
[ -n "$XY" ] && A shell input tap $XY && sleep 4 && shot 6-detail

[ -n "${KEEP:-}" ] || docker compose exec -T recorder python -m recorder.cli token revoke "$TOKEN_ID" < /dev/null > /dev/null
say "done; screenshots in $OUT${KEEP:+ (emulator kept: docker rm -f android-emu; revoke token $TOKEN_ID)}"
