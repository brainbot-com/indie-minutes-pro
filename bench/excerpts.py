"""Our own recordings in the benchmark (sets "own" and "own-ref", stay on the box):

    python -m bench.excerpts add /data/test/retreat-excerpt.m4a --name retreat --language en
    python -m bench.excerpts cut /data/drive-inbox/call.mp4 --start 12:30 --name call1 --language de
    # edit data/bench/datasets/own-ref/call1/ref.md: fix words and speakers
    python -m bench.excerpts import call1

add puts a whole recording into "own" without a reference: the scorer reports
speed, memory, speakers and how far the engines agree with each other. cut
makes a 5-minute excerpt in "own-ref" with a draft transcript (engine A) for a
person to correct; import turns the corrected file into the references WER,
cpWER and DER need.

ref.md has one line per turn, "[mm:ss] Speaker N: text". Keep the timestamps
roughly right: they become the speaker segments for DER (approximate)."""
import argparse
import json
import re
from pathlib import Path

from recorder import audio
from recorder.config import Settings
from recorder.engines import Engines
from recorder.render import words_to_segments

ROOT = Path("/data/bench/datasets/own")
REF_ROOT = Path("/data/bench/datasets/own-ref")
LINE = re.compile(r"^\[(\d+):(\d{2})\]\s*([^:]+):\s*(.*)$")


def seconds(text):
    parts = [int(p) for p in text.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def add(src, name, language):
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    audio.to_wav16k(src, d / "audio.wav")
    (d / "meta.json").write_text(json.dumps({"set": "own", "item": name, "language": language,
                                             "source": str(src), "reference": None}, indent=1))
    print(f"{name}: {audio.wav_duration(d / 'audio.wav') / 60:.1f} min, no reference (agreement only)")


def cut(src, start, minutes, name, engine, language="de"):
    d = REF_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    full = d / "full.wav"
    audio.to_wav16k(src, full)
    audio.slice_wav(full, d / "audio.wav", start, start + minutes * 60)
    full.unlink()
    t = Engines(Settings()).get(engine).transcribe(d / "audio.wav", d, language=language, log=print)
    lines = [f"# {name}: {Path(src).name} from {start // 60}:{start % 60:02d}, {minutes} min, draft by {engine}",
             "# One line per turn: [mm:ss] Speaker N: text. Fix the words and the speakers.", ""]
    for s in words_to_segments(t["words"], max_gap=1.0):
        lines.append(f"[{int(s['s']) // 60:02d}:{int(s['s']) % 60:02d}] {s['spk']}: {s['text']}")
    (d / "ref.md").write_text("\n".join(lines) + "\n")
    (d / "meta.json").write_text(json.dumps({"set": "own-ref", "item": name, "language": language,
                                             "source": str(src), "start_s": start, "reference": "draft"}, indent=1))
    print(f"wrote {d / 'ref.md'}: correct it, then run: python -m bench.excerpts import {name}")


def import_ref(name):
    d = REF_ROOT / name
    duration = audio.wav_duration(d / "audio.wav")
    turns = []
    for line in (d / "ref.md").read_text().splitlines():
        m = LINE.match(line.strip())
        if m:
            turns.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3).strip(), m.group(4).strip()))
    segs = []
    for i, (start, spk, text) in enumerate(turns):
        end = turns[i + 1][0] if i + 1 < len(turns) else duration
        segs.append({"session_id": name, "speaker": spk, "start_time": float(start),
                     "end_time": float(max(end, start + 0.5)), "words": text})
    (d / "ref.seglst.json").write_text(json.dumps(segs, ensure_ascii=False, indent=0))
    (d / "ref.rttm").write_text("".join(
        f"SPEAKER {name} 1 {s['start_time']:.3f} {s['end_time'] - s['start_time']:.3f} <NA> <NA> "
        f"{s['speaker'].replace(' ', '_')} <NA> <NA>\n" for s in segs))
    meta = json.loads((d / "meta.json").read_text())
    meta.update(reference="hand-corrected", speakers=len({s["speaker"] for s in segs}))
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{name}: {len(segs)} turns, {meta['speakers']} speakers; the benchmark now scores it")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["add", "cut", "import"])
    ap.add_argument("source", help="audio or video file (add, cut) or excerpt name (import)")
    ap.add_argument("--language", default="de")
    ap.add_argument("--start", default="0:00", help="mm:ss or hh:mm:ss")
    ap.add_argument("--minutes", type=int, default=5)
    ap.add_argument("--name")
    ap.add_argument("--engine", default="A-iq4")
    a = ap.parse_args(argv)
    if a.what == "add":
        add(a.source, a.name or Path(a.source).stem[:30], a.language)
    elif a.what == "cut":
        cut(a.source, seconds(a.start), a.minutes, a.name or Path(a.source).stem[:30], a.engine, a.language)
    else:
        import_ref(a.source)


if __name__ == "__main__":
    main()
