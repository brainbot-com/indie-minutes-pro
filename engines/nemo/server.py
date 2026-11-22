"""Engine A service: NVIDIA NeMo-Speech.cpp (prebuilt Vulkan archive) running
Nemotron 3.5 Transcribe 30B-A4B, plus `nemo-speech diarize` with Nemotron 3
Diarization for engine B.

ops:
  transcribe  {audio, out, model: iq4|q8, language?, mode: diarize|instruction|plain, device: vulkan|cpu}
  diarize     {audio, out, device}
The CLI is started per job, so nothing stays in GPU memory between meetings."""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, "/app")
from jobserver import JobServer, Sampler  # noqa: E402

BIN = os.environ.get("NEMO_SPEECH_BIN", "nemo-speech")
MODELS = os.environ.get("MODELS", "/models")
CKPT = "NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"
DECODERS = {"iq4": f"{MODELS}/nemotron-transcribe/{CKPT}-AD-IQ4_NL.gguf",
            "q8": f"{MODELS}/nemotron-transcribe/{CKPT}-Q8_0.gguf"}
DIAR_MODEL = f"{MODELS}/nemotron-diar/Nemotron-3-Diarization.q8_0.gguf"
SPEAKER_INSTRUCTION = ("Transcribe this audio with punctuation and capitalization. Include all spoken words and "
                       "verbal fillers. Write numbers in their standard written form. Put speaker tags <spk:0> "
                       "<spk:1> <spk:2> <spk:3> <spk:4> <spk:5> <spk:6> <spk:7> on the transcription.")
# One sequence that holds one hour (12.5 audio tokens/s + transcript): serial and 1 h capable.
SALM_ARGS = ["--asr.salm.max_sequences", "1", "--asr.salm.n_ctx_per_sequence", "73728"]


def device_arg(params):
    return "cpu" if params.get("device") == "cpu" else "vulkan:0"


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


def info():
    try:
        version = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        version = f"unknown ({e})"
    return {"engine": "nemo-speech.cpp", "version": version,
            "models": {k: os.path.exists(v) for k, v in {**DECODERS, "diar": DIAR_MODEL}.items()}}


if __name__ == "__main__":
    JobServer({"transcribe": transcribe, "diarize": diarize}, info, int(os.environ.get("PORT", "8090"))).serve()
