"""One worker thread, one job at a time: convert, transcribe, summarize.

Job kinds: process (all steps), transcribe (redo with another engine, then
summarize), summarize (new recorder summary only). A job that was running when
the service stopped is queued again on start; finished steps are skipped."""
import threading
import time
import traceback

from . import audio, speakers, vault
from .summarize import summarize
from .util import now_iso, read_text, write_text


class MeetingLog:
    """The job's log with the meeting, encrypted like its other files: each
    line rewrites it (a job writes a few dozen)."""

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg):
        line = f"{now_iso()} {msg}"
        print(line, flush=True)
        old = read_text(self.path) if self.path.exists() else ""
        write_text(self.path, old + line + "\n")


class Worker:
    def __init__(self, settings, db, store, engines, followup=None):
        self.s = settings
        self.db = db
        self.store = store
        self.engines = engines
        self.followup = followup
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.current = None

    def start(self):
        self.db.run("UPDATE jobs SET state = 'queued', started_at = NULL WHERE state = 'running'")
        self.thread = threading.Thread(target=self._loop, name="worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wake.set()

    def enqueue(self, mid, kind="process", engine=None):
        self.db.run("INSERT INTO jobs (meeting_id, kind, engine, created_at) VALUES (?, ?, ?, ?)",
                    mid, kind, engine, now_iso())
        self.store.set_state(mid, "queued", error=None)
        self.wake.set()

    def queue(self):
        return self.db.all("SELECT * FROM jobs WHERE state IN ('queued', 'running') ORDER BY id")

    def _claim(self):
        with self.db.tx() as con:
            row = con.execute("SELECT * FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            con.execute("UPDATE jobs SET state = 'running', started_at = ? WHERE id = ?", (now_iso(), row["id"]))
            return dict(row)

    def _loop(self):
        while not self.stopping.is_set():
            job = self._claim()
            if not job:
                self.wake.wait(5)
                self.wake.clear()
                continue
            self.current = job
            try:
                self.run_job(job)
            finally:
                self.current = None

    def run_job(self, job):
        mid = job["meeting_id"]
        folder = self.store.folder(mid)
        log = MeetingLog(folder / "engine" / "log.txt")
        log(f"job {job['id']} {job['kind']} engine={job['engine'] or self.s.engine}")
        try:
            transcribed = False
            if job["kind"] in ("process", "transcribe"):
                self._convert(mid, log)
                transcribed = self._transcribe(mid, job["engine"] or self.s.engine, job["kind"] == "transcribe", log)
            if transcribed or job["kind"] == "summarize" or not self.store.summary(mid):
                self._summarize(mid, log)
            self.store.set_state(mid, "done", error=None)
            self.db.run("UPDATE jobs SET state = 'done', finished_at = ? WHERE id = ?", now_iso(), job["id"])
            log("done")
        except Exception as e:
            log("FAILED: " + "".join(traceback.format_exception(e)).strip())
            self.store.set_state(mid, "failed", error=str(e)[:500])
            self.db.run("UPDATE jobs SET state = 'failed', finished_at = ?, error = ? WHERE id = ?",
                        now_iso(), str(e)[:500], job["id"])
            return
        if self.followup and self.s.followup_on_done:
            try:
                res = self.followup.run(meeting_ids=[mid])
                log(f"follow-up: {len(res.get('proposed', []))} proposals, {len(res.get('executed', []))} carried out or queued")
            except Exception as e:
                log(f"follow-up failed (the hourly run retries): {e}")

    def _convert(self, mid, log):
        wav = self.store.audio(mid)
        if wav.exists():
            return
        src = self.store.original(mid)
        if not src:
            raise RuntimeError("no audio for this meeting")
        self.store.set_state(mid, "converting")
        t0 = time.monotonic()
        v = vault.VAULT
        # ffmpeg reads and writes plain files: in the scratch space (RAM), never on disk
        with v.scratch(mid) as tmp:
            plain = tmp / src.name
            v.export(src, plain)
            out = tmp / "audio.wav"
            audio.to_wav16k(plain, out)
            duration = audio.wav_duration(out)
            v.take(out, wav)
        self.store.set_state(mid, "converting", duration_s=duration)
        log(f"converted {src.name} -> audio.wav ({duration:.1f} s audio, {time.monotonic() - t0:.1f} s)")

    def _transcribe(self, mid, engine_name, force, log):
        if self.store.transcript(mid) and not force:
            return False
        engine = self.engines.get(engine_name)
        self.store.set_state(mid, "transcribing", engine=engine.name)
        meta = self.store.meta(mid)
        t0 = time.monotonic()
        v = vault.VAULT
        # the engines read the audio and write their output in the scratch space they share
        # with the recorder (RAM); their files then join the meeting, encrypted
        with v.scratch(mid) as tmp:
            wav = tmp / "audio.wav"
            v.export(self.store.audio(mid), wav)
            ref = self.store.audio(mid).with_name("audio.wav.ref.json")     # the fake engine's script, in demos
            if ref.exists():
                v.export(ref, tmp / ref.name)
            workdir = tmp / "engine" / engine_name
            workdir.mkdir(parents=True)
            transcript = engine.transcribe(wav, workdir, language=meta.get("language"), log=log)
            v.take_tree(workdir, self.store.folder(mid) / "engine" / engine_name)
        transcript["meeting_id"] = mid
        transcript.setdefault("timings", {})["job_wall_s"] = round(time.monotonic() - t0, 2)
        self.store.save_transcript(mid, transcript)
        log(f"transcribed with {engine_name}: {len(transcript['words'])} words, "
            f"{len(transcript['speakers'])} speakers, {time.monotonic() - t0:.1f} s")
        return True

    def _summarize(self, mid, log):
        transcript = self.store.transcript(mid)
        self.store.set_state(mid, "summarizing")
        t0 = time.monotonic()
        summary = summarize(transcript, self.s, log=log)
        version = self.store.add_summary(mid, summary, author="recorder")
        # names the transcript itself gives (introductions, people addressed by name): suggestions only
        named = {x.get("label"): {"name": x.get("name"), "why": x.get("evidence")}
                 for x in summary.get("speakers") or [] if (x.get("name") or "").strip()
                 and speakers.plausible(x.get("label"), x.get("name"), x.get("evidence"))}
        if named:
            speakers.suggest(self.store.folder(mid), set(self.store.names(mid)), named, by="recorder")   # names only
        log(f"summary v{version} ({self.s.llm_model}, {time.monotonic() - t0:.1f} s)")
