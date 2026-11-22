"""Accent hints for naming speakers: mavleo96/whisper-accent-medium.en (Apache-2.0;
Whisper medium.en with an accent head for 23 accents of English) on the CPU, so it
neither waits for nor takes the GPU. Only the encoder runs, nothing is transcribed.

ops:
  accent {audio: a 16 kHz mono WAV under /data/scratch, speakers: {label: [[start, end], ...]}}
         -> {model, speakers: {label: [{accent, p}, ...]}}   (the three likeliest)
Each speaker's stretches are joined up to 30 s, Whisper's window. Without the
model (deploy/fetch-accent-model.sh) /v1/health says ready: false, and the
recorder leaves the hints out."""
import os
import sys
import threading

sys.path.insert(0, "/app")
from jobserver import JobServer  # noqa: E402

MODEL_DIR = os.environ.get("ACCENT_MODEL", "/models/whisper-accent-medium.en")
RATE = 16000
WINDOW = 30.0
state = {"model": None, "processor": None, "error": None}


def load():
    """In the background: /v1/health answers at once, ready once the model is in memory."""
    try:
        if not os.path.exists(os.path.join(MODEL_DIR, "model.safetensors")):
            state["error"] = f"no model in {MODEL_DIR} (deploy/fetch-accent-model.sh)"
            return
        import torch
        from whisper_accent.inference import load_model
        torch.set_num_threads(int(os.environ.get("THREADS", "8")))
        state["model"], state["processor"] = load_model(MODEL_DIR, "cpu", torch.float32)
    except Exception as e:
        state["error"] = f"{type(e).__name__}: {e}"


def accent(params, job):
    import numpy as np
    import soundfile as sf
    import torch
    model, processor = state["model"], state["processor"]
    if model is None:
        raise RuntimeError(state["error"] or "the model is still loading")
    names = {v: k for k, v in model.config.accent_to_id.items()}
    out = {}
    with sf.SoundFile(params["audio"]) as f:
        if f.samplerate != RATE or f.channels != 1:
            raise ValueError(f"want 16 kHz mono, got {f.samplerate} Hz x {f.channels}")
        for label, spans in (params.get("speakers") or {}).items():
            parts, total = [], 0.0
            for s, e in spans:
                e = min(float(e), float(s) + WINDOW - total)
                if e <= s:
                    break
                f.seek(int(s * RATE))
                parts.append(f.read(int((e - s) * RATE), dtype="float32"))
                total += e - s
            if not parts:
                continue
            # one speaker at a time: a 30 s window each, memory stays small
            inputs = processor.feature_extractor([np.concatenate(parts)], sampling_rate=RATE,
                                                 return_attention_mask=True, return_tensors="pt")
            with torch.no_grad():
                enc = model.model.encoder(inputs.input_features, attention_mask=inputs.attention_mask, return_dict=True)
                p = torch.softmax(enc.accent_logits.float(), dim=-1)[0]
            top = torch.topk(p, 3)
            out[label] = [{"accent": names[int(i)], "p": round(float(v), 3)} for v, i in zip(top.values, top.indices)]
    return {"model": os.path.basename(MODEL_DIR), "speakers": out}


def info():
    return {"engine": "accent", "ready": state["model"] is not None, "model": os.path.basename(MODEL_DIR),
            "device": "cpu", "error": state["error"]}


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    JobServer({"accent": accent}, info, int(os.environ.get("PORT", "8092")), gpu=False).serve()
