"""Our own call recordings as test material, handled on the box only.

    python -m bench.drive_inbox scan
    python -m bench.drive_inbox excerpts [--max 3] [--minutes 5]

scan transcribes every audio or video file in data/drive-inbox/ with engine A
and prints what each file is without its content: duration, language,
speakers, words per minute (data/bench/drive/scan.json). excerpts picks, in
each German file, the window with the most speakers and speaker changes and
cuts it with a draft reference to correct (bench/excerpts.py)."""
import argparse
import json
from pathlib import Path

from recorder import audio
from recorder.config import Settings
from recorder.engines import Engines

from . import excerpts

INBOX = Path("/data/drive-inbox")
OUT = Path("/data/bench/drive")
MEDIA = {".mp4", ".m4a", ".mov", ".webm", ".wav", ".mp3", ".ogg", ".flac", ".mkv"}


def scan(engine="A-iq4"):
    eng = Engines(Settings()).get(engine)
    rows = []
    for f in sorted(p for p in INBOX.iterdir() if p.suffix.lower() in MEDIA):
        d = OUT / f.stem[:60]
        d.mkdir(parents=True, exist_ok=True)
        wav = d / "audio.wav"
        if not wav.exists():
            audio.to_wav16k(f, wav)
        tf = d / "transcript.json"
        if not tf.exists():
            t = eng.transcribe(wav, d, language=None, log=lambda m: print("   ", m, flush=True))
            tf.write_text(json.dumps(t, ensure_ascii=False))
        t = json.loads(tf.read_text())
        minutes = t["duration_s"] / 60
        rows.append({"file": f.name, "dir": str(d), "minutes": round(minutes, 1), "language": t["language"],
                     "speakers": len(t["speakers"]), "words_per_min": round(len(t["words"]) / max(minutes, 0.1)),
                     "engine_rtf": t.get("timings", {}).get("rtf")})
        print(json.dumps(rows[-1]), flush=True)
    (OUT / "scan.json").write_text(json.dumps(rows, indent=1))
    return rows


def best_window(t, seconds):
    """Start of the window with the most distinct speakers, then the most
    speaker changes, then the most words."""
    words = t["words"]
    best, best_key = 0.0, None
    start = 0.0
    while start + seconds <= t["duration_s"] or start == 0.0:
        ws = [w for w in words if start <= w["s"] < start + seconds]
        changes = sum(1 for a, b in zip(ws, ws[1:]) if a["spk"] != b["spk"])
        key = (len({w["spk"] for w in ws}), changes, len(ws))
        if best_key is None or key > best_key:
            best, best_key = start, key
        start += 30.0
        if start + seconds > t["duration_s"]:
            break
    return best, best_key


def make_excerpts(limit, minutes):
    rows = json.loads((OUT / "scan.json").read_text())
    german = sorted((r for r in rows if r["language"] == "de"), key=lambda r: (-r["speakers"], -r["minutes"]))
    made = []
    for r in german[:limit]:
        t = json.loads((Path(r["dir"]) / "transcript.json").read_text())
        start, key = best_window(t, minutes * 60)
        name = Path(r["dir"]).name[:24].replace(" ", "_") + f"-{int(start) // 60:02d}m"
        excerpts.cut(Path(r["dir"]) / "audio.wav", int(start), minutes, name, "A-iq4")
        made.append({"file": r["file"], "excerpt": name, "start_s": int(start),
                     "speakers_in_window": key[0], "speaker_changes": key[1]})
        print(json.dumps(made[-1]), flush=True)
    return made


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["scan", "excerpts"])
    ap.add_argument("--max", type=int, default=3)
    ap.add_argument("--minutes", type=int, default=5)
    a = ap.parse_args(argv)
    if a.what == "scan":
        scan()
    else:
        make_excerpts(a.max, a.minutes)


if __name__ == "__main__":
    main()
