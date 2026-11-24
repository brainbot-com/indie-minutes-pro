"""Test data: AMI meetings (single distant mic and headset mix) with their
reference words and speaker segments, and simulated German conversations
stitched from Multilingual LibriSpeech (MLS) German with exact references.

    python -m bench.data ami [--meetings ES2004a,...]
    python -m bench.data mls-de [--conversations 4]

Writes data/bench/datasets/<set>/<item>/{audio.wav, ref.seglst.json, ref.rttm, meta.json}.
AMI audio comes from the diarizers-community/ami mirror on Hugging Face (whole
meetings; the Edinburgh mirror refuses scripted bulk downloads), the words and
abstractive summaries from AMI's manual annotations, the speaker segments from
BUT's AMI-diarization-setup. AMI: CC BY 4.0, University of Edinburgh et al.
MLS: CC BY 4.0, Meta AI."""
import argparse
import io
import json
import random
import re
import shutil
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path("/data/bench/datasets")
HF_AMI = "https://huggingface.co/datasets/diarizers-community/ami/resolve/main/{config}/test-{i:05d}-of-00003.parquet"
AMI_ANN = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
BUT = "https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/main"
# Ten meetings of the Full-corpus-ASR test set, all three scenario sites plus a
# non-scenario one. The three in SPOTCHECK have abstractive summaries.
AMI_MEETINGS = ["ES2004a", "ES2004b", "ES2004c", "IS1009a", "IS1009b", "IS1009c",
                "TS3003a", "TS3003b", "TS3003c", "EN2002a"]
CHANNELS = {"sdm": "Array1-01", "ihm-mix": "Mix-Headset"}
HF_CONFIG = {"sdm": "sdm", "ihm-mix": "ihm"}
MLS_TREE = "https://huggingface.co/api/datasets/facebook/multilingual_librispeech/tree/main/german"
MLS_FILE = "https://huggingface.co/datasets/facebook/multilingual_librispeech/resolve/main/{path}"
UA = "minutes-pro-bench/0.1 (+https://github.com/brainbot-com/indie-minutes-pro)"


def fetch(url, dst):
    dst = Path(dst)
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(".part-" + dst.name)
    # The Edinburgh mirror answers Python's default User-Agent with 403.
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.replace(dst)
    return dst


def segments_from_words(words, gap=0.5):
    """words: [(start, end, speaker, text)] sorted -> utterance segments per speaker."""
    by_spk = {}
    for w in words:
        by_spk.setdefault(w[2], []).append(w)
    segs = []
    for spk, ws in by_spk.items():
        cur = None
        for s, e, _, t in sorted(ws):
            if cur and s - cur["end_time"] <= gap:
                cur["words"] += " " + t
                cur["end_time"] = max(cur["end_time"], e)
            else:
                cur = {"speaker": spk, "start_time": s, "end_time": e, "words": t}
                segs.append(cur)
    return sorted(segs, key=lambda x: x["start_time"])


def ami_words(zf, meeting):
    """Reference words of one meeting from the NXT words files (punctuation,
    vocal sounds, pauses and other non-word elements dropped)."""
    out = []
    for name in sorted(n for n in zf.namelist() if re.fullmatch(rf"words/{meeting}\.[A-E]\.words\.xml", n)):
        letter = name.split(".")[1]
        root = ET.fromstring(zf.read(name))
        last = 0.0
        for el in root.iter():
            if el.tag != "w" or el.get("punc") == "true" or not (el.text or "").strip():
                continue
            s = float(el.get("starttime") or last)
            e = float(el.get("endtime") or s)
            last = e
            out.append((s, e, letter, el.text.strip()))
    return sorted(out)


def ami_actions(zf, meeting):
    """The 'actions' sentences of AMI's human abstractive summary, if any."""
    name = f"abstractive/{meeting}.abssumm.xml"
    if name not in zf.namelist():
        return None
    root = ET.fromstring(zf.read(name))
    out = {}
    for part in ("abstract", "actions", "decisions", "problems"):
        sec = root.find(part)
        out[part] = [s.text.strip() for s in sec.iter("sentence") if (s.text or "").strip()] if sec is not None else []
    return out


def ami_audio(short, meetings, cache):
    """Write cache/ami/<meeting>.<channel>.wav for the wanted test meetings from
    the Hugging Face mirror. Rows are matched by file name, else by duration
    against BUT's UEM files (exact to the sample)."""
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile as sf

    want = {m for m in meetings if not (cache / f"ami/{m}.{CHANNELS[short]}.wav").exists()}
    if not want:
        return
    uem = {}
    for m in want:
        line = fetch(f"{BUT}/uems/test/{m}.uem", cache / f"uem/{m}.uem").read_text().split()
        uem[m] = float(line[3])
    for i in range(3):
        shard = fetch(HF_AMI.format(config=HF_CONFIG[short], i=i), cache / f"hf-ami-{short}-test-{i}.parquet")
        for batch in pq.ParquetFile(shard).iter_batches(batch_size=1, columns=["audio"]):
            a = batch.column("audio")[0].as_py()
            x, rate = sf.read(io.BytesIO(a["bytes"]), dtype="int16")
            if x.ndim > 1:
                x = x.mean(axis=1).astype(np.int16)
            name = Path(a.get("path") or "").name
            m = next((m for m in want if name.startswith(m + ".")), None)
            if not m:
                m = next((m for m in want if abs(len(x) / rate - uem[m]) < 0.05), None)
            if m:
                dst = cache / f"ami/{m}.{CHANNELS[short]}.wav"
                dst.parent.mkdir(parents=True, exist_ok=True)
                sf.write(dst, x, rate, subtype="PCM_16")
                want.discard(m)
                print(f"  {m} {short}: {len(x) / rate / 60:.1f} min from the HF mirror", flush=True)
        if not want:
            return
    raise SystemExit(f"not found in the mirror: {sorted(want)}")


def prep_ami(meetings):
    cache = ROOT.parent / "cache"
    ann = zipfile.ZipFile(fetch(AMI_ANN, cache / "ami_public_manual_1.6.2.zip"))
    for short in CHANNELS:
        ami_audio(short, meetings, cache)
    for m in meetings:
        words = ami_words(ann, m)
        segs = segments_from_words(words)
        rttm = fetch(f"{BUT}/only_words/rttms/test/{m}.rttm", cache / f"rttm/{m}.rttm").read_text()
        summary = ami_actions(ann, m)
        for short, ch in CHANNELS.items():
            d = ROOT / f"ami-{short}" / m
            d.mkdir(parents=True, exist_ok=True)
            src = cache / f"ami/{m}.{ch}.wav"
            if not (d / "audio.wav").exists():
                shutil.copy(src, d / "audio.wav")
            seg = [{"session_id": m, **s} for s in segs]
            (d / "ref.seglst.json").write_text(json.dumps(seg, indent=0))
            (d / "ref.rttm").write_text(rttm)
            meta = {"set": f"ami-{short}", "item": m, "language": "en", "channel": ch,
                    "speakers": len({s["speaker"] for s in segs}), "words": len(words),
                    "source": "AMI Meeting Corpus, CC BY 4.0", "abstractive": summary}
            (d / "meta.json").write_text(json.dumps(meta, indent=1))
        print(f"{m}: {len(words)} words, {len({w[2] for w in words})} speakers", flush=True)


def prep_mls_de(n_conv, seed=7):
    import numpy as np
    import pyarrow.parquet as pq
    import soundfile as sf

    files = json.load(urllib.request.urlopen(urllib.request.Request(MLS_TREE, headers={"User-Agent": UA}), timeout=60))
    shard = sorted(f["path"] for f in files if "/test" in f["path"] and f["path"].endswith(".parquet"))[0]
    table = pq.read_table(fetch(MLS_FILE.format(path=shard), ROOT.parent / "cache" / Path(shard).name))
    rows = table.to_pylist()
    by_spk = {}
    for r in rows:
        by_spk.setdefault(str(r["speaker_id"]), []).append(r)
    speakers = sorted((s for s, rs in by_spk.items() if len(rs) >= 12), key=lambda s: -len(by_spk[s]))
    rng = random.Random(seed)
    plan = ([2, 2, 3, 4] * 3)[:n_conv]
    used = set()
    for i, n_spk in enumerate(plan, 1):
        spk = [s for s in speakers if s not in used][:n_spk]
        used.update(spk)
        pools = {s: list(by_spk[s]) for s in spk}
        for p in pools.values():
            rng.shuffle(p)
        audio, segs, t, rate = [], [], 0.0, 16000
        current = None
        while t < 360 and any(pools.values()):
            choices = [s for s in spk if pools[s] and s != current] or [s for s in spk if pools[s]]
            current = rng.choice(choices)
            for _ in range(rng.choice([1, 1, 2])):  # a turn: one or two utterances
                if not pools[current]:
                    break
                r = pools[current].pop()
                x, sr = sf.read(io.BytesIO(r["audio"]["bytes"]), dtype="float32")
                if sr != rate:
                    raise SystemExit(f"unexpected sample rate {sr}")
                gap = rng.uniform(0.25, 0.9)
                audio.append(np.zeros(int(gap * rate), dtype="float32"))
                t += gap
                segs.append({"session_id": f"mls-de-{i}", "speaker": current, "start_time": round(t, 3),
                             "end_time": round(t + len(x) / rate, 3), "words": r["transcript"].strip()})
                audio.append(x)
                t += len(x) / rate
        d = ROOT / "mls-de" / f"conv{i}"
        d.mkdir(parents=True, exist_ok=True)
        sf.write(d / "audio.wav", np.concatenate(audio), rate, subtype="PCM_16")
        (d / "ref.seglst.json").write_text(json.dumps(segs, ensure_ascii=False, indent=0))
        (d / "ref.rttm").write_text("".join(
            f"SPEAKER mls-de-{i} 1 {s['start_time']:.3f} {s['end_time'] - s['start_time']:.3f} <NA> <NA> {s['speaker']} <NA> <NA>\n"
            for s in segs))
        meta = {"set": "mls-de", "item": f"conv{i}", "language": "de", "speakers": n_spk, "duration_s": round(t, 1),
                "source": f"Multilingual LibriSpeech German ({shard}), CC BY 4.0"}
        (d / "meta.json").write_text(json.dumps(meta, indent=1))
        print(f"conv{i}: {n_spk} speakers, {t / 60:.1f} min, {len(segs)} utterances", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["ami", "mls-de"])
    ap.add_argument("--meetings", default=",".join(AMI_MEETINGS))
    ap.add_argument("--conversations", type=int, default=4)
    a = ap.parse_args(argv)
    if a.what == "ami":
        prep_ami(a.meetings.split(","))
    else:
        prep_mls_de(a.conversations)
    subprocess.run(["chmod", "-R", "a+rX", str(ROOT)], check=False)


if __name__ == "__main__":
    main()
