"""Engine A: Nemotron 3.5 Transcribe 30B-A4B in NeMo-Speech.cpp (Vulkan), with
--diarize and word timestamps. The model takes at most one hour per request,
so longer recordings are cut into 59-minute windows that overlap by 60 s;
speaker labels are per window and are linked through the overlap (flagged in
the transcript)."""
import os

from ..audio import slice_wav, wav_duration
from ..util import guess_language
from .base import Engine, finish, merge_windows, plan_windows
from .remote import health, run_job

MAX_S = 59 * 60
OVERLAP_S = 60
DECODER = {"iq4": "AD-IQ4_NL", "q8": "Q8_0"}


class NemoEngine(Engine):
    def __init__(self, settings, variant="iq4"):
        self.s = settings
        self.variant = variant
        self.name = f"A-{variant}"
        # diarize: the CLI's speaker prompt (lowercase, no punctuation), as asked;
        # instruction: the model card's punctuated speaker-tag prompt instead.
        self.mode = os.environ.get("NEMO_MODE", "diarize")
        self.label = f"Nemotron 3.5 Transcribe {DECODER[variant]} · NeMo-Speech.cpp · Vulkan"

    def transcribe(self, wav, workdir, language=None, log=print, device="vulkan"):
        duration = wav_duration(wav)
        windows = plan_windows(duration, MAX_S, OVERLAP_S)
        parts, text, langs = [], [], []
        timings = {"engine_wall_s": 0.0, "queue_and_poll_s": 0.0}
        resources = {}
        for k, (start, end) in enumerate(windows, 1):
            src = wav if len(windows) == 1 else slice_wav(wav, workdir / f"part{k}.wav", start, end)
            if len(windows) > 1:
                log(f"window {k}/{len(windows)}: {start:.0f}-{end:.0f} s")
            res = run_job(self.s.nemo_url, {"op": "transcribe", "audio": str(src), "out": str(workdir / f"part{k}.json"),
                                            "model": self.variant, "language": language, "mode": self.mode,
                                            "device": device}, log)
            timings["engine_wall_s"] += res["wall_s"]
            timings["queue_and_poll_s"] += (res.get("job_wall_s") or res["wall_s"]) - res["wall_s"]
            for key, v in (res.get("timings") or {}).items():
                timings[key] = round(timings.get(key, 0) + v, 3)
            for key, v in (res.get("resources") or {}).items():
                if isinstance(v, (int, float)):
                    resources[key] = max(resources.get(key, v), v)
            words = res.get("words") or []
            if not words and res.get("text"):
                # No alignment: keep the text as one block rather than invent times.
                words = [{"word": res["text"], "start": 0.0, "end": end - start}]
            prev = None
            conv = []
            for w in words:
                spk = w.get("speaker") or prev
                prev = spk
                conv.append({"w": w["word"], "s": w["start"] + start, "e": w["end"] + start,
                             "spk": f"w{k}s{spk}" if spk else f"w{k}s1"})
            parts.append((start, end, conv))
            text.append(res.get("text") or "")
            langs += [x for x in (res.get("languages") or []) if x and x != "und"]
        notes, extra = [], {}
        if len(parts) > 1:
            words, links = merge_windows(parts)
            extra["windows"] = links
            notes.append(f"This recording is longer than 59 minutes, so it was transcribed in {len(parts)} parts "
                         f"that overlap by {OVERLAP_S} s. Speaker labels are per part and were linked through the "
                         "overlap; a label in a later part may still be a different person.")
            scope = "per_chunk"
        else:
            words, scope = parts[0][2], "meeting"
        lang = language or (langs[0] if langs else guess_language(" ".join(text)))
        timings = {k: round(v, 2) for k, v in timings.items()}
        timings["rtf"] = round(timings["engine_wall_s"] / max(duration, 1e-3), 4)
        info = health(self.s.nemo_url)
        engine = {"name": self.name, "label": self.label, "backend": "cpu" if device == "cpu" else "vulkan",
                  "decoder": DECODER[self.variant], "mode": self.mode, "runtime": info.get("version", "")}
        return finish(words, language=lang, duration=duration, engine=engine, notes=notes, scope=scope,
                      timings=timings, resources=resources, extra=extra)
