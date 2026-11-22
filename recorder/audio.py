"""ffmpeg and WAV helpers. The engines all read 16 kHz mono 16-bit PCM."""
import json
import subprocess
import wave
from pathlib import Path

RATE = 16000

EXT_BY_MIME = {
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/flac": "flac", "video/webm": "webm", "video/mp4": "mp4",
}


def ext_for_mime(mime):
    base = (mime or "").split(";")[0].strip().lower()
    return EXT_BY_MIME.get(base, "bin")


def to_wav16k(src, dst):
    dst = Path(dst)
    tmp = dst.with_name(".tmp-" + dst.name)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(tmp)],
        check=True, capture_output=True, text=True)
    tmp.replace(dst)
    return dst


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def wav_duration(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def slice_wav(src, dst, start, end):
    """Exact sample slice of a PCM WAV (no re-encode)."""
    with wave.open(str(src), "rb") as r:
        rate = r.getframerate()
        first = max(0, int(start * rate))
        last = min(r.getnframes(), int(end * rate))
        r.setpos(first)
        frames = r.readframes(last - first)
        params = r.getparams()
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    return dst
