"""HTTP service: chunked upload API for phones, JSON API for agents, web view, PWA.

Caddy terminates TLS on 10.1.9.7:8443 and lets in VPN clients only; this app
listens on the host's loopback (published 127.0.0.1:18000) and on the internal
Docker network, never on the LAN."""
import base64
import contextlib
import mimetypes
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import segno
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from . import __version__, audio, speakers, vault
from .config import Settings
from .connect import CODE_MINUTES, Codes, Guesses, shown
from .db import open_index
from .engines import NAMES as ENGINE_NAMES, Engines
from .followup import Followup
from .gdrive import Google, GoogleError
from .people import label, resolve
from .render import display_name, local_time, render_summary_md, words_to_segments
from .store import Store
from .util import (fmt_clock, fmt_duration, fmt_ts, hash_token, new_id, new_meeting_id, new_token, now_iso,
                   parse_iso, read_bytes, read_text, sha256_hex, write_bytes)
from .worker import Worker

HERE = Path(__file__).parent
PWA_DIR = HERE.parent / "pwa"
mimetypes.add_type("font/woff2", ".woff2")   # the slim image does not know it
COOKIE = "ir_token"
READERS = ("device", "admin")
WRITERS = ("agent", "admin")
FILES = {"transcript.json", "transcript.md", "summary.md", "summary.json", "actions.json",
         "speakers.json", "meta.json"}


class SealIn(BaseModel):
    kid: str = Field(max_length=40)
    epk: str = Field(max_length=200)        # the phone's public half for this recording (P-256, b64url)


class NewRecording(BaseModel):
    client_id: str = Field(min_length=8, max_length=80)
    mime: str = Field(max_length=100)
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=8)
    started_at: str | None = Field(default=None, max_length=40)
    storage: str | None = Field(default=None, pattern="^(local|drive)$")
    seal: SealIn | None = None              # chunks encrypted on the phone for the box's sealing key


class SettingsIn(BaseModel):
    storage_default: str | None = Field(default=None, pattern="^(local|drive)$")
    drive_folder: str | None = Field(default=None, max_length=120)
    drive_include_audio: bool | None = None


class PairIn(BaseModel):
    code: str = Field(max_length=40)
    device: str = Field("", max_length=80)


class StorageIn(BaseModel):
    storage: str = Field(pattern="^(local|drive)$")


class Finalize(BaseModel):
    total_chunks: int = Field(ge=1, le=100_000)
    title: str | None = Field(default=None, max_length=200)


class SpeakersIn(BaseModel):
    names: dict[str, str]


class SummaryIn(BaseModel):
    """A new summary version from an agent: markdown, or the structured fields, or both."""
    author: str = Field(default="agent", max_length=80)
    markdown: str | None = Field(default=None, max_length=200_000)
    title: str | None = Field(default=None, max_length=200)
    tldr: str | None = None
    decisions: list[str] | None = None
    action_items: list[dict] | None = None
    open_questions: list[str] | None = None
    note: str | None = Field(default=None, max_length=2000)


class ReprocessIn(BaseModel):
    kind: str = Field(default="transcribe", pattern="^(transcribe|summarize)$")
    engine: str | None = None


class IssuesIn(BaseModel):
    repo: str = Field(max_length=200)
    issues: list[dict]


class AckIn(BaseModel):
    url: str = Field(max_length=2000)
    note: str | None = Field(default=None, max_length=2000)


def create_app(settings=None):
    s = settings or Settings()
    s.ensure_dirs()
    db = open_index(s)                       # also loads the keys (vault.configure)
    store = Store(s, db)
    engines = Engines(s)
    google = Google(s, db)
    followup = Followup(s, db, store, google)
    worker = Worker(s, db, store, engines, followup)
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(fmt_ts=fmt_ts, fmt_duration=fmt_duration, fmt_clock=fmt_clock, version=__version__,
                                 local_time=lambda iso: local_time(iso, s.display_tz))
    def page_md(text):
        # The meeting page shows title, date and Drive link itself: skip the header
        # lines render_summary_md writes (title, byline, link) and keep the rest.
        lines, i = (text or "").splitlines(), 0
        for is_header in (lambda ln: ln.startswith("# "), lambda ln: ln.startswith("_") and ln.endswith("_"),
                          lambda ln: ln.startswith(("https://", "http://"))):
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and is_header(lines[i]):
                i += 1
        return md.render("\n".join(lines[i:]))
    templates.env.filters["md"] = page_md

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if s.worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="indie-recorder", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.state.settings, app.state.db, app.state.store = s, db, store
    app.state.google = google
    app.state.worker, app.state.followup, app.state.engines = worker, followup, engines
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.mount("/app", StaticFiles(directory=PWA_DIR, html=True), name="app")

    # -- auth: per-device and per-agent tokens, stored hashed ----------------

    def token_row(request):
        raw = ""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            raw = auth[7:].strip()
        raw = raw or request.cookies.get(COOKIE, "")
        if not raw:
            return None
        row = db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL", hash_token(raw))
        if row and (row["last_used_at"] or "")[:16] != now_iso()[:16]:
            db.run("UPDATE tokens SET last_used_at = ? WHERE id = ?", now_iso(), row["id"])
        return row

    def need(*roles, html=False):
        def dep(request: Request):
            row = token_row(request)
            if row and row["role"] in roles:
                return row
            if html:
                raise HTTPException(303, headers={"Location": "/login?next=" + quote(str(request.url.path))})
            raise HTTPException(401 if not row else 403, "valid token required" if not row else "not allowed")
        return dep

    def person(*roles, html=False):
        """Like need(), but answers who the request speaks for (people.py):
        everything below shows a caller only their own meetings."""
        def dep(request: Request):
            row = token_row(request)
            who = resolve(db, row, request.headers) if row and row["role"] in roles else None
            if who:
                return who
            if html:
                raise HTTPException(303, headers={"Location": "/login?next=" + quote(str(request.url.path))})
            raise HTTPException(401 if not row else 403, "valid token required" if not row else "not allowed")
        return dep

    def is_agent(who):
        """An agent, not a person: one of brainbot:agent command's (through the
        connector) or one with an agent token of its own."""
        if who.token.get("trusts_identity"):
            return who.caller != "user"
        return who.token["role"] == "agent"

    def page(request, name, who, **ctx):
        tok = who.token if hasattr(who, "token") else who
        return templates.TemplateResponse(request, name, {"tok": tok, "who": who, "s": s, **ctx})

    def meeting_or_404(mid, who=None):
        m = store.meta(mid)
        if not m or (who is not None and not who.sees(m)):
            raise HTTPException(404, "no such meeting")
        return m

    def visible_proposals(who, **kw):
        return [p for p in followup.list(**kw) if who.sees(store.meta(p["meeting_id"]))]

    # -- upload API ------------------------------------------------------------

    def recording_status(row):
        received = [r["idx"] for r in db.all("SELECT idx FROM chunks WHERE recording_id = ? ORDER BY idx", row["id"])]
        m = store.meta(row["meeting_id"]) if row["meeting_id"] else None
        return {"recording_id": row["id"], "client_id": row["client_id"], "state": row["state"],
                "chunk_seconds": s.chunk_seconds, "received": received,
                "meeting_id": row["meeting_id"], "meeting_state": m["state"] if m else None,
                "meeting_url": store.link(m["id"]) if m else None}

    def own_recording(rid, tok):
        row = db.one("SELECT * FROM recordings WHERE id = ? AND token_id = ?", rid, tok["id"])
        if not row:
            raise HTTPException(404, "no such recording for this device")
        return row

    @app.post("/api/v1/recordings")
    def create_recording(body: NewRecording, tok=Depends(need(*READERS))):
        if body.seal and (not vault.SEALER or body.seal.kid != vault.SEALER.kid):
            raise HTTPException(409, "the box's sealing key is another one: fetch it again from /api/v1/health")
        row = db.one("SELECT * FROM recordings WHERE token_id = ? AND client_id = ?", tok["id"], body.client_id)
        if not row:
            db.run("INSERT OR IGNORE INTO recordings (id, token_id, client_id, mime, title, language, started_at,"
                   " created_at, storage, seal_kid, seal_epk) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   new_id("rec"), tok["id"], body.client_id, body.mime, body.title, body.language,
                   body.started_at or now_iso(), now_iso(), body.storage,
                   body.seal.kid if body.seal else None, body.seal.epk if body.seal else None)
            row = db.one("SELECT * FROM recordings WHERE token_id = ? AND client_id = ?", tok["id"], body.client_id)
        (s.uploads_dir / row["id"]).mkdir(parents=True, exist_ok=True)
        return recording_status(row)

    @app.get("/api/v1/recordings/{rid}")
    def get_recording(rid: str, tok=Depends(need(*READERS))):
        return recording_status(own_recording(rid, tok))

    @app.put("/api/v1/recordings/{rid}/chunks/{idx}")
    async def put_chunk(rid: str, idx: int, request: Request, tok=Depends(need(*READERS))):
        row = own_recording(rid, tok)
        if not 0 <= idx < 100_000:
            raise HTTPException(400, "bad chunk index")
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty chunk")
        if len(data) > s.max_chunk_bytes:
            raise HTTPException(413, "chunk too large")
        digest = sha256_hex(data)
        claimed = request.headers.get("x-chunk-sha256", "").lower()
        if claimed and claimed != digest:
            raise HTTPException(422, "checksum mismatch, send the chunk again")
        have = db.one("SELECT sha256 FROM chunks WHERE recording_id = ? AND idx = ?", rid, idx)
        if have and have["sha256"] == digest:
            return {"ok": True, "idx": idx, "duplicate": True}
        if row["state"] != "uploading":
            raise HTTPException(409, "recording is already finalized")
        write_bytes(s.uploads_dir / rid / f"{idx:06d}.part", data)
        db.run("INSERT INTO chunks (recording_id, idx, size, sha256, received_at) VALUES (?, ?, ?, ?, ?)"
               " ON CONFLICT (recording_id, idx) DO UPDATE SET size = excluded.size,"
               " sha256 = excluded.sha256, received_at = excluded.received_at",
               rid, idx, len(data), digest, now_iso())
        return {"ok": True, "idx": idx, "size": len(data), "sha256": digest}

    @app.post("/api/v1/recordings/{rid}/finalize")
    def finalize(rid: str, body: Finalize, tok=Depends(need(*READERS))):
        row = own_recording(rid, tok)
        if row["state"] == "finalized":
            return recording_status(row)
        have = {r["idx"] for r in db.all("SELECT idx FROM chunks WHERE recording_id = ?", rid)}
        missing = [i for i in range(body.total_chunks) if i not in have]
        if missing:
            return JSONResponse({"error": "missing chunks", "missing": missing[:500]}, status_code=409)
        if db.run("UPDATE recordings SET state = 'finalizing' WHERE id = ? AND state = 'uploading'", rid) != 1:
            raise HTTPException(409, "finalize already in progress")
        mid = None
        try:
            started = parse_iso(row["started_at"])
            mid = new_meeting_id(started)
            store.create(mid, title=body.title or row["title"], started_at=row["started_at"] or now_iso(),
                         source=f"device:{tok['name']}", recording_id=rid, language=row["language"],
                         storage=row["storage"], owner_id=tok.get("user_id"))
            dst = store.folder(mid) / f"original.{audio.ext_for_mime(row['mime'])}"
            # sealed on the phone: opened here with the box's sealing key; stored encrypted at rest
            key = vault.SEALER.chunk_key(row["seal_epk"], row["client_id"]) if row["seal_epk"] else None
            with vault.VAULT.writer(dst) as out:
                for i in range(body.total_chunks):
                    data = read_bytes(s.uploads_dir / rid / f"{i:06d}.part")
                    out.write(vault.open_chunk(key, row["client_id"], i, data) if key else data)
        except Exception as e:
            db.run("UPDATE recordings SET state = 'uploading' WHERE id = ?", rid)
            if mid and store.meta(mid):
                store.delete(mid)               # no half-made meeting; the phone sends again and finalizes later
            if isinstance(e, vault.VaultError):
                raise HTTPException(422, str(e)) from e
            raise
        db.run("UPDATE recordings SET state = 'finalized', total_chunks = ?, meeting_id = ? WHERE id = ?",
               body.total_chunks, mid, rid)
        shutil.rmtree(s.uploads_dir / rid, ignore_errors=True)
        worker.enqueue(mid)
        return recording_status(db.one("SELECT * FROM recordings WHERE id = ?", rid))

    # -- meetings API (web view, PWA, agents) ----------------------------------

    def meeting_json(m, transcript=False):
        mid = m["id"]
        out = {**m, "url": store.link(mid), "speaker_names": store.names(mid),
               "summary": store.summary(mid), "summary_md": _read(store.folder(mid) / "summary.md"),
               "actions": store.actions(mid).get("items", []),
               "summary_versions": [{k: v.get(k) for k in ("version", "author", "created_at", "note")}
                                    for v in store.summary_versions(mid)]}
        if transcript:
            out["transcript_md"] = _read(store.folder(mid) / "transcript.md")
        return out

    @app.get("/api/v1/meetings")
    def list_meetings(since: str | None = None, state: str | None = None,
                      who=Depends(person("device", "agent", "admin"))):
        rows = store.list(limit=1000, who=who)
        if since:
            rows = [m for m in rows if (m["updated_at"] or m["created_at"]) > since]
        if state:
            rows = [m for m in rows if m["state"] == state]
        return [{**m, "url": store.link(m["id"])} for m in rows]

    @app.get("/api/v1/meetings/{mid}")
    def get_meeting(mid: str, transcript: bool = False, who=Depends(person("device", "agent", "admin"))):
        return meeting_json(meeting_or_404(mid, who), transcript=transcript)

    @app.get("/api/v1/meetings/{mid}/transcript")
    def get_transcript(mid: str, format: str = "json", who=Depends(person("device", "agent", "admin"))):
        meeting_or_404(mid, who)
        if format == "md":
            return PlainTextResponse(_read(store.folder(mid) / "transcript.md"), media_type="text/markdown")
        t = store.transcript(mid)
        if not t:
            raise HTTPException(404, "no transcript yet")
        names = store.names(mid)
        return {**t, "speaker_names": names}

    @app.put("/api/v1/meetings/{mid}/speakers")
    def put_speakers(mid: str, body: SpeakersIn, who=Depends(person(*READERS))):
        meeting_or_404(mid, who)
        if is_agent(who):
            raise HTTPException(403, "an agent suggests names (suggest_speakers); the person names speakers")
        return {"names": store.set_names(mid, body.names)}

    @app.post("/api/v1/meetings/{mid}/summary")
    def post_summary(mid: str, body: SummaryIn, who=Depends(person(*WRITERS))):
        m = meeting_or_404(mid, who)
        if not (body.markdown or body.tldr):
            raise HTTPException(400, "send markdown or tldr")
        current = store.summary(mid) or {}
        summary = {k: v for k, v in body.model_dump().items() if v is not None and k != "author"}
        summary.setdefault("language", current.get("language") or m["language"])
        version = store.add_summary(mid, summary, author=f"{body.author} ({who.name})")
        return {"meeting_id": mid, "version": version, "url": store.link(mid)}

    @app.post("/api/v1/meetings/{mid}/reprocess")
    def reprocess(mid: str, body: ReprocessIn, who=Depends(person(*READERS))):
        meeting_or_404(mid, who)
        if body.engine and body.engine not in ENGINE_NAMES:
            raise HTTPException(400, f"engine must be one of {ENGINE_NAMES}")
        worker.enqueue(mid, kind=body.kind, engine=body.engine)
        return {"queued": True}

    @app.get("/api/v1/settings")
    def get_settings(who=Depends(person("device", "agent", "admin"))):
        return store.settings(who.user_id)

    @app.get("/api/v1/me")
    def me(who=Depends(person("device", "agent", "admin"))):
        """Who a token records for: the phone shows it as "Recording for ..."."""
        owner = db.one("SELECT * FROM users WHERE id = ?", who.user_id) if who.user_id else None
        return {"device": who.name, "role": who.token["role"], "person": (owner or {}).get("name") or (owner or {}).get("email")}

    @app.put("/api/v1/settings")
    def put_settings(body: SettingsIn, who=Depends(person("agent", "admin"))):
        try:
            return store.update_settings(**body.model_dump(), user_id=who.user_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/v1/meetings/{mid}/storage")
    def put_storage(mid: str, body: StorageIn, who=Depends(person("device", "agent", "admin"))):
        meeting_or_404(mid, who)
        change_storage(mid, body.storage)
        return {"meeting_id": mid, "storage": body.storage}

    def change_storage(mid, storage):
        store.set_storage(mid, storage)
        if storage == "drive" and store.meta(mid)["state"] == "done":
            threading.Thread(target=followup.run, kwargs={"meeting_ids": [mid]}, daemon=True).start()

    @app.get("/api/v1/files/{token:path}")
    def signed_file(token: str, request: Request):
        """A file of a meeting, by a signed link from drive_files (no other auth)."""
        from .signing import verify
        found = verify(s, token)
        if not found:
            raise HTTPException(403, "link expired or invalid")
        mid, name = found
        if name.startswith("clip:"):            # seven seconds of one speaker's voice, to put a name to it
            card = speakers.profile(store.transcript(mid) or {}).get(name[5:])
            if not card or not card["clip"] or not store.audio(mid).exists():
                raise HTTPException(404)
            wav = speakers.clip_wav(store.audio(mid), card["clip"]["s"], card["clip"]["e"])
            got = span(request, len(wav))
            if got is None:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{len(wav)}"})
            start, end, partial = got
            headers = {"Cache-Control": "private, max-age=600", "Accept-Ranges": "bytes",
                       **({"Content-Range": f"bytes {start}-{end - 1}/{len(wav)}"} if partial else {})}
            return Response(wav[start:end], status_code=206 if partial else 200, media_type="audio/wav", headers=headers)
        files = dict(store.drive_files(mid))
        if name not in files:
            raise HTTPException(404)
        return plain_file(files[name], filename=name)

    @app.get("/api/v1/jobs")
    def jobs(tok=Depends(need("device", "agent", "admin"))):
        return {"current": worker.current, "queue": worker.queue()}

    @app.get("/api/v1/health")
    def health():
        alive = bool(worker.thread and worker.thread.is_alive())
        return {"ok": True, "version": __version__, "worker": alive, "engine": s.engine, "llm": s.llm_model, "box": s.box_name,
                # phones encrypt their chunks for this key before they store or send them
                "seal": vault.SEALER.describe() if vault.SEALER else None}

    # -- follow-up proposals and the Drive outbox ------------------------------

    @app.get("/api/v1/proposals")
    def list_proposals(state: str | None = None, who=Depends(person("device", "agent", "admin"))):
        return visible_proposals(who, state=state)

    def own_proposal(pid, who):
        row = db.one("SELECT meeting_id FROM proposals WHERE id = ?", pid)
        if not row or not who.sees(store.meta(row["meeting_id"])):
            raise HTTPException(404, "no such proposal")

    @app.post("/api/v1/proposals/{pid}/{action}")
    def decide(pid: str, action: str, who=Depends(person(*READERS))):
        if action not in ("approve", "reject"):
            raise HTTPException(404)
        own_proposal(pid, who)
        return followup.decide(pid, approve=action == "approve", who=who.name)

    @app.get("/api/v1/outbox")
    def outbox(who=Depends(person(*WRITERS))):
        return [p for p in followup.outbox() if who.sees(store.meta(p["meeting_id"]))]

    @app.post("/api/v1/outbox/{pid}/ack")
    def ack(pid: str, body: AckIn, who=Depends(person(*WRITERS))):
        own_proposal(pid, who)
        return followup.ack(pid, body.url, body.note, who=who.name)

    @app.put("/api/v1/issues-index")
    def issues_index(body: IssuesIn, tok=Depends(need(*WRITERS))):
        return followup.sync_issues(body.repo, body.issues)

    # -- web view ---------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return page(request, "login.html", None, next=next, error=None)

    @app.post("/login")
    def login(request: Request, token: str = Form(...), next: str = Form("/")):
        row = db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL", hash_token(token.strip()))
        if not row or row["role"] not in READERS:
            return page(request, "login.html", None, next=next, error="Unknown or revoked token.")
        safe = next.startswith("/") and not next.startswith("//") and "\\" not in next
        resp = RedirectResponse(next if safe else "/", status_code=303)
        resp.set_cookie(COOKIE, token.strip(), httponly=True, secure=s.secure_cookies,
                        samesite="strict", max_age=180 * 86400)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, who=Depends(person(*READERS, html=True))):
        return page(request, "meetings.html", who, meetings=store.list(who=who), jobs=worker.queue())

    @app.get("/m/{mid}", response_class=HTMLResponse)
    def meeting_page(request: Request, mid: str, v: int | None = None, who=Depends(person(*READERS, html=True))):
        m = meeting_or_404(mid, who)
        versions = store.summary_versions(mid)
        shown = next((x for x in versions if x["version"] == v), None) if v else None
        summary_md = _read(store.folder(mid) / "summary.md")
        if shown:
            summary_md = render_summary_md(shown, store.names(mid), m, s.display_tz, store.link(mid))
        t = store.transcript(mid) or {}
        names = store.names(mid)
        segs = [{**seg, "name": display_name(seg["spk"], names)}
                for seg in words_to_segments(t.get("words", [])) or t.get("segments", [])]
        proposals = {p["item_key"]: p for p in followup.list(meeting_id=mid)}
        mappings = {r["item_key"]: r for r in db.all("SELECT * FROM mappings WHERE meeting_id = ?", mid)}
        return page(request, "meeting.html", who, m=m, t=t, segs=segs, names=names, summary_md=summary_md,
                    versions=versions, shown=shown, actions=store.actions(mid).get("items", []),
                    proposals=proposals, mappings=mappings, engines=[e for e in ENGINE_NAMES if e != "fake"],
                    has_audio=store.audio(mid).exists() or bool(store.original(mid)))

    @app.post("/m/{mid}/speakers")
    async def speakers_form(request: Request, mid: str, who=Depends(person(*READERS, html=True))):
        meeting_or_404(mid, who)
        form = await request.form()
        store.set_names(mid, {k[5:]: v for k, v in form.items() if k.startswith("name:")})
        return RedirectResponse(f"/m/{mid}#speakers", status_code=303)

    @app.post("/m/{mid}/storage")
    def storage_form(mid: str, storage: str = Form(...), who=Depends(person(*READERS, html=True))):
        meeting_or_404(mid, who)
        if storage not in ("local", "drive"):
            raise HTTPException(400)
        change_storage(mid, storage)
        return RedirectResponse(f"/m/{mid}", status_code=303)

    @app.post("/m/{mid}/reprocess")
    def reprocess_form(mid: str, kind: str = Form("transcribe"), engine: str = Form(""),
                       who=Depends(person(*READERS, html=True))):
        meeting_or_404(mid, who)
        if kind not in ("transcribe", "summarize") or (engine and engine not in ENGINE_NAMES):
            raise HTTPException(400)
        worker.enqueue(mid, kind=kind, engine=engine or None)
        return RedirectResponse(f"/m/{mid}", status_code=303)

    def span(request, size):
        """The bytes a request asks for (its first range): (start, end, partial),
        or None when none of them exist. Audio players ask for ranges, and Safari
        plays nothing from a server that does not answer them."""
        rng = request.headers.get("range", "") if request else ""
        if not rng.startswith("bytes="):
            return 0, size, False
        a, _, b = rng[6:].split(",")[0].strip().partition("-")
        try:
            start, end = (int(a), int(b) + 1 if b else size) if a else (max(0, size - int(b)), size)
        except ValueError:
            return 0, size, False
        end = min(end, size)
        return (start, end, True) if start < end else None

    def plain_file(path, request=None, media_type=None, filename=None):
        """A meeting's file, decrypted on the way out, with byte ranges for the audio player."""
        v = vault.VAULT
        size = v.size(path)
        got = span(request, size)
        if got is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start, end, partial = got
        status = 206 if partial else 200
        headers = {"Accept-Ranges": "bytes"}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
        headers["Content-Length"] = str(end - start)
        if filename:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        media_type = media_type or mimetypes.guess_type(str(filename or path))[0] or "application/octet-stream"
        return StreamingResponse(v.stream(path, start, end), status_code=status, media_type=media_type, headers=headers)

    @app.get("/m/{mid}/audio")
    def meeting_audio(mid: str, request: Request, who=Depends(person(*READERS, html=True))):
        meeting_or_404(mid, who)
        f = store.audio(mid) if store.audio(mid).exists() else store.original(mid)
        if not f:
            raise HTTPException(404)
        return plain_file(f, request)

    @app.get("/m/{mid}/files/{name}")
    def meeting_file(mid: str, name: str, who=Depends(person(*READERS, html=True))):
        meeting_or_404(mid, who)
        f = store.folder(mid) / name
        if name not in FILES or not f.exists():
            raise HTTPException(404)
        return plain_file(f, media_type="text/markdown; charset=utf-8" if name.endswith(".md") else None)

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, who=Depends(person(*READERS, html=True))):
        items = visible_proposals(who)
        titles = {m["id"]: m["title"] or m["id"] for m in store.list(limit=1000, who=who)}
        return page(request, "review.html", who, items=items, titles=titles, auto=s.followup_auto,
                    repo=s.github_repo, executor=s.followup_executor, targets=sorted(s.followup_targets),
                    carrier=("mock GitHub and Drive on this box" if s.followup_executor == "direct"
                             else "the Agent Command Center agent (outbox)"))

    @app.post("/review/run")
    def review_run(tok=Depends(need(*READERS, html=True))):
        threading.Thread(target=followup.run, kwargs={"auto": s.followup_auto}, daemon=True).start()
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/{pid}/{action}")
    def review_decide(pid: str, action: str, who=Depends(person(*READERS, html=True))):
        if action not in ("approve", "reject"):
            raise HTTPException(404)
        own_proposal(pid, who)
        followup.decide(pid, approve=action == "approve", who=who.name)
        return RedirectResponse("/review", status_code=303)

    def token_rows():
        users = {u["id"]: u for u in db.all("SELECT * FROM users")}
        return [{**r, "person": label(users.get(r["user_id"])) if r["user_id"] else ""}
                for r in db.all("SELECT * FROM tokens ORDER BY created_at DESC")], list(users.values())

    @app.get("/devices", response_class=HTMLResponse)
    def devices(request: Request, tok=Depends(need("admin", html=True))):
        rows, users = token_rows()
        return page(request, "devices.html", tok, rows=rows, users=users)

    @app.post("/devices", response_class=HTMLResponse)
    def add_agent(request: Request, name: str = Form(...), user_id: str = Form(""),
                  tok=Depends(need("admin", html=True))):
        """A token for an agent (phones connect with a code instead)."""
        if user_id and not db.one("SELECT 1 FROM users WHERE id = ?", user_id):
            raise HTTPException(400, "no such person")
        name = name.strip()[:80] or "agent"
        raw = create_token(db, name, "agent", user_id=user_id or None)
        rows, users = token_rows()
        return page(request, "devices.html", tok, rows=rows, users=users, created={"name": name, "token": raw})

    @app.post("/devices/connect", response_class=HTMLResponse)
    def connect_phone(request: Request, user_id: str = Form(""), tok=Depends(need("admin", html=True))):
        if user_id and not db.one("SELECT 1 FROM users WHERE id = ?", user_id):
            raise HTTPException(400, "no such person")
        rows, users = token_rows()
        return page(request, "devices.html", tok, rows=rows, users=users, offer=connect_offer(user_id or None),
                    offer_for=person_name(user_id) if user_id else None)

    codes, guesses = Codes(db), Guesses()

    def qr_svg(text):
        # a viewBox instead of a fixed size, so the page can scale it and it stays square
        return segno.make(text, error="m").svg_inline(scale=4, dark="#111", light="#fff", omitsize=True)

    def ca_der():
        """This box's CA certificate for the phone app to pin (public, not a secret)."""
        try:
            pem = s.ca_cert_file.read_text()
        except OSError:
            return None
        der = base64.b64decode("".join(ln for ln in pem.splitlines() if ln and not ln.startswith("-----")))
        return base64.urlsafe_b64encode(der).decode().rstrip("=")

    def connect_offer(user_id):
        """A connect code for a person's next phone: one link and its QR code."""
        code, expires = codes.create(user_id)
        url = f"{s.connect_url}#{code}"
        return {"code": shown(code), "url": url, "qr": qr_svg(url), "expires_at": expires, "minutes": CODE_MINUTES,
                "apk_url": s.apk_url if s.apk_file.exists() else None}

    def phone_token(name, user_id):
        raw = create_token(db, name, "device", user_id=user_id)
        return raw, db.one("SELECT id FROM tokens WHERE token_hash = ?", hash_token(raw))["id"]

    def person_name(user_id):
        owner = db.one("SELECT * FROM users WHERE id = ?", user_id) if user_id else None
        return (owner or {}).get("name") or (owner or {}).get("email")

    @app.get("/connect", response_class=HTMLResponse)
    def connect_page(request: Request):
        """Where a connect code leads (the code stays in the fragment, the server never sees it)."""
        return templates.TemplateResponse(request, "connect.html", {"s": s})

    # -- Google Drive: each person connects their own (gdrive.py) ---------------

    def google_page(request, ok, error=None, email=None, status_code=200):
        lang = "de" if request.headers.get("accept-language", "").lower().startswith("de") else "en"
        return templates.TemplateResponse(request, "google.html", {"s": s, "lang": lang, "ok": ok, "email": email,
                                          "error": error}, status_code=status_code)

    @app.get("/oauth/google/start")
    def google_start(request: Request, start: str = Query("", alias="s")):
        """Where the app's Connect Google Drive leads: on to Google's own sign-in."""
        try:
            return RedirectResponse(google.authorize_url(start), status_code=302)
        except GoogleError as e:
            return google_page(request, False, e, status_code=400)

    @app.get("/oauth/google/callback", response_class=HTMLResponse)
    def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        try:
            if error:
                raise GoogleError("denied" if error == "access_denied" else "google", error)
            done = google.finish(state, code)
        except GoogleError as e:
            return google_page(request, False, e, status_code=400)
        threading.Thread(target=followup.run, daemon=True).start()     # what waited for Drive goes now
        return google_page(request, True, email=done["email"])

    @app.get("/api/v1/pair/{code}")
    def pair_peek(code: str):
        """What a code would connect to, so the phone can ask before it spends it."""
        if guesses.blocked():
            raise HTTPException(429, "too many wrong codes, try again in a few minutes")
        row = codes.peek(code)
        if not row:
            guesses.miss()
            raise HTTPException(404, "unknown, used or expired code")
        return {"person": person_name(row["user_id"]), "box": s.box_name, "box_url": s.public_url, "ca": ca_der(),
                "expires_at": row["expires_at"], "webapp": s.webapp_url,
                "apk_url": s.apk_url if s.apk_file.exists() else None}

    @app.post("/api/v1/pair")
    def pair_redeem(body: PairIn):
        """Spends a code: the phone's own token, and where and how to reach the box."""
        if guesses.blocked():
            raise HTTPException(429, "too many wrong codes, try again in a few minutes")
        name = " ".join(body.device.split())[:60] or "Phone"
        raw, row = codes.redeem(body.code, phone_token, name)
        if not raw:
            guesses.miss()
            raise HTTPException(404, "unknown, used or expired code")
        return {"token": raw, "box_url": s.public_url, "ca": ca_der(), "person": person_name(row["user_id"]),
                "device": name, "box": s.box_name}

    @app.get("/indie-recorder.apk")
    def apk():
        if not s.apk_file.exists():
            raise HTTPException(404)
        return FileResponse(s.apk_file, media_type="application/vnd.android.package-archive", filename="indie-recorder.apk")

    @app.post("/devices/{tid}/revoke")
    def revoke(tid: str, tok=Depends(need("admin", html=True))):
        db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", now_iso(), tid)
        return RedirectResponse("/devices", status_code=303)

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(PWA_DIR / "icon-192.png")

    from .engines.remote import health as engine_health
    from .mcp_server import mount_mcp

    def engines_health():
        out = {}
        for name, url in (("A", s.nemo_url), ("B", s.qwen_url)):
            h = engine_health(url)
            out[name] = {"ok": bool(h.get("ok")), "busy": bool(h.get("current"))} if h else {"ok": False}
        return out
    mcp_call = mount_mcp(app, s, db, store, followup, token_row, worker=worker, engines_health=engines_health)

    # -- brainbot:agent command: its "indie-recorder" connector ------------------
    # One endpoint for the Meetings app and people's agents there. The service
    # token proves the call comes from brainbot:agent command, which names the
    # person in X-Indie-User and says whether a person or an agent asks
    # (X-Indie-Caller). Ops are the MCP tools, answered for that person, plus
    # the phone pairing below, which only a person may use.
    MAX_PHONES = 5

    @app.post("/api/v1/indiechat")
    async def indiechat(request: Request):
        row = token_row(request)
        if not row or not row.get("trusts_identity"):
            return JSONResponse({"ok": False, "error": "service token required"}, status_code=401)
        who = resolve(db, row, request.headers)
        if not who:
            return JSONResponse({"ok": False, "error": "no person named (X-Indie-User)"}, status_code=401)
        return await answer(request, who)

    # The phone apps: the same ops as the app in brainbot:agent command, for the
    # person the phone belongs to, so every app shows the same meetings the same
    # way. Not the agent's ops (pending work, Drive outbox, the skill) and not
    # pairing: a phone does not pair phones.
    PHONE_OPS = {"status", "whoami", "set_name", "list_meetings", "get_meeting", "rename_speakers", "set_storage",
                 "new_summary", "get_settings", "update_settings", "drive_connect", "drive_disconnect"}

    @app.post("/api/v1/app")
    async def phone_app(request: Request, who=Depends(person("device"))):
        if not who.user_id:
            return JSONResponse({"ok": False, "error": "this phone belongs to nobody: connect it again with a code"},
                                status_code=403)
        return await answer(request, who, PHONE_OPS)

    async def answer(request, who, allowed=None):
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("op"), str):
            return JSONResponse({"ok": False, "error": "send {op, args}"}, status_code=400)
        op, args = body["op"], body.get("args") if isinstance(body.get("args"), dict) else {}
        if allowed is not None and op not in allowed:
            return JSONResponse({"ok": False, "error": f"unknown op {op!r}"}, status_code=400)
        try:
            # off the event loop: an op may wait on an engine or on Google, and the
            # server must go on answering everyone else meanwhile
            return {"ok": True, "result": await run_in_threadpool(person_op, op, args, who)}
        except ToolError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    def person_op(op, args, who):
        if op in ("pair_phone", "phones", "remove_phone", "set_name", "drive_connect", "drive_disconnect") \
                and who.caller != "user":
            raise ToolError("only a person can pair or remove phones, set their name or connect Google Drive, not an agent")
        if op == "whoami":
            me = db.one("SELECT name FROM users WHERE id = ?", who.user_id)
            return {"meetings": len(store.list(limit=10000, who=who)), "caller": who.caller,
                    "phones": len(phones(who)), "name": me["name"]}
        if op == "set_name":
            # brainbot:agent command names people only by id; the person picks the name their phones show
            name = str(args.get("name") or "").strip()[:80] or None
            db.run("UPDATE users SET name = ? WHERE id = ?", name, who.user_id)
            return {"name": name}
        if op == "phones":
            return phones(who)
        if op == "drive_connect":
            try:
                return {"url": google.start_link(who.user_id)}
            except GoogleError:
                raise ToolError("this box has no Google sign-in set up")
        if op == "drive_disconnect":
            return google.disconnect(who.user_id)
        if op == "pair_phone":
            if len(phones(who)) >= MAX_PHONES:
                raise ToolError(f"at most {MAX_PHONES} phones: remove one first")
            return connect_offer(who.user_id)
        if op == "remove_phone":
            n = db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND role = 'device'"
                       " AND revoked_at IS NULL", now_iso(), str(args.get("id", "")), who.user_id)
            if not n:
                raise ToolError("no such phone")
            return {"removed": args.get("id")}
        return mcp_call(op, args, who)

    def phones(who):
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "last_used_at": r["last_used_at"]}
                for r in db.all("SELECT * FROM tokens WHERE user_id = ? AND role = 'device' AND revoked_at IS NULL"
                                " ORDER BY created_at", who.user_id)]

    from .mcp_server import ToolError
    return app


def create_token(db, name, role, user_id=None, trusts_identity=False):
    """A new token, stored hashed. trusts_identity: brainbot:agent command's service
    token, which speaks for the person its X-Indie-User header names (people.py)."""
    raw = new_token()
    db.run("INSERT INTO tokens (id, name, role, token_hash, created_at, user_id, trusts_identity)"
           " VALUES (?, ?, ?, ?, ?, ?, ?)",
           new_id("tok"), name, role, hash_token(raw), now_iso(), user_id, 1 if trusts_identity else 0)
    return raw


def _read(path):
    try:
        return read_text(path)
    except FileNotFoundError:
        return ""


def main():
    """`python -m recorder.api` runs the service (uvicorn)."""
    import faulthandler
    import signal
    import uvicorn
    # where a stuck server stands: `docker kill -s USR1 indie-recorder-recorder-1`
    # writes every thread's stack to the log (the host allows no debugger to attach)
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1,172.16.0.0/12", log_level="info")


if __name__ == "__main__":
    main()
