"""Scores the runs: data/bench/results.json and data/bench/results.md.

WER    speaker-agnostic: reference utterances in time order against the
       hypothesis words in time order (jiwer), after normalization.
cpWER  meeteval's concatenated minimum-permutation WER (speaker-attributed).
DER    pyannote.metrics, collar 0.25 s (pyannote convention: 0.125 s either
       side of each reference boundary), overlapping speech scored.
Normalization: Whisper's English normalizer for English (also drops fillers
such as "uh", "um", "hmm"), Whisper's basic normalizer for German.

    python -m bench.score"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path("/data/bench")


def normalizer(lang):
    from whisper_normalizer.basic import BasicTextNormalizer
    from whisper_normalizer.english import EnglishTextNormalizer
    norm = EnglishTextNormalizer() if lang == "en" else BasicTextNormalizer()
    return lambda text: re.sub(r"\s+", " ", norm(text or "")).strip()


def hyp_segments(t, gap=0.5):
    """Each speaker's words merged across their own pauses up to `gap` seconds
    (other speakers' words in between do not split a region)."""
    segs, open_seg = [], {}
    for w in sorted(t["words"], key=lambda w: w["s"]):
        cur = open_seg.get(w["spk"])
        if cur and w["s"] - cur["end_time"] <= gap:
            cur["words"] += " " + w["w"]
            cur["end_time"] = max(cur["end_time"], w["e"])
        else:
            cur = open_seg[w["spk"]] = {"speaker": w["spk"], "start_time": w["s"], "end_time": w["e"], "words": w["w"]}
            segs.append(cur)
    return segs


def annotation(segs, label="speaker"):
    from pyannote.core import Annotation, Segment
    a = Annotation()
    for i, s in enumerate(segs):
        if s["end_time"] > s["start_time"]:
            a[Segment(s["start_time"], s["end_time"]), i] = s[label]
    return a


def read_rttm(path):
    out = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if p and p[0] == "SPEAKER":
            out.append({"speaker": p[7], "start_time": float(p[3]), "end_time": float(p[3]) + float(p[4])})
    return out


def score_item(ref_dir, t):
    import jiwer
    import meeteval
    from pyannote.core import Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate

    meta = json.loads((ref_dir / "meta.json").read_text())
    norm = normalizer(meta["language"])
    ref = json.loads((ref_dir / "ref.seglst.json").read_text())
    hyp = hyp_segments(t)
    ref_text = norm(" ".join(s["words"] for s in sorted(ref, key=lambda s: s["start_time"])))
    hyp_text = norm(" ".join(w["w"] for w in sorted(t["words"], key=lambda w: w["s"])))
    out = jiwer.process_words(ref_text, hyp_text)
    res = {"ref_words": len(ref_text.split()), "wer": out.wer, "sub": out.substitutions, "del": out.deletions,
           "ins": out.insertions}
    sid = ref[0]["session_id"]
    r = {s: " ".join(norm(x["words"]) for x in ref if x["speaker"] == s) for s in {x["speaker"] for x in ref}}
    h = {s: " ".join(norm(x["words"]) for x in hyp if x["speaker"] == s) for s in {x["speaker"] for x in hyp}}
    cp = meeteval.wer.wer.cp.cp_word_error_rate_multifile({sid: r}, {sid: h})[sid]
    res.update(cpwer=cp.error_rate, cp_errors=cp.errors, cp_length=cp.length,
               ref_speakers=len(r), hyp_speakers=len(h), missed_speakers=cp.missed_speaker,
               extra_speakers=cp.falarm_speaker)
    der = DiarizationErrorRate(collar=0.25, skip_overlap=False)
    uem = Timeline([Segment(0, t["duration_s"])])
    comp = der(annotation(read_rttm(ref_dir / "ref.rttm")), annotation(hyp), uem=uem, detailed=True)
    res.update(der=comp["diarization error rate"],
               der_miss=comp["missed detection"] / comp["total"] if comp["total"] else None,
               der_fa=comp["false alarm"] / comp["total"] if comp["total"] else None,
               der_conf=comp["confusion"] / comp["total"] if comp["total"] else None)
    return res


def main():
    results = []
    for tf in sorted((ROOT / "runs").glob("*/*/*/transcript.json")):
        engine, set_name, item = tf.parts[-4:-1]
        if engine == "cpu-proof":
            continue
        ref_dir = ROOT / "datasets" / set_name / item
        t = json.loads(tf.read_text())
        row = {"engine": engine, "set": set_name, "item": item, "duration_s": t["duration_s"],
               "timings": t.get("timings", {}), "resources": t.get("resources", {}),
               "speaker_scope": t.get("speaker_scope")}
        try:
            row.update(score_item(ref_dir, t))
        except Exception as e:
            row["score_error"] = f"{type(e).__name__}: {e}"
        results.append(row)
        print(f"{engine:6} {set_name:12} {item:9} WER {row.get('wer', float('nan')):.3f} "
              f"cpWER {row.get('cpwer', float('nan')):.3f} DER {row.get('der', float('nan')):.3f}", flush=True)
    (ROOT / "results.json").write_text(json.dumps(results, indent=1))
    (ROOT / "results.md").write_text(report(results))
    print(f"wrote {ROOT / 'results.md'}")


def pooled(rows, key, num, den):
    n = sum(r[num] for r in rows if num in r)
    d = sum(r[den] for r in rows if den in r)
    return n / d if d else None


def report(results):
    groups = defaultdict(list)
    for r in results:
        groups[(r["set"], r["engine"])].append(r)
    lines = ["| Set | Engine | Items | Audio h | WER | cpWER | DER | Wall s per audio h | RTF | Peak GTT delta GiB |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for (set_name, engine), rows in sorted(groups.items()):
        ok = [r for r in rows if "wer" in r]
        hours = sum(r["duration_s"] for r in rows) / 3600
        wall = sum(r["timings"].get("engine_wall_s", 0) for r in rows)
        wer = (sum(r["wer"] * r["ref_words"] for r in ok) / sum(r["ref_words"] for r in ok)) if ok else None
        cp = pooled(ok, "cpwer", "cp_errors", "cp_length")
        dur = sum(r["duration_s"] for r in ok)
        der = sum(r["der"] * r["duration_s"] for r in ok) / dur if dur else None
        gtt = max((r["resources"].get("gtt_peak_delta_gib") or 0) for r in rows)
        f = lambda v: f"{100 * v:.1f}%" if v is not None else "n/a"
        per_hour = f"{wall / hours:.0f} | {wall / (hours * 3600):.3f}" if hours else "n/a | n/a"
        lines.append(f"| {set_name} | {engine} | {len(rows)} | {hours:.2f} | {f(wer)} | {f(cp)} | {f(der)} | "
                     f"{per_hour} | {gtt:.1f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
