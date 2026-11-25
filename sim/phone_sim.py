#!/usr/bin/env python3
"""Phone simulator: streams an audio file through the same chunked upload API
the PWA uses, then optionally waits for the transcript and summary.

    python3 sim/phone_sim.py --url https://10.1.9.7:8443 --cacert ca.crt \\
        --token-file ~/.config/minutes-pro/token meeting.wav --title "Weekly" --wait

Like the phone it encodes to WebM/Opus first (--encode opus, needs ffmpeg; use
--encode none to send the file as it is) and uploads 30 s worth of bytes per
chunk. --flaky 0.2 fails a fifth of the uploads on the first try and sends them
again, --realtime paces chunks like a live recording. Only the standard library
is used, so it runs on any machine with Python 3.9+. Prints JSON."""
import argparse
import hashlib
import json
import math
import os
import random
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

MIME = {"wav": "audio/wav", "webm": "audio/webm", "ogg": "audio/ogg", "mp3": "audio/mpeg",
        "m4a": "audio/mp4", "mp4": "audio/mp4", "flac": "audio/flac"}


class Api:
    def __init__(self, url, token, cacert=None, insecure=False):
        self.url = url.rstrip("/")
        self.token = token
        if insecure:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(cafile=cacert) if cacert else ssl.create_default_context()

    def call(self, method, path, body=None, headers=None, raw=False):
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        data = body
        if body is not None and not raw:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=120) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, {"error": payload.decode(errors="replace")[:300]}


def duration_of(path):
    if path.suffix.lower() == ".wav":
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def encode_opus(src, dst):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", "-f", "webm", str(dst)],
                   check=True)
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("audio", type=Path)
    ap.add_argument("--url", default=os.environ.get("IR_URL", "https://10.1.9.7:8443"))
    ap.add_argument("--token", default=os.environ.get("IR_TOKEN"))
    ap.add_argument("--token-file", type=Path)
    ap.add_argument("--cacert", default=os.environ.get("IR_CACERT"))
    ap.add_argument("--insecure", action="store_true", help="skip certificate checks (testing only)")
    ap.add_argument("--encode", choices=["opus", "none"], default="opus")
    ap.add_argument("--chunk-seconds", type=float, default=30)
    ap.add_argument("--title")
    ap.add_argument("--language", help="hint for the engine, e.g. de or en")
    ap.add_argument("--client-id", help="resume an earlier upload with this id")
    ap.add_argument("--flaky", type=float, default=0.0, help="share of uploads that fail once")
    ap.add_argument("--realtime", action="store_true", help="wait chunk-seconds between chunks")
    ap.add_argument("--wait", action="store_true", help="wait until the box has finished the meeting")
    ap.add_argument("--timeout", type=float, default=6 * 3600, help="seconds to wait with --wait")
    a = ap.parse_args(argv)

    token = a.token or (a.token_file.read_text().strip() if a.token_file else None)
    if not token:
        ap.error("give --token, --token-file or IR_TOKEN")
    api = Api(a.url, token, a.cacert, a.insecure)
    rng = random.Random(42)
    t_start = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp:
        src = a.audio
        if a.encode == "opus":
            src = encode_opus(a.audio, Path(tmp) / "phone.webm")
        data = src.read_bytes()
        seconds = duration_of(src)
        mime = MIME.get(src.suffix.lower().lstrip("."), "application/octet-stream")
        per_chunk = max(1, math.ceil(len(data) / max(seconds, 1e-3) * a.chunk_seconds))
        chunks = [data[i:i + per_chunk] for i in range(0, len(data), per_chunk)]

        client_id = a.client_id or str(uuid.uuid4())
        status, rec = api.call("POST", "/api/v1/recordings", {
            "client_id": client_id, "mime": mime, "title": a.title or a.audio.stem, "language": a.language})
        if status != 200:
            sys.exit(f"create failed: {status} {rec}")
        rid = rec["recording_id"]
        have = set(rec.get("received", []))
        retries = 0
        for idx, chunk in enumerate(chunks):
            if idx in have:
                continue
            if a.realtime and idx:
                time.sleep(a.chunk_seconds)
            sha = hashlib.sha256(chunk).hexdigest()
            for attempt in range(8):
                if a.flaky and attempt == 0 and rng.random() < a.flaky:
                    retries += 1
                    continue  # pretend the network dropped this upload
                status, res = api.call("PUT", f"/api/v1/recordings/{rid}/chunks/{idx}", chunk,
                                       {"Content-Type": "application/octet-stream", "X-Chunk-SHA256": sha}, raw=True)
                if status == 200:
                    break
                retries += 1
                time.sleep(min(30, 2 ** attempt))
            else:
                sys.exit(f"chunk {idx} failed: {status} {res}")
        status, rec = api.call("POST", f"/api/v1/recordings/{rid}/finalize", {"total_chunks": len(chunks), "title": a.title})
        if status != 200:
            sys.exit(f"finalize failed: {status} {rec}")
        upload_s = time.monotonic() - t_start

        out = {"client_id": client_id, "recording_id": rid, "meeting_id": rec["meeting_id"],
               "meeting_url": rec["meeting_url"], "chunks": len(chunks), "bytes": len(data),
               "audio_s": round(seconds, 1), "upload_s": round(upload_s, 1), "retries": retries,
               "state": rec["meeting_state"]}
        if a.wait:
            deadline = time.monotonic() + a.timeout
            while time.monotonic() < deadline:
                status, rec = api.call("GET", f"/api/v1/recordings/{rid}")
                if status == 200 and rec["meeting_state"] in ("done", "failed"):
                    break
                time.sleep(10)
            out["state"] = rec.get("meeting_state")
            out["processing_s"] = round(time.monotonic() - t_start - upload_s, 1)
            status, m = api.call("GET", f"/api/v1/meetings/{out['meeting_id']}")
            if status == 200:
                out["title"] = m.get("title")
                out["tldr"] = m.get("tldr")
                out["speakers"] = m.get("speakers")
                out["actions"] = len(m.get("actions", []))
                out["error"] = m.get("error")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if out.get("state") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
