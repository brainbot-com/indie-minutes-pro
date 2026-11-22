"""Tiny job server shared by the engine containers (standard library only).

POST /v1/jobs {"op": ..., ...}  -> {"id": ...}        queue a job
GET  /v1/jobs/<id>              -> state, result, timings, resources
GET  /v1/health                 -> engine info

One job runs at a time per container, and a file lock on the shared data
volume (/data/.gpu.lock) keeps the two engine containers from using the GPU at
the same time. A running GPU process is never killed: on this box a killed
Vulkan job once stalled the amdgpu driver. While a job runs, GTT use and GPU
busy are sampled from sysfs (rocm-smi shows no VRAM on this APU)."""
import fcntl
import glob
import json
import os
import queue
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GPU_LOCK = os.environ.get("GPU_LOCK", "/data/.gpu.lock")


def amd_card():
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        try:
            if open(f"{dev}/vendor").read().strip() == "0x1002" and os.path.exists(f"{dev}/mem_info_gtt_used"):
                return dev
        except OSError:
            pass
    return None


CARD = amd_card()


def read_int(path):
    try:
        return int(open(path).read().strip())
    except (OSError, ValueError):
        return None


def gpu_sample():
    if not CARD:
        return {}
    return {"gtt_used": read_int(f"{CARD}/mem_info_gtt_used"), "vram_used": read_int(f"{CARD}/mem_info_vram_used"),
            "busy": read_int(f"{CARD}/gpu_busy_percent")}


class Sampler:
    """Samples GPU memory and busy plus a process's RSS twice a second."""

    def __init__(self, pid=None):
        self.pid = pid
        self.base = gpu_sample()
        self.peak_gtt = self.base.get("gtt_used") or 0
        self.busy = []
        self.peak_rss = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        while not self.stop.wait(0.5):
            s = gpu_sample()
            if s.get("gtt_used"):
                self.peak_gtt = max(self.peak_gtt, s["gtt_used"])
            if s.get("busy") is not None:
                self.busy.append(s["busy"])
            if self.pid:
                self.peak_rss = max(self.peak_rss, rss_of(self.pid))

    def result(self):
        self.stop.set()
        self.thread.join(timeout=2)
        base = self.base.get("gtt_used") or 0
        return {
            "gtt_base_gib": round(base / 2**30, 2),
            "gtt_peak_gib": round(self.peak_gtt / 2**30, 2),
            "gtt_peak_delta_gib": round((self.peak_gtt - base) / 2**30, 2),
            "gpu_busy_avg_pct": round(sum(self.busy) / len(self.busy), 1) if self.busy else None,
            "gpu_busy_samples": len(self.busy),
            "rss_peak_gib": round(self.peak_rss / 2**30, 2) if self.peak_rss else None,
        }


def rss_of(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


class gpu_lock:
    def __enter__(self):
        os.makedirs(os.path.dirname(GPU_LOCK), exist_ok=True)
        self.f = open(GPU_LOCK, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


class JobServer:
    """handlers: {op: fn(job_params) -> dict}; info: fn() -> dict for /v1/health."""

    def __init__(self, handlers, info, port, keep=500):
        self.handlers = handlers
        self.info = info
        self.port = port
        self.keep = keep
        self.jobs = {}
        self.order = []
        self.q = queue.Queue()
        self.current = None

    def submit(self, params):
        if params.get("op") not in self.handlers:
            raise ValueError(f"op must be one of {sorted(self.handlers)}")
        for k in ("audio", "out"):
            p = params.get(k)
            if p and not os.path.realpath(p).startswith("/data/"):
                raise ValueError(f"{k} must be under /data")
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {"id": jid, "state": "queued", "params": params, "created": time.time()}
        self.order.append(jid)
        while len(self.order) > self.keep:
            self.jobs.pop(self.order.pop(0), None)
        self.q.put(jid)
        return jid

    def worker(self):
        while True:
            jid = self.q.get()
            job = self.jobs.get(jid)
            if not job:
                continue
            self.current = jid
            job["state"] = "waiting for GPU"
            with gpu_lock():
                job["state"] = "running"
                job["started"] = time.time()
                try:
                    job["result"] = self.handlers[job["params"]["op"]](job["params"], job)
                    job["state"] = "done"
                except Exception as e:
                    job["state"] = "failed"
                    job["error"] = f"{type(e).__name__}: {e}"
                    job["trace"] = traceback.format_exc()[-4000:]
                job["finished"] = time.time()
                job["wall_s"] = round(job["finished"] - job["started"], 2)
            self.current = None

    def serve(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/health":
                    return self.reply(200, {"ok": True, "current": server.current, "queued": server.q.qsize(),
                                            "card": CARD, **server.info()})
                if self.path.startswith("/v1/jobs/"):
                    job = server.jobs.get(self.path.rsplit("/", 1)[1])
                    return self.reply(200, job) if job else self.reply(404, {"error": "no such job"})
                self.reply(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/v1/jobs":
                    return self.reply(404, {"error": "not found"})
                try:
                    params = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    self.reply(200, {"id": server.submit(params)})
                except (ValueError, json.JSONDecodeError) as e:
                    self.reply(400, {"error": str(e)})

        threading.Thread(target=self.worker, daemon=True).start()
        print(f"engine listening on :{self.port}, GPU card {CARD}", flush=True)
        ThreadingHTTPServer(("0.0.0.0", self.port), Handler).serve_forever()
