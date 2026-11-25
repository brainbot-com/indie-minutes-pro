"""Engine A service: NVIDIA NeMo-Speech.cpp (prebuilt Vulkan archive) running
Nemotron 3.5 Transcribe 30B-A4B, plus `nemo-speech diarize` with Nemotron 3
Diarization for engine B.

ops:
  transcribe  {audio, out, model: iq4|q8, language?, mode: diarize|instruction|plain, device: vulkan|cpu}
  diarize     {audio, out, device}
The CLI is started per job, so nothing stays in GPU memory between meetings.

FETCH_MODELS=iq4 (an Indie Box, a fresh machine) downloads what that decoder
needs on start, about 21 GB, from Hugging Face at a pinned revision, resumes
after a restart, and checks each file's SHA-256 before using it. /v1/health
says how far it is; the recorder holds recordings until the model is there.

NEMO_DEVICE picks the GPU where it is not Vulkan: metal (a Mac, with the
macos-aarch64-metal build run natively: standalone/mac/engine.sh), cuda:0, or
cpu on a machine without a usable GPU (slow: about half real time)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# jobserver.py sits next to this file in the image (/app) and one folder up in the repo
sys.path[:0] = ["/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
from jobserver import JobServer, Sampler  # noqa: E402

BIN = os.environ.get("NEMO_SPEECH_BIN", "nemo-speech")
MODELS = os.environ.get("MODELS", "/models")
CKPT = "NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"
DECODERS = {"iq4": f"{MODELS}/nemotron-transcribe/{CKPT}-AD-IQ4_NL.gguf",
            "q8": f"{MODELS}/nemotron-transcribe/{CKPT}-Q8_0.gguf"}
DIAR_MODEL = f"{MODELS}/nemotron-diar/Nemotron-3-Diarization.q8_0.gguf"
ENCODER = f"{MODELS}/nemotron-transcribe/{CKPT}-Encoder-F16.gguf"

# what FETCH_MODELS downloads: the files on Hugging Face at this revision, with their size and SHA-256
REPO, REVISION = "AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF", "dbd06c5cad7a2f33c8e06be2fb3ef814dbd9aa59"
FILES = {
    ENCODER: (1670410080, "4221029c8a565c4a80c507ffa760c716e595acf663f9f4273ce69910050e7ba2"),
    DECODERS["iq4"]: (19678656672, "0c47d4ab6c783e7606ec03714b354fab0b2052027a7ea1991e7e2d3009c345ad"),
    DECODERS["q8"]: (35004643232, "3d99075b58f15153d87e8922f54e12dda818bbb505c2e0d7d50cd4f30beac9f2"),
}
FETCH = {"state": "idle"}
SPEAKER_INSTRUCTION = ("Transcribe this audio with punctuation and capitalization. Include all spoken words and "
                       "verbal fillers. Write numbers in their standard written form. Put speaker tags <spk:0> "
                       "<spk:1> <spk:2> <spk:3> <spk:4> <spk:5> <spk:6> <spk:7> on the transcription.")
# One sequence that holds one hour (12.5 audio tokens/s + transcript): serial and 1 h capable.
SALM_ARGS = ["--asr.salm.max_sequences", "1", "--asr.salm.n_ctx_per_sequence", "73728"]


DEVICE = os.environ.get("NEMO_DEVICE", "vulkan:0")


def device_arg(params):
    return "cpu" if params.get("device") == "cpu" else DEVICE


def run(cmd, log_path, job):
    """Run the CLI to the end (never killed), sampling GPU memory and busy."""
    job["cmd"] = cmd
    t0 = time.monotonic()
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env={**os.environ})
        job["pid"] = proc.pid
        sampler = Sampler(proc.pid).start()
        code = proc.wait()
    resources = sampler.result()
    wall = round(time.monotonic() - t0, 2)
    text = open(log_path, errors="replace").read()
    if code != 0:
        raise RuntimeError(f"nemo-speech exited with {code}: {text[-1500:]}")
    return wall, resources, text


def parse_timings(text):
    """Pick up whatever stage timings the CLI prints with --verbose (seconds)."""
    out = {}
    for key in ("load", "encode", "encoder", "prefill", "decode", "align", "diarize", "total"):
        m = re.findall(rf"\b{key}\w*\s*[:=]\s*([0-9.]+)\s*(ms|s)\b", text, re.I)
        if m:
            v, unit = m[-1]
            out[f"{key}_s"] = round(float(v) / (1000 if unit.lower() == "ms" else 1), 3)
    return out


def transcribe(params, job):
    model = DECODERS[params.get("model", "iq4")]
    out = params["out"]
    # --force: a meeting transcribed again writes to the same file.
    cmd = [BIN, "--verbose", "transcribe", params["audio"], "--model", model, "--device", device_arg(params),
           "--format", "json", "-o", out, "--force", *SALM_ARGS]
    if params.get("language"):
        cmd += ["-l", params["language"]]
    mode = params.get("mode", "diarize")
    if mode == "diarize":
        cmd += ["--diarize"]
    elif mode == "instruction":
        cmd += ["--instruction", SPEAKER_INSTRUCTION]
    # plain: the default transcription prompt, no speakers (a separate diarizer adds them)
    wall, resources, text = run(cmd, out + ".log", job)
    result = json.load(open(out))
    return {"words": result.get("words", []), "languages": result.get("languages"), "text": result.get("text", ""),
            "duration": result.get("duration"), "wall_s": wall, "timings": parse_timings(text),
            "resources": resources, "model_file": os.path.basename(model), "device": device_arg(params)}


def diarize(params, job):
    out = params["out"]
    cmd = [BIN, "--verbose", "diarize", params["audio"], "--model", DIAR_MODEL, "--device", device_arg(params),
           "--format", "json", "-o", out, "--no-batching", "--force"]
    wall, resources, text = run(cmd, out + ".log", job)
    result = json.load(open(out))
    return {"segments": result.get("segments", []), "wall_s": wall, "timings": parse_timings(text),
            "resources": resources, "device": device_arg(params)}


def fetch(variants):
    """Downloads the encoder and the decoders named (iq4, q8) unless they are there."""
    todo = [f for f in [ENCODER] + [DECODERS[v] for v in variants] if not os.path.exists(f)]
    total = sum(FILES[f][0] for f in todo)
    FETCH.update(state="downloading" if todo else "ready", done=0, total=total, file=None, error=None)
    base = 0
    try:
        for path in todo:
            size, sha = FILES[path]
            FETCH["file"] = os.path.basename(path)
            got = _download(path, size, sha, lambda n: FETCH.update(done=base + n))
            base += got
        FETCH.update(state="ready", file=None, done=total)
    except Exception as e:
        FETCH.update(state="failed", error=f"{type(e).__name__}: {e}"[:300])
        print(f"model download failed: {FETCH['error']}", flush=True)


def _download(path, size, sha, progress):
    """One file, into path.part (resumed after a restart), checked, then renamed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part, h = path + ".part", hashlib.sha256()
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have > size:
        os.remove(part)
        have = 0
    with open(part, "ab") as out:
        if have:                                  # the part already there counts towards the hash
            with open(part, "rb") as old:
                for block in iter(lambda: old.read(1 << 24), b""):
                    h.update(block)
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{os.path.basename(path)}"
        pause = 10
        while have < size:
            req = urllib.request.Request(url, headers={"Range": f"bytes={have}-", "User-Agent": "minutes-pro-engine"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    if have and r.status != 206:
                        raise RuntimeError(f"the server ignored the range request ({r.status})")
                    for block in iter(lambda: r.read(1 << 20), b""):
                        out.write(block)
                        h.update(block)
                        have += len(block)
                        progress(have)
            except OSError as e:                  # offline, or a dropped connection: go on from where it stopped
                if isinstance(e, urllib.error.HTTPError) and e.code not in (408, 429, 500, 502, 503, 504):
                    raise
                print(f"download of {os.path.basename(path)} stopped at {have} bytes ({e}), again in {pause} s", flush=True)
                out.flush()
                time.sleep(pause)
                pause = min(pause * 2, 300)
    if have != size or h.hexdigest() != sha:
        os.remove(part)
        raise RuntimeError(f"{os.path.basename(path)} does not match its checksum: downloaded again on the next start")
    os.replace(part, path)
    return size


_version = []


def info():
    if not _version:                      # asked once: health is polled often
        try:
            _version.append(subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=20).stdout.strip())
        except Exception as e:
            return {"engine": "nemo-speech.cpp", "version": f"unknown ({e})", "models": {}, "ready": False,
                    "fetch": dict(FETCH)}
    version = _version[0]
    have = lambda *paths: all(os.path.exists(p) for p in paths)   # noqa: E731
    models = {"iq4": have(ENCODER, DECODERS["iq4"]), "q8": have(ENCODER, DECODERS["q8"]), "diar": have(DIAR_MODEL)}
    return {"engine": "nemo-speech.cpp", "version": version, "models": models, "ready": models["iq4"] or models["q8"],
            "fetch": dict(FETCH)}


if __name__ == "__main__":
    wanted = [v for v in os.environ.get("FETCH_MODELS", "").replace(" ", "").split(",") if v]
    if wanted:
        unknown = [v for v in wanted if v not in DECODERS]
        if unknown:
            sys.exit(f"FETCH_MODELS: unknown {unknown}, expected some of {sorted(DECODERS)}")
        threading.Thread(target=fetch, args=(wanted,), name="fetch", daemon=True).start()
    JobServer({"transcribe": transcribe, "diarize": diarize}, info, int(os.environ.get("PORT", "8090"))).serve()
