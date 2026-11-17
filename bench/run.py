"""Runs the engines over the test sets; finished items are skipped, so it can
be stopped and started again.

    python -m bench.run --engines A-iq4,A-q8,B --sets ami-sdm,ami-ihm-mix,mls-de
    python -m bench.run --cpu-proof        # one clip on GPU and on CPU, per engine
    python -m bench.run --probe-only 300   # LLM latency on an otherwise idle box

Results: data/bench/runs/<engine>/<set>/<item>/transcript.json plus engine logs.
While engines run, a probe asks the box's main LLM a fixed question once a
minute and records time to first token and tokens/s (box impact), labelled
with what the GPU was doing at the time."""
import argparse
import json
import threading
import time
from pathlib import Path

import httpx

from recorder.audio import slice_wav, wav_duration
from recorder.config import Settings
from recorder.engines import Engines
from recorder.util import now_iso, write_json

ROOT = Path("/data/bench")
PROBE_Q = "In one sentence each, name three things a team should write down after a meeting."


class Probe(threading.Thread):
    def __init__(self, settings, path, every=60):
        super().__init__(daemon=True)
        self.s, self.path, self.every = settings, path, every
        self.phase = "idle"
        self.stop = threading.Event()

    def ask(self):
        body = {"model": self.s.llm_model, "stream": True, "think": False,
                "messages": [{"role": "user", "content": PROBE_Q}],
                "options": {"temperature": 0, "num_predict": 48}}
        t0 = time.monotonic()
        first, final = None, {}
        with httpx.stream("POST", f"{self.s.ollama_url}/api/chat", json=body, timeout=600) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if first is None and msg.get("message", {}).get("content"):
                    first = time.monotonic() - t0
                if msg.get("done"):
                    final = msg
        total = time.monotonic() - t0
        n, dur = final.get("eval_count") or 0, (final.get("eval_duration") or 0) / 1e9
        return {"ttft_s": round(first or total, 2), "total_s": round(total, 2),
                "tok_s": round(n / dur, 2) if dur else None, "load_s": round((final.get("load_duration") or 0) / 1e9, 2)}

    def run(self):
        while not self.stop.is_set():
            phase = self.phase
            try:
                rec = {"at": now_iso(), "phase": phase, **self.ask()}
            except Exception as e:
                rec = {"at": now_iso(), "phase": phase, "error": str(e)[:200]}
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            self.stop.wait(self.every)


def items(sets):
    for set_name in sets:
        for d in sorted((ROOT / "datasets" / set_name).glob("*/")):
            if (d / "audio.wav").exists():
                yield set_name, d


def run_engines(s, engines, sets, probe):
    eng = Engines(s)
    for name in engines:
        for set_name, d in items(sets):
            out = ROOT / "runs" / name / set_name / d.name
            if (out / "transcript.json").exists():
                continue
            out.mkdir(parents=True, exist_ok=True)
            meta = json.loads((d / "meta.json").read_text())
            probe.phase = f"{name}:{set_name}"
            t0 = time.monotonic()
            print(f"{now_iso()} {name} {set_name}/{d.name} ...", flush=True)
            try:
                t = eng.get(name).transcribe(d / "audio.wav", out, language=meta.get("language"),
                                             log=lambda m: print("   ", m, flush=True))
            except Exception as e:
                write_json(out / "error.json", {"error": str(e), "at": now_iso()})
                print(f"    FAILED: {e}", flush=True)
                continue
            finally:
                probe.phase = "idle"
            t.setdefault("timings", {})["job_wall_s"] = round(time.monotonic() - t0, 2)
            write_json(out / "transcript.json", t)
            print(f"    {len(t['words'])} words, {len(t['speakers'])} speakers, "
                  f"rtf {t['timings'].get('rtf')}", flush=True)


def cpu_proof(s, probe, seconds=120):
    """Same clip on the GPU and on the CPU: if the GPU path silently fell back
    to the CPU, both would take as long and GTT would not move."""
    src = ROOT / "datasets" / "ami-sdm" / "ES2004a" / "audio.wav"
    clip = ROOT / "runs" / "cpu-proof" / "clip.wav"
    if not clip.exists():
        slice_wav(src, clip, 0, seconds)
    eng = Engines(s)
    results = {}
    for name in ("A-iq4", "B"):
        for device in ("gpu", "cpu"):
            out = ROOT / "runs" / "cpu-proof" / f"{name}-{device}"
            if (out / "transcript.json").exists():
                results[f"{name}-{device}"] = json.loads((out / "summary.json").read_text())
                continue
            out.mkdir(parents=True, exist_ok=True)
            probe.phase = f"{name}:cpu-proof-{device}"
            t0 = time.monotonic()
            kw = {"device": "cpu"} if device == "cpu" else {}
            t = eng.get(name).transcribe(clip, out, language="en", log=lambda m: print("   ", m, flush=True), **kw)
            probe.phase = "idle"
            summary = {"clip_s": wav_duration(clip), "wall_s": round(time.monotonic() - t0, 2),
                       "timings": t.get("timings"), "resources": t.get("resources"),
                       "backend": t["engine"].get("backend"), "words": len(t["words"])}
            write_json(out / "transcript.json", t)
            write_json(out / "summary.json", summary)
            results[f"{name}-{device}"] = summary
            print(f"{name} on {device}: {summary['wall_s']} s for {seconds} s audio", flush=True)
    write_json(ROOT / "runs" / "cpu-proof" / "results.json", results)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="A-iq4,A-q8,B")
    ap.add_argument("--sets", default="ami-sdm,ami-ihm-mix,mls-de")
    ap.add_argument("--cpu-proof", action="store_true")
    ap.add_argument("--probe-only", type=int, default=0, help="seconds of idle LLM probing")
    ap.add_argument("--probe-every", type=int, default=60)
    a = ap.parse_args(argv)
    s = Settings()
    (ROOT / "runs").mkdir(parents=True, exist_ok=True)
    probe = Probe(s, ROOT / "runs" / "llm-probe.jsonl", a.probe_every)
    probe.start()
    try:
        if a.probe_only:
            time.sleep(a.probe_only)
        elif a.cpu_proof:
            cpu_proof(s, probe)
        else:
            run_engines(s, a.engines.split(","), a.sets.split(","), probe)
    finally:
        probe.stop.set()


if __name__ == "__main__":
    main()
