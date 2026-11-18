#!/usr/bin/env bash
# End to end on aibox1: an Android emulator (KVM) with the debug app pairs
# through a person's pairing link, records a speech file in place of the
# microphone, uploads it to the running recorder, and the box transcribes it.
# Then the same with the network cut during the recording, with the app
# killed in the middle of one, and with a pause. From ~/indie-recorder on aibox1:
#
#   android/e2e/emulator-test.sh
#
# Needs the image indie-recorder-android (android/Dockerfile), a debug APK
# (./gradlew assembleDebug) and the recorder stack. Screenshots (light and
# dark) and the log go to data/test/android/. At the end the test meetings,
# the emulator's token and the test person are deleted again.
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
# The same screen in light and in dark (the activity is recreated in between).
shots() {
	shot "$1-light"
	A shell cmd uimode night yes > /dev/null; sleep 3; shot "$1-dark"
	A shell cmd uimode night no > /dev/null; sleep 2
}
box() { docker compose exec -T recorder python -c "$1" < /dev/null; }
cli() { docker compose exec -T recorder python -m recorder.cli "$@" < /dev/null; }
ui_has() { A shell uiautomator dump /sdcard/ui.xml > /dev/null; A shell cat /sdcard/ui.xml | grep -q "text=\"$1\""; }
tap_text() {  # taps the first thing on the screen that shows this text; false when there is none
	A shell uiautomator dump /sdcard/ui.xml > /dev/null
	local xy
	xy=$(A shell cat /sdcard/ui.xml | python3 -c "
import re, sys
m = re.search(r'text=\"' + re.escape(sys.argv[1]) + r'\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', sys.stdin.read())
print((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else print('')" "$1")
	[ -n "$xy" ] && A shell input tap $xy
}

say "test audio: 75 s of German conversation (MLS, stitched)"
docker compose exec -T recorder ffmpeg -nostdin -loglevel error -y -t 75 -i /data/bench/datasets/mls-de/conv1/audio.wav \
	-ac 1 -ar 16000 -c:a pcm_s16le /$OUT/test.wav < /dev/null

say "a test person, a device token for the emulator in that person's name, and its pairing link"
PERSON=$(cli user add --ext-id "emulator-test-$(date +%s)" --name "Emulator Test" | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
TOKEN=$(cli token add --name "Android emulator test" --role device --user "$PERSON" | tail -1 | tr -d '\r')
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

meetings() {  # the meeting ids of everything the emulator recorded
	box "
from recorder.config import Settings; from recorder.db import DB
db = DB(Settings().db_path)
print(' '.join(r['meeting_id'] for r in db.all(\"SELECT meeting_id FROM recordings WHERE token_id = ? AND meeting_id IS NOT NULL\", '$TOKEN_ID')))"
}
cleanup_box() {  # the test meetings, the token and the test person
	local mids
	mids=$(meetings)
	[ -z "$mids" ] || cli delete $mids | grep -v "^[{}]" || true
	cli token revoke "$TOKEN_ID" > /dev/null
	cli user remove "$PERSON" | tr -d '\n' && echo || echo "the test person $PERSON is still there (a meeting of theirs is still being processed)"
}
finish() {
	docker rm -f android-emu > /dev/null 2>&1 || true
	cleanup_box
}

say "emulator (Android 14, x86_64, KVM)"
docker rm -f android-emu > /dev/null 2>&1 || true
docker run -d --name android-emu --device /dev/kvm --network host \
	-v "$PWD/$OUT:/out" -v "$PWD/android:/src:ro" indie-recorder-android bash -c '
	echo no | avdmanager -s create avd -n t -k "'"$SYSIMG"'" -d pixel_6 --force > /dev/null &&
	exec emulator -avd t -no-window -no-audio -gpu swiftshader_indirect -no-snapshot -no-boot-anim -port 5560' > /dev/null
# KEEP=1 leaves the emulator running and the token, the person and the meetings on the box, for a look afterwards.
[ -n "${KEEP:-}" ] || trap finish EXIT
timeout 600 docker exec android-emu bash -c "until adb -s $SERIAL shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do sleep 3; done"
A install -r -g /src/app/build/outputs/apk/debug/app-debug.apk > /dev/null
A shell settings put global window_animation_scale 0
A shell cmd uimode night no > /dev/null
A root > /dev/null; sleep 2

say "the connect screen, before pairing"
A shell am start -W -n $PKG/.MainActivity > /dev/null
sleep 4
shots 0-connect

say "pairing through the deep link"
A shell "am start -W -a android.intent.action.VIEW -d '$LINK' --ez auto true $PKG" > /dev/null
sleep 6
A shell run-as $PKG ls shared_prefs | grep -q box.xml && echo "paired" || { echo "NOT paired"; shot pair-failed; exit 1; }
ui_has "Emulator Test" && echo "the ready screen names the person: Emulator Test" || echo "the ready screen does not name the person (yet)"
shots 1-ready
A push /out/test.wav /data/local/tmp/test.wav > /dev/null
A shell run-as $PKG cp /data/local/tmp/test.wav files/test.wav

record() {  # title
	A shell "am start -n $PKG/.MainActivity --es test_wav /data/data/$PKG/files/test.wav --es test_title '$1' --es test_storage local" > /dev/null
}
hook() {  # test_pause, test_resume or test_stop
	A shell "am start -n $PKG/.MainActivity --ez $1 true" > /dev/null
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
phone_ms() {  # title -> the recorded length the phone counted
	A shell "run-as $PKG sh -c 'cat files/recordings/*/meta.json'" | python3 -c "
import json, sys
for line in sys.stdin.read().replace('}{', '}\n{').splitlines():
    r = json.loads(line)
    if r['title'] == '$1': print(r['durationMs'])"
}

say "1. record 75 s, upload while recording, finalize"
record "Emulator test"
sleep 20; shots 2-recording
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

say "4. pause: 30 s recorded, 20 s paused, 30 s more; the box gets 60 s"
record "Emulator pause test"
sleep 30
hook test_pause
sleep 3
ui_has "Resume" && echo "paused: the screen offers Resume" || echo "paused: NO Resume on the screen"
shots 3-paused
sleep 11
hook test_resume
sleep 30
hook test_stop
wait_final "Emulator pause test" 240
PAUSE_MS=$(phone_ms "Emulator pause test")
echo "phone counted ${PAUSE_MS} ms of audio for the pause test"

say "the meetings on the box"
for _ in $(seq "${WAIT_STEPS:-120}"); do        # 10 s each; the box may be busy with other GPU jobs
	S=$(box "
from recorder.config import Settings; from recorder.store import Store; from recorder.db import DB
s = Settings(); st = Store(s, DB(s.db_path))
print(' '.join(st.meta(m)['state'] for m in '$(meetings)'.split()))")
	echo "$S" | grep -qvE "^((done|failed) ?)+$" || break
	sleep 10
done
for t in "Emulator test" "Emulator offline test" "Emulator kill test" "Emulator pause test"; do
	m=$(recording_of "$t" | awk '{print $3}')
	box "
from recorder.config import Settings; from recorder.store import Store; from recorder.db import DB
s = Settings(); st = Store(s, DB(s.db_path)); m = st.meta('$m'); t = st.transcript('$m') or {}
print('$t:', m['state'], round(m.get('duration_s') or 0, 1), 's,', len(t.get('words', [])), 'words,', m.get('speakers'), 'speakers, owner', m.get('owner_id'))"
done
PAUSE_S=$(box "
from recorder.config import Settings; from recorder.store import Store; from recorder.db import DB
s = Settings(); print(Store(s, DB(s.db_path)).meta('$(recording_of "Emulator pause test" | awk '{print $3}')').get('duration_s') or 0)")
python3 -c "
d = float('$PAUSE_S'); p = int('${PAUSE_MS:-0}') / 1000
print(f'pause test: box {d:.1f} s, phone {p:.1f} s, expected 60 s (80 s with the pause)')
assert abs(d - 60) <= 4 and abs(p - 60) <= 4, 'the pause was recorded or audio is missing'
print('pause test passed')"

say "the summary and the settings on the phone"
A shell am start -n $PKG/.MainActivity > /dev/null
sleep 3
A shell input swipe 540 2000 540 600 300; sleep 1         # the list is under the record block
if tap_text "Emulator test"; then sleep 4; shots 6-meeting; else echo "no row for the meeting on the screen"; fi
A shell input keyevent 4; sleep 2
if tap_text "SETTINGS"; then sleep 2; shots 7-settings; else echo "no settings link on the screen"; fi

say "done; screenshots in $OUT${KEEP:+ (kept: docker rm -f android-emu; then token $TOKEN_ID, person $PERSON and their meetings on the box)}"
