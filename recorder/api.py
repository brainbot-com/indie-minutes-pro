"""HTTP service: chunked upload API for phones, JSON API for agents, web view, PWA.

Caddy terminates TLS on 10.1.9.7:8443 and lets in VPN clients only; this app
listens on the host's loopback (published 127.0.0.1:18000) and on the internal
Docker network, never on the LAN."""
import contextlib
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

import segno
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from . import __version__, audio
from .config import Settings
from .db import DB
from .engines import NAMES as ENGINE_NAMES, Engines
from .followup import Followup
from .render import display_name, local_time, render_summary_md
from .store import Store
from .util import (fmt_duration, fmt_ts, hash_token, new_id, new_meeting_id, new_token, now_iso,
                   parse_iso, sha256_hex, write_bytes)
from .worker import Worker

HERE = Path(__file__).parent
PWA_DIR = HERE.parent / "pwa"
COOKIE = "ir_token"
READERS = ("device", "admin")
WRITERS = ("agent", "admin")
FILES = {"transcript.json", "transcript.md", "summary.md", "summary.json", "actions.json",
         "speakers.json", "meta.json"}


class NewRecording(BaseModel):
    client_id: str = Field(min_length=8, max_length=80)
    mime: str = Field(max_length=100)
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=8)
    started_at: str | None = Field(default=None, max_length=40)


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


class AckIn(BaseModel):
    url: str = Field(max_length=2000)
    note: str | None = Field(default=None, max_length=2000)


def create_app(settings=None):
    s = settings or Settings()
    s.ensure_dirs()
    db = DB(s.db_path)
    store = Store(s, db)
    engines = Engines(s)
    followup = Followup(s, db, store)
    worker = Worker(s, db, store, engines, followup)
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(fmt_ts=fmt_ts, fmt_duration=fmt_duration, version=__version__,
                                 local_time=lambda iso: local_time(iso, s.display_tz))
    templates.env.filters["md"] = lambda text: md.render(text or "")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if s.worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="indie-recorder", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.state.settings, app.state.db, app.state.store = s, db, store
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

    def page(request, name, tok, **ctx):
        return templates.TemplateResponse(request, name, {"tok": tok, "s": s, **ctx})

    def meeting_or_404(mid):
        m = store.meta(mid)
        if not m:
            raise HTTPException(404, "no such meeting")
        return m

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
        row = db.one("SELECT * FROM recordings WHERE token_id = ? AND client_id = ?", tok["id"], body.client_id)
        if not row:
            db.run("INSERT OR IGNORE INTO recordings (id, token_id, client_id, mime, title, language, started_at, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   new_id("rec"), tok["id"], body.client_id, body.mime, body.title, body.language,
                   body.started_at or now_iso(), now_iso())
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
        try:
            started = parse_iso(row["started_at"])
            mid = new_meeting_id(started)
            store.create(mid, title=body.title or row["title"], started_at=row["started_at"] or now_iso(),
                         source=f"device:{tok['name']}", recording_id=rid, language=row["language"])
            dst = store.folder(mid) / f"original.{audio.ext_for_mime(row['mime'])}"
            tmp = dst.with_name(".tmp-" + dst.name)
            with open(tmp, "wb") as out:
                for i in range(body.total_chunks):
                    out.write((s.uploads_dir / rid / f"{i:06d}.part").read_bytes())
            tmp.replace(dst)
        except Exception:
            db.run("UPDATE recordings SET state = 'uploading' WHERE id = ?", rid)
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
                      tok=Depends(need("device", "agent", "admin"))):
        rows = store.list(limit=1000)
        if since:
            rows = [m for m in rows if (m["updated_at"] or m["created_at"]) > since]
        if state:
            rows = [m for m in rows if m["state"] == state]
        return [{**m, "url": store.link(m["id"])} for m in rows]

    @app.get("/api/v1/meetings/{mid}")
    def get_meeting(mid: str, transcript: bool = False, tok=Depends(need("device", "agent", "admin"))):
        return meeting_json(meeting_or_404(mid), transcript=transcript)

    @app.get("/api/v1/meetings/{mid}/transcript")
    def get_transcript(mid: str, format: str = "json", tok=Depends(need("device", "agent", "admin"))):
        meeting_or_404(mid)
        if format == "md":
            return PlainTextResponse(_read(store.folder(mid) / "transcript.md"), media_type="text/markdown")
        t = store.transcript(mid)
        if not t:
            raise HTTPException(404, "no transcript yet")
        names = store.names(mid)
        return {**t, "speaker_names": names}

    @app.put("/api/v1/meetings/{mid}/speakers")
    def put_speakers(mid: str, body: SpeakersIn, tok=Depends(need(*READERS))):
        meeting_or_404(mid)
        return {"names": store.set_names(mid, body.names)}

    @app.post("/api/v1/meetings/{mid}/summary")
    def post_summary(mid: str, body: SummaryIn, tok=Depends(need(*WRITERS))):
        m = meeting_or_404(mid)
        if not (body.markdown or body.tldr):
            raise HTTPException(400, "send markdown or tldr")
        current = store.summary(mid) or {}
        summary = {k: v for k, v in body.model_dump().items() if v is not None and k != "author"}
        summary.setdefault("language", current.get("language") or m["language"])
        version = store.add_summary(mid, summary, author=f"{body.author} ({tok['name']})")
        return {"meeting_id": mid, "version": version, "url": store.link(mid)}

    @app.post("/api/v1/meetings/{mid}/reprocess")
    def reprocess(mid: str, body: ReprocessIn, tok=Depends(need(*READERS))):
        meeting_or_404(mid)
        if body.engine and body.engine not in ENGINE_NAMES:
            raise HTTPException(400, f"engine must be one of {ENGINE_NAMES}")
        worker.enqueue(mid, kind=body.kind, engine=body.engine)
        return {"queued": True}

    @app.get("/api/v1/jobs")
    def jobs(tok=Depends(need("device", "agent", "admin"))):
        return {"current": worker.current, "queue": worker.queue()}

    @app.get("/api/v1/health")
    def health():
        alive = bool(worker.thread and worker.thread.is_alive())
        return {"ok": True, "version": __version__, "worker": alive, "engine": s.engine, "llm": s.llm_model}

    # -- follow-up proposals and the Drive outbox ------------------------------

    @app.get("/api/v1/proposals")
    def list_proposals(state: str | None = None, tok=Depends(need("device", "agent", "admin"))):
        return followup.list(state=state)

    @app.post("/api/v1/proposals/{pid}/{action}")
    def decide(pid: str, action: str, tok=Depends(need(*READERS))):
        if action not in ("approve", "reject"):
            raise HTTPException(404)
        return followup.decide(pid, approve=action == "approve", who=tok["name"])

    @app.get("/api/v1/outbox")
    def outbox(tok=Depends(need(*WRITERS))):
        return followup.outbox()

    @app.post("/api/v1/outbox/{pid}/ack")
    def ack(pid: str, body: AckIn, tok=Depends(need(*WRITERS))):
        return followup.ack(pid, body.url, body.note, who=tok["name"])

    # -- web view ---------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return page(request, "login.html", None, next=next, error=None)

    @app.post("/login")
    def login(request: Request, token: str = Form(...), next: str = Form("/")):
        row = db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL", hash_token(token.strip()))
        if not row or row["role"] not in READERS:
            return page(request, "login.html", None, next=next, error="Unknown or revoked token.")
        resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
        resp.set_cookie(COOKIE, token.strip(), httponly=True, secure=s.secure_cookies,
                        samesite="strict", max_age=180 * 86400)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, tok=Depends(need(*READERS, html=True))):
        return page(request, "meetings.html", tok, meetings=store.list(), jobs=worker.queue())

    @app.get("/m/{mid}", response_class=HTMLResponse)
    def meeting_page(request: Request, mid: str, v: int | None = None, tok=Depends(need(*READERS, html=True))):
        m = meeting_or_404(mid)
        versions = store.summary_versions(mid)
        shown = next((x for x in versions if x["version"] == v), None) if v else None
        summary_md = _read(store.folder(mid) / "summary.md")
        if shown:
            summary_md = render_summary_md(shown, store.names(mid), m, s.display_tz, store.link(mid))
        t = store.transcript(mid) or {}
        names = store.names(mid)
        segs = [{**seg, "name": display_name(seg["spk"], names)} for seg in t.get("segments", [])]
        proposals = {p["item_key"]: p for p in followup.list(meeting_id=mid)}
        mappings = {r["item_key"]: r for r in db.all("SELECT * FROM mappings WHERE meeting_id = ?", mid)}
        return page(request, "meeting.html", tok, m=m, t=t, segs=segs, names=names, summary_md=summary_md,
                    versions=versions, shown=shown, actions=store.actions(mid).get("items", []),
                    proposals=proposals, mappings=mappings, engines=ENGINE_NAMES,
                    has_audio=store.audio(mid).exists() or bool(store.original(mid)))

    @app.post("/m/{mid}/speakers")
    async def speakers_form(request: Request, mid: str, tok=Depends(need(*READERS, html=True))):
        meeting_or_404(mid)
        form = await request.form()
        store.set_names(mid, {k[5:]: v for k, v in form.items() if k.startswith("name:")})
        return RedirectResponse(f"/m/{mid}#speakers", status_code=303)

    @app.post("/m/{mid}/reprocess")
    def reprocess_form(mid: str, kind: str = Form("transcribe"), engine: str = Form(""),
                       tok=Depends(need(*READERS, html=True))):
        meeting_or_404(mid)
        if kind not in ("transcribe", "summarize") or (engine and engine not in ENGINE_NAMES):
            raise HTTPException(400)
        worker.enqueue(mid, kind=kind, engine=engine or None)
        return RedirectResponse(f"/m/{mid}", status_code=303)

    @app.get("/m/{mid}/audio")
    def meeting_audio(mid: str, tok=Depends(need(*READERS, html=True))):
        meeting_or_404(mid)
        f = store.audio(mid) if store.audio(mid).exists() else store.original(mid)
        if not f:
            raise HTTPException(404)
        return FileResponse(f)

    @app.get("/m/{mid}/files/{name}")
    def meeting_file(mid: str, name: str, tok=Depends(need(*READERS, html=True))):
        meeting_or_404(mid)
        f = store.folder(mid) / name
        if name not in FILES or not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="text/markdown; charset=utf-8" if name.endswith(".md") else None)

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request, tok=Depends(need(*READERS, html=True))):
        items = followup.list()
        titles = {m["id"]: m["title"] or m["id"] for m in store.list(limit=1000)}
        return page(request, "review.html", tok, items=items, titles=titles, auto=s.followup_auto,
                    repo=s.github_repo, has_token=followup.github.available())

    @app.post("/review/run")
    def review_run(tok=Depends(need(*READERS, html=True))):
        threading.Thread(target=followup.run, kwargs={"auto": s.followup_auto}, daemon=True).start()
        return RedirectResponse("/review", status_code=303)

    @app.post("/review/{pid}/{action}")
    def review_decide(pid: str, action: str, tok=Depends(need(*READERS, html=True))):
        if action not in ("approve", "reject"):
            raise HTTPException(404)
        followup.decide(pid, approve=action == "approve", who=tok["name"])
        return RedirectResponse("/review", status_code=303)

    @app.get("/devices", response_class=HTMLResponse)
    def devices(request: Request, tok=Depends(need("admin", html=True))):
        rows = db.all("SELECT * FROM tokens ORDER BY created_at DESC")
        return page(request, "devices.html", tok, rows=rows, created=None)

    @app.post("/devices", response_class=HTMLResponse)
    def add_device(request: Request, name: str = Form(...), role: str = Form("device"),
                   tok=Depends(need("admin", html=True))):
        if role not in ("device", "agent"):
            raise HTTPException(400)
        raw = create_token(db, name.strip()[:80] or "device", role)
        pair = f"{s.public_url}/app/#pair={raw}"
        qr = segno.make(pair, error="m").svg_inline(scale=4, dark="#111", light="#fff") if role == "device" else None
        rows = db.all("SELECT * FROM tokens ORDER BY created_at DESC")
        return page(request, "devices.html", tok, rows=rows,
                    created={"name": name, "role": role, "token": raw, "pair": pair, "qr": qr})

    @app.post("/devices/{tid}/revoke")
    def revoke(tid: str, tok=Depends(need("admin", html=True))):
        db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", now_iso(), tid)
        return RedirectResponse("/devices", status_code=303)

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(PWA_DIR / "icon-192.png")

    from .mcp_server import mount_mcp
    mount_mcp(app, s, db, store, followup, token_row)
    return app


def create_token(db, name, role):
    raw = new_token()
    db.run("INSERT INTO tokens (id, name, role, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
           new_id("tok"), name, role, hash_token(raw), now_iso())
    return raw


def _read(path):
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return ""


def main():
    """`python -m recorder.api` runs the service (uvicorn)."""
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8000, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1,172.16.0.0/12", log_level="info")


if __name__ == "__main__":
    main()
