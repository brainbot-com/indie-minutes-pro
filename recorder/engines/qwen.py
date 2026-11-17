"""Engine B: Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B (PyTorch ROCm) for words
and times, Nemotron 3 Diarization (`nemo-speech diarize`, Vulkan) for speakers.
Each word gets the speaker whose turn it overlaps most."""
from ..audio import wav_duration
from ..util import guess_language
from .base import Engine, assign_speakers, finish
from .remote import run_job


class QwenEngine(Engine):
    name = "B"
    label = "Qwen3-ASR-1.7B + ForcedAligner-0.6B (ROCm) · Nemotron 3 Diarization (Vulkan)"

    def __init__(self, settings):
        self.s = settings

    def transcribe(self, wav, workdir, language=None, log=print, device="gpu"):
        duration = wav_duration(wav)
        cpu = device == "cpu"
        diar = run_job(self.s.nemo_url, {"op": "diarize", "audio": str(wav), "out": str(workdir / "diarization.json"),
                                         "device": "cpu" if cpu else "vulkan"}, log)
        asr = run_job(self.s.qwen_url, {"op": "asr_align", "audio": str(wav), "out": str(workdir / "asr.json"),
                                        "language": language, "device": "cpu" if cpu else "cuda"}, log)
        turns = [(seg["start"], seg["end"], f"d{seg['speaker']}") for seg in diar.get("segments", [])]
        words = assign_speakers([dict(w) for w in asr.get("words", [])], turns)
        for w in words:
            w["spk"] = w.get("spk") or "d1"
        lang = language or asr.get("language") or guess_language(asr.get("text", ""))
        timings = {"diarize_s": diar["wall_s"], "asr_s": asr["timings"].get("asr_s"),
                   "align_s": asr["timings"].get("align_s"), "load_s": asr.get("load_s"),
                   "engine_wall_s": round(diar["wall_s"] + asr["wall_s"], 2)}
        timings["rtf"] = round(timings["engine_wall_s"] / max(duration, 1e-3), 4)
        resources = {}
        for part in (diar.get("resources") or {}, asr.get("resources") or {}):
            for k, v in part.items():
                if isinstance(v, (int, float)):
                    resources[k] = max(resources.get(k, v), v)
        engine = {"name": self.name, "label": self.label, "backend": "cpu" if cpu else "rocm+vulkan",
                  "torch": asr.get("torch"), "hip": asr.get("hip"), "gpu": asr.get("gpu"),
                  "align_chunk_s": asr.get("align_chunk_s"), "diarization_turns": len(turns)}
        return finish(words, language=lang, duration=duration, engine=engine, timings=timings, resources=resources)
