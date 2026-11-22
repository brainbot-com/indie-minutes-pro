"""Engine B service: Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B (qwen-asr
package) on the GPU through PyTorch ROCm (AMD TheRock wheels for gfx1151).
Speakers come from engine A's container (`nemo-speech diarize`).

op asr_align {audio, out, language?: ISO code, device: cuda|cpu, align_chunk_s?: 180}

The package cuts long audio at quiet points into chunks of at most
align_chunk_s seconds (the aligner handles up to 5 minutes; the package's own
default is 3) and runs ASR and alignment per chunk. Models load on first use
and are freed after IDLE_UNLOAD_S without jobs."""
import gc
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, "/app")
from jobserver import JobServer, Sampler  # noqa: E402

ASR = os.environ.get("ASR_MODEL", "/models/Qwen3-ASR-1.7B")
ALIGNER = os.environ.get("ALIGNER_MODEL", "/models/Qwen3-ForcedAligner-0.6B")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "600"))
LANGS = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
         "ru": "Russian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "nl": "Dutch", "pl": "Polish"}
ISO = {v: k for k, v in LANGS.items()}

state = {"model": None, "device": None, "last": time.time()}
busy = threading.Lock()
stage = {"align_s": 0.0}


def unload():
    if state["model"] is None:
        return
    import torch
    state["model"] = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load(device):
    if state["model"] is not None and state["device"] == device:
        return state["model"]
    unload()
    import torch
    from qwen_asr import Qwen3ASRModel
    dmap = "cuda:0" if device == "cuda" else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    t0 = time.monotonic()
    model = Qwen3ASRModel.from_pretrained(
        ASR, dtype=dtype, device_map=dmap, max_inference_batch_size=1, max_new_tokens=4096,
        forced_aligner=ALIGNER, forced_aligner_kwargs=dict(dtype=dtype, device_map=dmap))
    aligner = getattr(model, "forced_aligner", None)
    if aligner is not None and hasattr(aligner, "align"):
        inner = aligner.align

        def timed(*a, **kw):  # split alignment time from ASR time
            t = time.monotonic()
            try:
                return inner(*a, **kw)
            finally:
                stage["align_s"] += time.monotonic() - t
        aligner.align = timed
    state.update(model=model, device=device, load_s=round(time.monotonic() - t0, 2))
    return model


def norm(t):
    return re.sub(r"\W+", "", t.lower())


def with_punctuation(text, items):
    """The aligner returns bare words; take the punctuated, capitalized tokens
    of the ASR text in the same order where they match."""
    tokens = text.split()
    out, j = [], 0
    for it in items:
        word, key = it.text, norm(it.text)
        for k in range(j, min(j + 8, len(tokens))):
            if norm(tokens[k]) == key:
                word, j = tokens[k], k + 1
                break
        out.append({"w": word, "s": round(it.start_time, 3), "e": round(it.end_time, 3)})
    return out


def asr_align(params, job):
    import torch
    from qwen_asr.inference import utils as qutils
    with busy:
        qutils.MAX_FORCE_ALIGN_INPUT_SECONDS = float(params.get("align_chunk_s", 180))
        device = "cpu" if params.get("device") == "cpu" else "cuda"
        model = load(device)
        stage["align_s"] = 0.0
        sampler = Sampler(os.getpid()).start()
        t0 = time.monotonic()
        res = model.transcribe(audio=params["audio"], language=LANGS.get((params.get("language") or "")[:2]),
                               return_time_stamps=True)[0]
        wall = time.monotonic() - t0
        resources = sampler.result()
        state["last"] = time.time()
    words = with_punctuation(res.text or "", list(res.time_stamps or []))
    out = {"language": ISO.get(res.language, res.language), "text": res.text, "words": words,
           "wall_s": round(wall, 2), "load_s": state.get("load_s"),
           "timings": {"align_s": round(stage["align_s"], 2), "asr_s": round(wall - stage["align_s"], 2)},
           "resources": resources, "device": device, "torch": torch.__version__, "hip": torch.version.hip,
           "gpu": torch.cuda.get_device_name(0) if device == "cuda" else None,
           "align_chunk_s": qutils.MAX_FORCE_ALIGN_INPUT_SECONDS}
    if params.get("out"):
        with open(params["out"], "w") as f:
            json.dump(out, f, ensure_ascii=False)
    return out


def idle_unloader():
    while True:
        time.sleep(30)
        if state["model"] is not None and time.time() - state["last"] > IDLE_UNLOAD_S:
            with busy:
                if time.time() - state["last"] > IDLE_UNLOAD_S:
                    unload()
                    print("models unloaded after idle time", flush=True)


def info():
    import torch
    return {"engine": "qwen3-asr", "torch": torch.__version__, "hip": torch.version.hip,
            "gpu_available": torch.cuda.is_available(), "loaded": state["model"] is not None,
            "models": {"asr": os.path.exists(ASR), "aligner": os.path.exists(ALIGNER)}}


if __name__ == "__main__":
    threading.Thread(target=idle_unloader, daemon=True).start()
    JobServer({"asr_align": asr_align}, info, int(os.environ.get("PORT", "8091"))).serve()
