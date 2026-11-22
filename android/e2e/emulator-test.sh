#!/usr/bin/env bash
# End to end on aibox1: an Android emulator (KVM) with the debug app connects
# to a test person's account with connect codes (typed, then through the
# connect page's link; a used code is refused), records a speech file in place
# of the microphone, seals its chunks for the box, uploads them to the running
# recorder, and the box opens and transcribes them. Then the same with the
# network cut during the recording, with the app killed in the middle of one,
# with a pause, and on a small phone with large text in English and German.
# Someone may be recording on the box: each scenario waits until the box is
# free. From ~/indie-recorder on aibox1:
#
#   android/e2e/emulator-test.sh
#
# Needs the image indie-recorder-android (android/Dockerfile), a debug APK
# (./gradlew assembleDebug) and the recorder stack. Screenshots (light; DARK=1
# adds dark) and the log go to data/test/android/. At the end the test meetings
# and the test person (with the emulator's tokens) are deleted again.
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
# The screen in light (DARK=1 adds the same in dark; the activity is recreated in between).
shots() {
	shot "$1-light"
	[ -n "${DARK:-}" ] || return 0
	A shell cmd uimode night yes > /dev/null; sleep 3; shot "$1-dark"
	A shell cmd uimode night no > /dev/null; sleep 2
}
box() { docker compose exec -T recorder python -c "$1" < /dev/null; }
cli() { docker compose exec -T recorder python -m recorder.cli "$@" < /dev/null; }
ui_has() { A shell uiautomator dump /sdcard/ui.xml > /dev/null; A shell cat /sdcard/ui.xml | grep -q "text=\"$1\""; }
ui_contains() { A shell uiautomator dump /sdcard/ui.xml > /dev/null; A shell cat /sdcard/ui.xml | grep -qF "$1"; }
tap_text() {  # taps the first thing on the screen that shows this text; false when there is none
	A shell uiautomator dump /sdcard/ui.xml > /dev/null
	local xy
	xy=$(A shell cat /sdcard/ui.xml | python3 -c "
import re, sys
m = re.search(r'text=\"' + re.escape(sys.argv[1]) + r'\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', sys.stdin.read())
print((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else print('')" "$1")
	[ -n "$xy" ] && A shell input tap $xy
}

tap_field() {  # taps the first text field on the screen
	A shell uiautomator dump /sdcard/ui.xml > /dev/null
	local xy
	xy=$(A shell cat /sdcard/ui.xml | python3 -c "
import re, sys
m = re.search(r'class=\"android.widget.EditText\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"', sys.stdin.read())
print((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else print('')")
	[ -n "$xy" ] && A shell input tap $xy
}

say "test audio: 75 s of German conversation (MLS, stitched)"
docker compose exec -T recorder ffmpeg -nostdin -loglevel error -y -t 75 -i /data/bench/datasets/mls-de/conv1/audio.wav \
	-ac 1 -ar 16000 -c:a pcm_s16le /$OUT/test.wav < /dev/null

say "a test person; the emulator connects to their account with connect codes"
PERSON=$(cli user add --ext-id "emulator-test-$(date +%s)" --name "Emulator Test" | python3 -c "import json, sys; print(json.load(sys.stdin)['id'])")
BOX=https://10.1.9.7:8443
CA=$(python3 - <<'PY'
import base64
pem = open("secrets/tls/ca.crt").read()
der = base64.b64decode("".join(l for l in pem.splitlines() if l and not l.startswith("-----")))
print(base64.urlsafe_b64encode(der).decode().rstrip("="))
PY
)
code() {  # a fresh connect code for the test person, without its dash
	cli connect --user "$PERSON" | python3 -c "import json, sys; print(json.load(sys.stdin)['code'].replace('-', ''))"
}
TOKENS="SELECT id FROM tokens WHERE user_id = '$PERSON'"     # the emulator's tokens: one per connect
tokens_of_person() {
	box "
from recorder.config import Settings; from recorder.db import open_index
for r in open_index(Settings()).all(\"SELECT name, revoked_at FROM tokens WHERE user_id = '$PERSON' ORDER BY created_at\"):
    print(r['name'] + (' (revoked)' if r['revoked_at'] else ''))"
}

meetings() {  # the meeting ids of everything the emulator recorded
	box "
from recorder.config import Settings; from recorder.db import open_index
db = open_index(Settings())
print(' '.join(r['meeting_id'] for r in db.all(\"SELECT meeting_id FROM recordings WHERE token_id IN ($TOKENS) AND meeting_id IS NOT NULL\")))"
}
# Someone may be recording on the live box: before each scenario, wait until no recording of anyone
# else is open and alive (made or sent a chunk in the last 15 minutes; an abandoned one does not
# count) and the box has no job queued or running (the GPU does one job at a time).
box_busy() {
	box "
from datetime import datetime, timedelta, timezone
from recorder.config import Settings; from recorder.db import open_index
db = open_index(Settings())
t = (datetime.now(timezone.utc) - timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ')
print(db.one(\"SELECT COUNT(*) n FROM recordings r WHERE state != 'finalized' AND token_id NOT IN ($TOKENS) AND (created_at > ?\"
             \" OR EXISTS (SELECT 1 FROM chunks c WHERE c.recording_id = r.id AND c.received_at > ?))\", t, t)['n'],
      db.one(\"SELECT COUNT(*) n FROM jobs WHERE state IN ('queued', 'running')\")['n'])"
}
wait_box_free() {
	local n=0 b
	while b=$(box_busy); [ "$b" != "0 0" ]; do
		[ $((n % 10)) = 0 ] && say "the box is busy (open recordings of others, jobs): $b; waiting"
		n=$((n + 1)); [ $n -le 360 ] || { echo "the box stayed busy for 3 hours"; exit 1; }
		sleep 30
	done
}
sealed() {  # title: the box has the phone's half of the recording's seal (false when it has none)
	box "
import sys
from recorder.config import Settings; from recorder.db import open_index
r = open_index(Settings()).one(\"SELECT seal_kid, seal_epk FROM recordings WHERE token_id IN ($TOKENS) AND title = ? ORDER BY created_at DESC\", '$1')
print('the box: sealed for its key ' + r['seal_kid'] if r and r['seal_epk'] else 'the box: NOT sealed')
sys.exit(0 if r and r['seal_epk'] else 1)"
}
cleanup_box() {  # the test meetings, then the test person with the emulator's tokens
	local mids
	mids=$(meetings)
	[ -z "$mids" ] || cli delete $mids | grep -v "^[{}]" || true
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
# KEEP=1 leaves the emulator running and the person, their tokens and meetings on the box, for a look afterwards.
[ -n "${KEEP:-}" ] || trap finish EXIT
timeout 600 docker exec android-emu bash -c "until adb -s $SERIAL shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do sleep 3; done"
A install -r -g /src/app/build/outputs/apk/debug/app-debug.apk > /dev/null
A shell settings put global window_animation_scale 0
A shell cmd uimode night no > /dev/null
A root > /dev/null; sleep 2
A shell "settings put global device_name 'Test Pixel 8'"     # the name the phone's settings give it

paired() { A shell "run-as $PKG cat shared_prefs/box.xml" 2> /dev/null | grep -q 'name="url"'; }   # Disconnect leaves an empty file
type_code() {  # into the code field, the way a person types it
	tap_field
	sleep 1
	A shell input text "$1"
	sleep 1
}

say "the connect screen, before connecting"
A shell am start -W -n $PKG/.MainActivity > /dev/null
sleep 4
shots 0-connect

say "connect by typing a code (looked up at the connect address, ai-knowledge, as on a phone)"
CODE1=$(code)
type_code "$CODE1"
shots 0-connect-typed
tap_text "Connect"
sleep 5
if ui_contains "The box is not reachable"; then
	echo "the emulator does not reach ai-knowledge: typed codes are looked up at the box itself (debug override)"
	A shell "am start -n $PKG/.MainActivity --es test_connect_base $BOX --es test_connect_ca $CA" > /dev/null
	sleep 2; tap_text "Connect"; sleep 5
fi
ui_contains "Record into Emulator Test" && echo "the phone asks: Record into Emulator Test's account" ||
	{ echo "NO confirmation"; shot confirm-failed; exit 1; }
shots 0-confirm
tap_text "Connect"
sleep 6
paired && echo "connected by the typed code" || { echo "NOT connected"; shot pair-failed; exit 1; }
echo "the person's tokens on the box: $(tokens_of_person | tr '\n' ',')"
tokens_of_person | grep -qx "Test Pixel 8" && echo "the token has the phone's own name (its device name setting)" ||
	{ echo "the token does NOT have the phone's own name"; exit 1; }

say "a used code is refused"
tap_text "SETTINGS"; sleep 2
tap_text "Disconnect this phone"; sleep 3
paired && { echo "still connected after Disconnect"; exit 1; } || echo "disconnected"
type_code "$CODE1"
tap_text "Connect"
sleep 5
ui_contains "This code is used or expired" && echo "refused: This code is used or expired. Make a new one in indie-recorder, ..." ||
	{ echo "the used code was NOT refused"; shot used-failed; exit 1; }
shots 0-error

say "connect through the connect page's link (code, the box's address and its certificate)"
CODE2=$(code)
LINK="indierecorder://pair?c=$CODE2&u=$(python3 -c "from urllib.parse import quote; print(quote('$BOX', safe=''))")&ca=$CA"
A shell "am start -W -a android.intent.action.VIEW -d '$LINK' $PKG" > /dev/null
sleep 5
ui_contains "Record into Emulator Test" && echo "the link asks the same: Record into Emulator Test's account" ||
	{ echo "NO confirmation from the link"; shot link-failed; exit 1; }
tap_text "Connect"
sleep 6
paired && echo "connected by the link" || { echo "NOT connected"; shot pair-failed; exit 1; }
echo "the person's tokens on the box: $(tokens_of_person | tr '\n' ',')"
sleep 4
ui_has "Emulator Test" && echo "the ready screen names the person (from /api/v1/me): Emulator Test" || echo "the ready screen does not name the person (yet)"
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
from recorder.config import Settings; from recorder.db import open_index
db = open_index(Settings())
r = db.one(\"SELECT * FROM recordings WHERE token_id IN ($TOKENS) AND title = ? ORDER BY created_at DESC\", '$1')
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
phone_dir() {  # title -> the recording's folder on the phone
	local d
	for d in $(A shell "run-as $PKG ls files/recordings" | tr -d '\r'); do
		A shell "run-as $PKG cat files/recordings/$d/meta.json" | grep -qF "\"title\":\"$1\"" && { echo "$d"; return; }
	done
}
phone_files() {  # title -> the audio files of the recording on the phone
	local d
	d=$(phone_dir "$1")
	[ -z "$d" ] || A shell "run-as $PKG ls files/recordings/$d" | tr -d '\r' | grep -v meta.json | tr '\n' ' '
}
none_in_the_clear() {  # chunk heads: false when one is AAC in the clear (ff f1) or there are none
	echo "chunks on the phone start with: ${1:-nothing}"
	case " $1" in *" fff1"*) echo "a chunk is on the phone in the clear"; return 1 ;; esac
	[ -n "$1" ] && echo "no complete chunk on the phone is in the clear" || { echo "no chunks found on the phone"; return 1; }
}
chunk_heads() {  # title -> the first two bytes of each complete chunk on the phone (ff f1: AAC in the clear)
	local d f
	d=$(phone_dir "$1")
	[ -n "$d" ] || return 0
	for f in $(A shell "run-as $PKG ls files/recordings/$d" | tr -d '\r' | grep '\.aac$'); do
		printf '%s ' "$(A exec-out "run-as $PKG cat files/recordings/$d/$f" | head -c 2 | od -An -tx1 | tr -d ' \n')"
	done
}
phone_ms() {  # title -> the recorded length the phone counted
	A shell "run-as $PKG sh -c 'cat files/recordings/*/meta.json'" | python3 -c "
import json, sys
for line in sys.stdin.read().replace('}{', '}\n{').splitlines():
    r = json.loads(line)
    if r['title'] == '$1': print(r['durationMs'])"
}

say "1. record 75 s, sealed on the phone, upload while recording, finalize"
wait_box_free
record "Emulator test"
sleep 20; shots 2-recording
wait_final "Emulator test" 240
sealed "Emulator test"
if tap_text "Recordings"; then sleep 2; shot 3-list; A shell input keyevent 4; sleep 2; else echo "no Recordings button on the screen"; fi

say "2. network cut during the recording: chunks wait on the phone, sealed"
wait_box_free
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
echo "waiting on the phone: $(phone_files 'Emulator offline test')"
none_in_the_clear "$(chunk_heads 'Emulator offline test')"
A shell cmd connectivity airplane-mode disable
wait_final "Emulator offline test" 300

say "3. app killed after 40 s: the whole chunk is kept, the cut one is sealed on the restart, both are sent"
wait_box_free
record "Emulator kill test"
sleep 42
A shell cmd connectivity airplane-mode enable       # offline, so the recovered chunk stays on the phone for a look
A shell "kill -9 \$(pidof $PKG)"
echo "killed; on the phone: $(phone_files 'Emulator kill test')"
sleep 3
A shell am start -n $PKG/.MainActivity > /dev/null
sleep 5
echo "after the restart: $(phone_files 'Emulator kill test')"
none_in_the_clear "$(chunk_heads 'Emulator kill test')"
A shell cmd connectivity airplane-mode disable
wait_final "Emulator kill test" 240
sealed "Emulator kill test"
echo "finalized and sealed: the box opened every chunk, the recovered one too"
echo "the phone kept $(phone_ms 'Emulator kill test') ms of audio (counted when the chunks were sealed)"
shot 5-after-kill

say "4. pause: 30 s recorded, 20 s paused, 30 s more; the box gets 60 s"
wait_box_free
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
sealed "Emulator pause test"

say "5. a small phone with large text: 360 x 640 dp, font scale 1.3, English and German"
A shell wm size 1080x1920; A shell wm density 480; A shell settings put system font_scale 1.3
fit_check() {  # name, texts: nothing on the page scrolls; the texts show, the last one (the Recordings button) above the navigation bar
	A shell uiautomator dump /sdcard/ui.xml > /dev/null
	A shell cat /sdcard/ui.xml | python3 -c "
import re, sys
x = sys.stdin.read()
areas = re.findall(r'scrollable=\"true\"', x)
texts = set(re.findall(r'(?:text|content-desc)=\"([^\"]+)\"', x))
want = [w for w in sys.argv[2:] if w not in texts]
b = re.search(r'text=\"' + re.escape(sys.argv[-1]) + r'\"[^>]*bounds=\"\[\d+,\d+\]\[\d+,(\d+)\]\"', x)
on = b is not None and int(b[1]) <= 1920 - 144
print(sys.argv[1] + ':', len(areas), 'scrollable areas,', 'the Recordings button on the screen' if on else 'the Recordings button NOT on the screen',
      '| labels shown' if not want else '| MISSING ' + ', '.join(want))
sys.exit(0 if not areas and on and not want else 1)" "$1" "${@:2}" || { echo "fit check failed: $1"; FIT_FAILED=1; }
}
FIT_FAILED=0
for lang in en de; do
	if [ $lang = de ]; then
		A shell cmd locale set-app-locales $PKG --locales de-DE
		A shell am force-stop $PKG; A shell am start -W -n $PKG/.MainActivity > /dev/null; sleep 3
		L_REC=Aufnehmen; L_PAUSE=Pause; L_RESUME=Weiter; L_STOP=Stopp; L_LIST=Aufnahmen
	else
		A shell am start -W -n $PKG/.MainActivity > /dev/null; sleep 3
		L_REC=Record; L_PAUSE=Pause; L_RESUME=Resume; L_STOP=Stop; L_LIST=Recordings
	fi
	shot fit-ready-$lang; fit_check "ready ($lang)" "$L_REC" "$L_LIST"
	wait_box_free
	record "Fit test $lang"
	sleep 8; shot fit-recording-$lang; fit_check "recording ($lang)" "$L_PAUSE" "$L_STOP" "$L_LIST"
	hook test_pause; sleep 3; shot fit-paused-$lang; fit_check "paused ($lang)" "$L_RESUME" "$L_STOP" "$L_LIST"
	hook test_resume; sleep 4; hook test_stop
	wait_final "Fit test $lang" 240 || true
done
A shell cmd locale set-app-locales $PKG --locales en-US
A shell wm size reset; A shell wm density reset; A shell settings put system font_scale 1.0
[ "$FIT_FAILED" = 0 ] && echo "fit: no page scroll, every label and the Recordings button shown on Ready, Recording and Paused, in English and German" ||
	echo "fit: FAILED (see above)"

say "the meetings on the box"
for _ in $(seq "${WAIT_STEPS:-120}"); do        # 10 s each; the box may be busy with other GPU jobs
	S=$(box "
from recorder.config import Settings; from recorder.store import Store; from recorder.db import open_index
s = Settings(); st = Store(s, open_index(s))
print(' '.join(st.meta(m)['state'] for m in '$(meetings)'.split()))")
	echo "$S" | grep -qvE "^((done|failed) ?)+$" || break
	sleep 10
done
# each one sealed, opened, transcribed, and as long as its audio: 75 s, the kill test what the phone kept
KILL_S=$(python3 -c "print(int('$(phone_ms 'Emulator kill test')') / 1000)")
MEETINGS_OK=1
for t in "Emulator test=75" "Emulator offline test=75" "Emulator kill test=$KILL_S" "Emulator pause test=60"; do
	m=$(recording_of "${t%=*}" | awk '{print $3}')
	box "
import sys
from recorder.config import Settings; from recorder.store import Store; from recorder.db import open_index
s = Settings(); db = open_index(s); st = Store(s, db); m = st.meta('$m'); t = st.transcript('$m') or {}
sealed = bool(db.one(\"SELECT seal_epk FROM recordings WHERE meeting_id = ?\", '$m')['seal_epk'])
d, n = m.get('duration_s') or 0, len(t.get('words', []))
ok = sealed and m['state'] == 'done' and n > 0 and abs(d - ${t#*=}) <= 3
print('${t%=*}:', m['state'], round(d, 1), 's (the audio ${t#*=} s),', n, 'words,', m.get('speakers'), 'speakers,',
      'sealed' if sealed else 'NOT sealed', ', owner', m.get('owner_id'), '' if ok else ' <- FAILED')
sys.exit(0 if ok else 1)" || MEETINGS_OK=0
done
[ $MEETINGS_OK = 1 ] || { echo "a meeting is not sealed, not transcribed or not as long as its audio"; exit 1; }
PAUSE_S=$(box "
from recorder.config import Settings; from recorder.store import Store; from recorder.db import open_index
s = Settings(); print(Store(s, open_index(s)).meta('$(recording_of "Emulator pause test" | awk '{print $3}')').get('duration_s') or 0)")
python3 -c "
d = float('$PAUSE_S'); p = int('${PAUSE_MS:-0}') / 1000
print(f'pause test: box {d:.1f} s, phone {p:.1f} s, expected 60 s (80 s with the pause)')
assert abs(d - 60) <= 4 and abs(p - 60) <= 4, 'the pause was recorded or audio is missing'
print('pause test passed')"

say "on the phone the chunk keys and the audio are gone once the box has a recording"
A shell "run-as $PKG sh -c 'cat files/recordings/*/meta.json'" | python3 -c "
import json, sys
bad = 0
for line in sys.stdin.read().replace('}{', '}\n{').splitlines():
    r = json.loads(line)
    print(r['title'] + ':', r['state'] + ',', 'sealed' if r.get('sealEpk') else 'NOT sealed', 'for', r.get('sealKid'),
          '| chunk key', 'STILL THERE' if r.get('sealKey') else 'gone')
    bad += r['state'] != 'finalized' or not r.get('sealEpk') or bool(r.get('sealKey'))
sys.exit(1 if bad else 0)"
LEFT=$(A shell "run-as $PKG sh -c 'ls files/recordings/*/'" | tr -d '\r' | grep -c '\.aac' || true)
[ "$LEFT" = 0 ] && echo "no audio left on the phone" || { echo "$LEFT audio files left on the phone"; exit 1; }

say "the recordings, a summary and the settings on the phone"
A shell am start -n $PKG/.MainActivity > /dev/null
sleep 3
if tap_text "Recordings"; then sleep 3; shots 6-recordings; else echo "no Recordings button on the screen"; fi
if tap_text "Emulator test"; then sleep 4; shots 6-meeting; else echo "no row for the meeting on the screen"; fi
A shell input keyevent 4; sleep 2                         # back to the recordings
A shell input keyevent 4; sleep 2                         # back to the main screen
if tap_text "SETTINGS"; then sleep 2; shots 7-settings; else echo "no settings link on the screen"; fi

say "done; screenshots in $OUT${KEEP:+ (kept: docker rm -f android-emu; then person $PERSON with their tokens and meetings on the box)}"
