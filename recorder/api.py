"""HTTP service: chunked upload API for phones, JSON API for agents, web view, PWA.

A proxy in front terminates TLS: Caddy on 10.1.9.7:8443 for VPN clients only on
aibox1, the gateway on an Indie Box. This app never listens on the LAN itself."""
import base64
import contextlib
import hmac
import mimetypes
import os
import secrets
import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import segno
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field

from . import __version__, audio, passwords, speakers, urls, vault
from .config import Settings
from .connect import CODE_MINUTES, Codes, Guesses, shown
from .db import open_index
from .engines import NAMES as ENGINE_NAMES, Engines
from .followup import Followup
from .gdrive import Google, GoogleError
from .people import add_person, label, person_for, resolve
from .render import display_name, local_time, render_summary_md, words_to_segments
from .store import Store
from .util import (fmt_clock, fmt_duration, fmt_ts, hash_token, new_id, new_token, now_iso, parse_iso, read_bytes,
                   read_text, sha256_hex, utcnow, web_url, write_bytes)
from .worker import Worker

HERE = Path(__file__).parent
PWA_DIR = HERE.parent / "pwa"
# the Meetings app of brainbot:agent command, which is also the web app in a browser
WEB_APP = HERE.parent / "integrations" / "indie-chat" / "minutes-pro.html"
AGENT_SKILL = HERE.parent / "agents" / "meeting-follow-up" / "SKILL.md"
mimetypes.add_type("font/woff2", ".woff2")   # the slim image does not know it
COOKIE = "ir_token"
KEY_HEADER = "x-minutes-key"        # a signed-in browser's second half (start_session)
SESSION_DAYS = 30
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
    store = Store(s, db, drive_on=lambda: s.drive or google.available())
    engines = Engines(s)
    google = Google(s, db)
    followup = Followup(s, db, store, google)
    worker = Worker(s, db, store, engines, followup)
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(fmt_ts=fmt_ts, fmt_duration=fmt_duration, fmt_clock=fmt_clock, version=__version__,
                                 local_time=lambda iso: local_time(iso, s.display_tz))
    templates.env.filters["weburl"] = lambda u: web_url(u) or ""     # links from agents: http(s) only
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

    app = FastAPI(title="Minutes Pro", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.state.settings, app.state.db, app.state.store = s, db, store
    app.state.google = google
    app.state.worker, app.state.followup, app.state.engines = worker, followup, engines
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.mount("/app", StaticFiles(directory=PWA_DIR, html=True), name="app")
    app.add_middleware(Address)

    # The box's owner of the recorder, who signs in with a password (OWNER_PASSWORD,
    # from an Indie Box's install form). Without one, only tokens sign in.
    owner_id = None
    if s.owner_password:
        owner = person_for(db, "owner")
        if s.owner_name and not owner["name"]:
            db.run("UPDATE users SET name = ? WHERE id = ?", s.owner_name.strip()[:80], owner["id"])
        owner_id = owner["id"]
        # a new password (changed in the install form) ends what was signed in with the old one
        if not passwords.check(s.owner_password, db.setting("owner_password") or ""):
            db.run("DELETE FROM sessions WHERE user_id = ?", owner_id)
            db.set_setting("owner_password", passwords.make(s.owner_password))

    # -- auth: per-device and per-agent tokens, stored hashed ----------------

    def token_row(request):
        raw, cookie = "", False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            raw = auth[7:].strip()
        if not raw:
            raw, cookie = request.cookies.get(COOKIE, ""), True
            if raw and not cookie_counts(request):
                return None
        if not raw:
            return None
        row = db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL", hash_token(raw))
        if row and (row["last_used_at"] or "")[:16] != now_iso()[:16]:
            db.run("UPDATE tokens SET last_used_at = ? WHERE id = ?", now_iso(), row["id"])
        row = row or (session_row(raw, request) if cookie else None)
        if row:
            urls.remember(request)          # only a signed-in request says how people reach the box
        return row

    def cookie_counts(request):
        """The browser's cookie signs in pages, forms and the meetings app's endpoint,
        nothing else: the APIs take tokens in the Authorization header. A cookie goes to
        every port of a host name, so on an Indie Box the other apps' pages are not
        cross-site: a form or script there must not act with it (Origin check)."""
        path = request.url.path
        if path.startswith(("/api/", "/mcp")) and path != "/api/v1/web":
            return False
        if request.method in ("GET", "HEAD", "OPTIONS") or path == "/api/v1/web":
            return True                     # /api/v1/web: the key header (start_session) instead
        origin = request.headers.get("origin")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        return origin is None or urlsplit(origin).netloc.lower() == host.split(",")[0].strip().lower()

    def session_row(raw, request):
        """A browser signed in with a password (the owner's, or a team member's own): a
        token-like row of role "web". A member's carries whose shared meetings they read.
        Its cookie only opens pages; the meetings app's data also needs the key the page
        keeps (KEY_HEADER), which another app on the same host name cannot read."""
        ses = db.one("SELECT s.*, u.role AS user_role, u.invited_by FROM sessions s JOIN users u ON u.id = s.user_id"
                     " WHERE s.token_hash = ? AND s.expires_at > ?", hash_token(raw), now_iso())
        if not ses:
            return None
        if request.url.path == "/api/v1/web" and not hmac.compare_digest(
                ses["key_hash"] or "-", hash_token("key:" + request.headers.get(KEY_HEADER, ""))):
            return None
        if (ses["last_used_at"] or "")[:16] != now_iso()[:16]:
            db.run("UPDATE sessions SET last_used_at = ? WHERE id = ?", now_iso(), ses["id"])
            db.run("UPDATE users SET last_seen_at = ? WHERE id = ?", now_iso(), ses["user_id"])
        member = ses["user_role"] == "member"
        return {"id": ses["id"], "name": "Browser", "role": "web", "user_id": ses["user_id"], "trusts_identity": 0,
                "created_at": ses["created_at"], "last_used_at": ses["last_used_at"], "revoked_at": None,
                "team_owner": (ses["invited_by"] or "-") if member else None}

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

    def page(request, name, who, status_code=200, **ctx):
        tok = who.token if hasattr(who, "token") else who
        return templates.TemplateResponse(request, name, {"tok": tok, "who": who, "s": s, **ctx},
                                          status_code=status_code)

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
        mid = None                              # set once this call has made its meeting, and only then
        try:
            mid = store.create(parse_iso(row["started_at"]), title=body.title or row["title"],
                               started_at=row["started_at"] or now_iso(), source=f"device:{tok['name']}",
                               recording_id=rid, language=row["language"], storage=row["storage"],
                               owner_id=tok.get("user_id"))
            dst = store.folder(mid) / f"original.{audio.ext_for_mime(row['mime'])}"
            # sealed on the phone: opened here with the box's sealing key; stored encrypted at rest
            key = vault.SEALER.chunk_key(row["seal_epk"], row["client_id"]) if row["seal_epk"] else None
            with vault.VAULT.writer(dst) as out:
                for i in range(body.total_chunks):
                    data = read_bytes(s.uploads_dir / rid / f"{i:06d}.part")
                    out.write(vault.open_chunk(key, row["client_id"], i, data) if key else data)
        except Exception as e:
            db.run("UPDATE recordings SET state = 'uploading' WHERE id = ?", rid)
            if mid:
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
        if body.drive_include_audio and is_agent(who):
            raise HTTPException(403, "only the person decides whether recordings go to Drive")
        try:
            return store.update_settings(**body.model_dump(), user_id=who.user_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/v1/meetings/{mid}/storage")
    def put_storage(mid: str, body: StorageIn, who=Depends(person("device", "agent", "admin"))):
        meeting_or_404(mid, who)
        try:
            change_storage(mid, body.storage)
        except ValueError as e:
            raise HTTPException(400, str(e))
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

    def jobs_of(who):
        """The queue as far as it is the caller's: their own meetings' jobs."""
        return [j for j in worker.queue() if who.sees(store.meta(j["meeting_id"]))]

    @app.get("/api/v1/jobs")
    def jobs(who=Depends(person("device", "agent", "admin"))):
        current = worker.current
        return {"current": current if current and who.sees(store.meta(current["meeting_id"])) else None,
                "queue": jobs_of(who)}

    @app.get("/api/v1/health")
    def health():
        alive = bool(worker.thread and worker.thread.is_alive())
        return {"ok": True, "version": __version__, "worker": alive, "engine": s.engine, "llm": s.llm_model, "box": s.box_name,
                "drive": s.drive or google.available(),
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
        try:
            return followup.sync_issues(body.repo, body.issues)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # -- web view ---------------------------------------------------------------

    # wrong sign-ins per address (the gateway forwards the phone's or computer's own), so
    # nobody locks out everyone else; and per address and name, so one name is not guessed at
    logins, tries = Guesses(limit=30), Guesses(limit=5)
    no_member = passwords.make(secrets.token_urlsafe(16))   # checked for unknown names, so they take as long

    def client(request):
        return request.client.host if request.client else "-"

    def start_session(request, user_id, next="/"):
        """A browser signed in with a password: a cookie, and a key the page keeps in its
        own storage and sends with every call (KEY_HEADER). Other apps on the same host
        name get the cookie (cookies ignore ports) but never the key: it arrives once, in
        the fragment of this redirect, which no server sees."""
        raw, key = new_token(), secrets.token_urlsafe(24)
        expires = (utcnow() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.run("INSERT INTO sessions (id, user_id, token_hash, key_hash, created_at, expires_at)"
               " VALUES (?, ?, ?, ?, ?, ?)",
               new_id("ses"), user_id, hash_token(raw), hash_token("key:" + key), now_iso(), expires)
        db.run("DELETE FROM sessions WHERE expires_at <= ?", now_iso())
        mid = next[3:].split("?")[0].split("#")[0] if next.startswith("/m/") else ""
        resp = RedirectResponse("/#" + (f"m={quote(mid)}&" if mid else "") + f"k={key}", status_code=303)
        # a cookie marked Secure is never sent back over plain HTTP (an Indie Box's default)
        resp.set_cookie(COOKIE, raw, httponly=True, secure=s.secure_cookies and request.url.scheme == "https",
                        samesite="strict", max_age=SESSION_DAYS * 86400)
        return resp

    def owner_named(name):
        me = db.one("SELECT name FROM users WHERE id = ?", owner_id) if owner_id else None
        return not name or name.lower() in ("owner", ((me or {}).get("name") or "").lower())

    def name_taken(name, uid=None):
        """People sign in with their name: one person each ("owner" and the owner's name count)."""
        if db.one("SELECT 1 FROM users WHERE role = 'member' AND lower(name) = lower(?) AND id IS NOT ?", name, uid):
            return True
        return uid != owner_id and owner_named(name)

    def lang_of(request):
        return "de" if request.headers.get("accept-language", "").lower().startswith("de") else "en"

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return page(request, "login.html", None, next=next, error=None, lang=lang_of(request))

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy_page(request: Request):
        """What happens to recordings, and recording only with everyone's consent (public)."""
        return page(request, "privacy.html", None, lang=lang_of(request), root="")

    @app.get("/about", response_class=HTMLResponse)
    def about_page(request: Request):
        return page(request, "about.html", None, lang=lang_of(request), root="")

    @app.post("/login")
    def login(request: Request, token: str = Form(...), name: str = Form(""), next: str = Form("/")):
        given, lang, who = token.strip(), lang_of(request), " ".join(name.split())[:80]
        safe = next.startswith("/") and not next.startswith("//") and "\\" not in next
        resp = RedirectResponse(next if safe else "/", status_code=303)
        again = {"next": next, "lang": lang, "login_name": who}
        if s.owner_password:
            # Passwords only: a phone's token in a cookie would reach every app on the box's host name
            where, named = client(request), f"{client(request)} {who.lower()}"
            if logins.blocked(where) or tries.blocked(named):
                return page(request, "login.html", None, error="blocked", status_code=429, **again)
            if owner_named(who):
                if hmac.compare_digest(given.encode(), s.owner_password.encode()):
                    return start_session(request, owner_id, next if safe else "/")
            else:
                m = db.one("SELECT * FROM users WHERE role = 'member' AND lower(name) = lower(?)", who)
                if passwords.check(given, (m or {}).get("password_hash") or no_member) and m:
                    return start_session(request, m["id"], next if safe else "/")
            if not given.startswith("irt_"):    # a token (the phone app signing in its web view) is no guess
                logins.miss(where)
                tries.miss(named)
            return page(request, "login.html", None, error="password", status_code=401, **again)
        row = db.one("SELECT * FROM tokens WHERE token_hash = ? AND revoked_at IS NULL", hash_token(given))
        if not row or row["role"] not in READERS:
            return page(request, "login.html", None, error="token", status_code=401, **again)
        resp.set_cookie(COOKIE, given, httponly=True, secure=s.secure_cookies and request.url.scheme == "https",
                        samesite="strict", max_age=180 * 86400)
        return resp

    # -- joining the team: a link from the owner, once, to choose a password ----

    joins = Guesses()
    INVITE_DAYS = 7

    def invite_row(code):
        return db.one("SELECT * FROM invites WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                      hash_token("invite:" + str(code or "")), now_iso())

    @app.get("/join", response_class=HTMLResponse)
    def join_page(request: Request):
        """Where an invitation leads (the code stays in the fragment, the server never logs it)."""
        return templates.TemplateResponse(request, "join.html", {"s": s, "lang": lang_of(request), "error": None})

    @app.get("/api/v1/join/{code}")
    def join_peek(code: str, request: Request):
        if joins.blocked(client(request)):
            raise HTTPException(429, "too many wrong links, try again in a few minutes")
        row = invite_row(code)
        if not row:
            joins.miss(client(request))
            raise HTTPException(404, "unknown, used or expired link")
        member = db.one("SELECT * FROM users WHERE id = ?", row["user_id"])
        owner = db.one("SELECT * FROM users WHERE id = ?", member["invited_by"]) if member else None
        return {"name": (member or {}).get("name"), "owner": (owner or {}).get("name"), "box": s.box_name,
                "expires_at": row["expires_at"]}

    @app.post("/join")
    def join(request: Request, code: str = Form(...), password: str = Form(...), password2: str = Form(...)):
        lang = lang_of(request)
        if joins.blocked(client(request)):
            return templates.TemplateResponse(request, "join.html", {"s": s, "lang": lang, "error": "blocked"},
                                              status_code=429)
        wrong = passwords.problem(password) and "short" or (password != password2 and "differ")
        if wrong:
            return templates.TemplateResponse(request, "join.html", {"s": s, "lang": lang, "error": wrong, "code": code},
                                              status_code=400)
        with db.tx() as con:
            row = con.execute("SELECT * FROM invites WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                              (hash_token("invite:" + code), now_iso())).fetchone()
            if row:
                con.execute("UPDATE invites SET used_at = ? WHERE code_hash = ?", (now_iso(), row["code_hash"]))
        if not row:
            joins.miss(client(request))
            return templates.TemplateResponse(request, "join.html", {"s": s, "lang": lang, "error": "used"},
                                              status_code=404)
        # a new link for someone who joined before is how a password is reset: old sessions end
        db.run("DELETE FROM sessions WHERE user_id = ?", row["user_id"])
        db.run("UPDATE users SET password_hash = ?, last_seen_at = ? WHERE id = ?", passwords.make(password), now_iso(),
               row["user_id"])
        return start_session(request, row["user_id"])

    @app.get("/logout")
    def logout(request: Request):
        raw = request.cookies.get(COOKIE, "")
        if raw:
            db.run("DELETE FROM sessions WHERE token_hash = ?", hash_token(raw))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        row = token_row(request)
        if row and row["role"] == "web":
            return web_app(request)
        who = resolve(db, row, request.headers) if row and row["role"] in READERS else None
        if not who:
            return RedirectResponse("/login?next=/", status_code=303)
        return page(request, "meetings.html", who, meetings=store.list(who=who), jobs=jobs_of(who))

    def web_app(request):
        """The Meetings app, as in brainbot:agent command, talking to /api/v1/web."""
        lang = lang_of(request)
        html = WEB_APP.read_text()
        # the key from the sign-in's redirect (#k=...) goes into this page's own storage
        shim = ("<script>(() => { const h = new URLSearchParams(location.hash.slice(1)), k = h.get('k');"
                " if (!k) return; try { localStorage.setItem('mp_key', k); } catch (e) {} h.delete('k');"
                " const rest = h.toString(); history.replaceState(null, '', location.pathname + (rest ? '#' + rest : '')); })();"
                " window.IndieApp = window.IndieApp || { web: true, ask: async () => { throw new Error('no agent'); },"
                " fetch: async (connector, body) => { let key = ''; try { key = localStorage.getItem('mp_key') || ''; } catch (e) {}"
                " const r = await fetch('/api/v1/web', { method: 'POST', credentials: 'same-origin',"
                " headers: { 'content-type': 'application/json', 'x-minutes-key': key }, body: JSON.stringify(body) });"
                " if (r.status === 401) { location.href = '/login?next=/'; throw new Error('signed out'); }"
                " const j = await r.json().catch(() => ({})); if (!j.ok) throw new Error(j.error || r.statusText);"
                " return j; } };</script>")
        html = html.replace("<html>", f"<html lang=\"{lang}\">", 1).replace("<script", shim + "<script", 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                           "Referrer-Policy": "same-origin"})

    @app.get("/agent/SKILL.md", response_class=PlainTextResponse)
    def agent_skill():
        """The meeting-follow-up skill for any agent with MCP (agents/README.md)."""
        return PlainTextResponse(AGENT_SKILL.read_text(), media_type="text/markdown; charset=utf-8")

    @app.get("/agent/", response_class=HTMLResponse)
    def agent_guide(request: Request):
        """How to set up one's own agent (agents/README.md), with this recorder's address in it."""
        text = (AGENT_SKILL.parent.parent / "README.md").read_text().replace("https://minutes.example:9207",
                                                                               s.public_url)
        return templates.TemplateResponse(request, "agent.html", {"s": s, "tok": None, "body": md.render(text)})

    def web_or_person(request: Request):
        """A browser signed in with the password goes to the meetings app instead
        (its meeting links land there); everyone else as person(*READERS) does."""
        row = token_row(request)
        if row and row["role"] == "web":
            return row
        return person(*READERS, html=True)(request)

    @app.get("/m/{mid}", response_class=HTMLResponse)
    def meeting_page(request: Request, mid: str, v: int | None = None, who=Depends(web_or_person)):
        if isinstance(who, dict):                        # a web session: the meetings app opens it
            return RedirectResponse("/#m=" + quote(mid), status_code=303)
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
        try:
            change_storage(mid, storage)
        except ValueError as e:
            raise HTTPException(400, str(e))
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
                "apk_url": s.apk_url}

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
    def pair_peek(code: str, request: Request):
        """What a code would connect to, so the phone can ask before it spends it."""
        if guesses.blocked(client(request)):
            raise HTTPException(429, "too many wrong codes, try again in a few minutes")
        row = codes.peek(code)
        if not row:
            guesses.miss(client(request))
            raise HTTPException(404, "unknown, used or expired code")
        return {"person": person_name(row["user_id"]), "box": s.box_name, "box_url": s.public_url, "ca": ca_der(),
                "expires_at": row["expires_at"], "webapp": s.webapp_url,
                "apk_url": s.apk_url}

    @app.post("/api/v1/pair")
    def pair_redeem(body: PairIn, request: Request):
        """Spends a code: the phone's own token, and where and how to reach the box."""
        if guesses.blocked(client(request)):
            raise HTTPException(429, "too many wrong codes, try again in a few minutes")
        name = " ".join(body.device.split())[:60] or "Phone"
        raw, row = codes.redeem(body.code, phone_token, name)
        if not raw:
            guesses.miss(client(request))
            raise HTTPException(404, "unknown, used or expired code")
        return {"token": raw, "box_url": s.public_url, "ca": ca_der(), "person": person_name(row["user_id"]),
                "device": name, "box": s.box_name}

    @app.get("/indie-recorder.apk")
    def apk():
        if not s.apk_file.exists():
            raise HTTPException(404)
        return FileResponse(s.apk_file, media_type="application/vnd.android.package-archive", filename="minutes-pro.apk")

    @app.post("/devices/{tid}/revoke")
    def revoke(tid: str, tok=Depends(need("admin", html=True))):
        db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", now_iso(), tid)
        return RedirectResponse("/devices", status_code=303)

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(PWA_DIR / "icon-192.png")

    from .engines.remote import health as engine_health
    from .mcp_server import mount_mcp

    seen = {"at": -60.0, "engines": {}}

    def engines_health():
        """The engines as the apps show them, asked at most every 15 s; an engine set
        to "off" (engine B on an Indie Box, QWEN_URL=off) is not there to ask."""
        if time.monotonic() - seen["at"] < 15:
            return seen["engines"]
        out = {}
        for name, url in (("A", s.nemo_url), ("B", s.qwen_url)):
            if url == "off":
                continue
            h = engine_health(url, timeout=3)
            out[name] = {"ok": bool(h.get("ok")), "busy": bool(h.get("current"))} if h else {"ok": False}
        seen.update(at=time.monotonic(), engines=out)
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

    # A browser signed in with the owner's password (an Indie Box, or a recorder on a Mac):
    # the phone's ops plus connecting phones and giving the person's own agent a token.
    AGENT_OPS = {"agent_tokens", "new_agent_token", "revoke_agent_token"}
    TEAM_OPS = {"team", "team_invite", "team_reset", "team_remove", "team_settings", "share_meeting"}
    WEB_OPS = PHONE_OPS | {"pair_phone", "phones", "remove_phone", "delete_meeting"} | AGENT_OPS | TEAM_OPS
    # a team member reads what the owner shares: recording and managing stay with the owner
    MEMBER_OPS = {"status", "whoami", "set_name", "list_meetings", "get_meeting"}

    @app.post("/api/v1/web")
    async def web_ops(request: Request, who=Depends(person("web"))):
        if "application/json" not in request.headers.get("content-type", ""):
            return JSONResponse({"ok": False, "error": "send JSON"}, status_code=415)
        return await answer(request, who, MEMBER_OPS if who.member else WEB_OPS)

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
            role = "member" if who.member else "owner" if who.token["role"] == "web" else "person"
            out = {"meetings": len(store.list(limit=10000, who=who)), "caller": who.caller,
                   "phones": len(phones(who)), "name": me["name"], "role": role}
            if who.member:
                owner = db.one("SELECT name FROM users WHERE id = ?", who.team_owner)
                out["owner"] = (owner or {}).get("name")
            elif role == "owner":
                out["team_members"] = len(members(who.user_id))
            return out
        if op in TEAM_OPS:
            if who.token["role"] != "web" or who.member:
                raise ToolError("only the owner manages the team")
            return team_op(op, args, who.user_id)
        if op == "delete_meeting":
            if who.member or is_agent(who):
                raise ToolError("only the person who recorded a meeting deletes it")
            mid = str(args.get("meeting_id") or "")
            m = store.meta(mid)
            if not m or m.get("owner_id") != who.user_id:
                raise ToolError(f"no meeting {mid!r}")
            if db.one("SELECT 1 FROM jobs WHERE meeting_id = ? AND state = 'running'", mid):
                raise ToolError("the meeting is being processed right now: try again in a minute")
            store.erase(mid)
            return {"deleted": mid}
        if op == "set_name":
            # brainbot:agent command names people only by id; the person picks the name their phones show
            name = " ".join(str(args.get("name") or "").split())[:80] or None
            if who.token["role"] == "web":      # the owner and the team sign in with their names
                if who.member and not name:
                    raise ToolError("a name, please: you sign in with it")
                if name and name_taken(name, who.user_id):
                    raise ToolError(f"{name} is taken: pick another name")
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
        if op in AGENT_OPS:
            if who.token["role"] != "web":
                raise ToolError("agents in brainbot:agent command use the connector, not a token")
            return agent_op(op, args, who)
        return mcp_call(op, args, who)

    MAX_AGENTS = 5
    MAX_MEMBERS = 50

    def team_op(op, args, owner):
        """The owner's team: invite people by name (a link, once, to choose a password),
        a new link to reset one, remove them, and which meetings the team reads."""
        if op == "team_invite":
            name = " ".join(str(args.get("name") or "").split())[:80]
            if not name:
                raise ToolError("a name, please")
            if len(members(owner)) >= MAX_MEMBERS:
                raise ToolError(f"at most {MAX_MEMBERS} people in a team")
            if name_taken(name):
                raise ToolError(f"{name} is taken: pick another name")
            uid = add_person(db, name=name)["id"]
            db.run("UPDATE users SET role = 'member', invited_by = ? WHERE id = ?", owner, uid)
            return {"member": next(m for m in members(owner) if m["id"] == uid), **invite(uid)}
        if op in ("team_reset", "team_remove"):
            m = next((x for x in members(owner) if x["id"] == str(args.get("id") or "")), None)
            if not m:
                raise ToolError("no such person in the team")
            db.run("DELETE FROM sessions WHERE user_id = ?", m["id"])
            if op == "team_reset":              # the old password stops now, not when the new link is used
                db.run("UPDATE users SET password_hash = NULL WHERE id = ?", m["id"])
                return {"member": {**m, "joined": False, "last_seen_at": None}, **invite(m["id"])}
            for table in ("invites", "user_settings"):
                db.run(f"DELETE FROM {table} WHERE user_id = ?", m["id"])
            db.run("DELETE FROM users WHERE id = ?", m["id"])
        if op == "team_settings" and "share_new" in args:
            db.set_user_setting(owner, "share_new", "1" if args["share_new"] else "0")
        if op == "share_meeting":
            mid = str(args.get("meeting_id") or "")
            m = store.meta(mid)
            if not m or m.get("owner_id") != owner:
                raise ToolError(f"no meeting {mid!r}")
            db.run("UPDATE meetings SET shared = ? WHERE id = ?", 1 if args.get("shared") else 0, mid)
            return {"meeting_id": mid, "shared": bool(args.get("shared"))}
        return {"members": members(owner), "share_new": db.user_setting(owner, "share_new", "1") == "1"}

    def members(owner):
        now = now_iso()
        return [{"id": r["id"], "name": r["name"], "joined": bool(r["password_hash"]), "last_seen_at": r["last_seen_at"]
                 if r["password_hash"] else None,
                 "invite_expires_at": (db.one("SELECT max(expires_at) AS t FROM invites WHERE user_id = ? AND used_at IS NULL"
                                              " AND expires_at > ?", r["id"], now) or {}).get("t")}
                for r in db.all("SELECT * FROM users WHERE role = 'member' AND invited_by = ? ORDER BY created_at", owner)]

    def invite(uid):
        """A new link for the person; their older unused ones stop working."""
        code, now = secrets.token_urlsafe(24), now_iso()
        expires = (utcnow() + timedelta(days=INVITE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.run("UPDATE invites SET expires_at = ? WHERE user_id = ? AND used_at IS NULL AND expires_at > ?", now, uid, now)
        db.run("INSERT INTO invites (code_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
               hash_token("invite:" + code), uid, now, expires)
        url = f"{s.public_url}/join#{code}"
        return {"url": url, "qr": qr_svg(url), "expires_at": expires}

    def agent_op(op, args, who):
        """A token for the person's own agent (Claude, OpenClaw, Hermes, ...) to use the MCP
        server: it acts for them, writes summaries and suggests speaker names."""
        if op == "new_agent_token":
            if len(agent_tokens(who)) >= MAX_AGENTS:
                raise ToolError(f"at most {MAX_AGENTS} agents: remove one first")
            name = " ".join(str(args.get("name") or "").split())[:60] or "Agent"
            raw = create_token(db, name, "agent", user_id=who.user_id)
            return {"name": name, "token": raw, "mcp_url": s.public_url + "/mcp",
                    "skill_url": s.public_url + "/agent/SKILL.md", "agents": agent_tokens(who)}
        if op == "revoke_agent_token":
            n = db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND role = 'agent'"
                       " AND revoked_at IS NULL", now_iso(), str(args.get("id", "")), who.user_id)
            if not n:
                raise ToolError("no such agent")
            return {"removed": args.get("id"), "agents": agent_tokens(who)}
        return {"agents": agent_tokens(who), "mcp_url": s.public_url + "/mcp",
                "skill_url": s.public_url + "/agent/SKILL.md"}

    def agent_tokens(who):
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "last_used_at": r["last_used_at"]}
                for r in db.all("SELECT * FROM tokens WHERE user_id = ? AND role = 'agent' AND revoked_at IS NULL"
                                " ORDER BY created_at", who.user_id)]

    def phones(who):
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "last_used_at": r["last_used_at"]}
                for r in db.all("SELECT * FROM tokens WHERE user_id = ? AND role = 'device' AND revoked_at IS NULL"
                                " ORDER BY created_at", who.user_id)]

    from .mcp_server import ToolError
    return app


class Address:
    """Notes the address each request came in on (recorder/urls.py), for PUBLIC_URL=auto.
    Plain ASGI, so the endpoint and the threads it runs in see it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            urls.set_from(Request(scope))
        await self.app(scope, receive, send)


class NoCodes:
    """Connect and invite codes travel in the path of their look-up (GET /api/v1/pair/CODE,
    /api/v1/join/CODE): the access log shows those paths without them."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and isinstance(args[2], str) \
                and args[2].startswith(("/api/v1/pair/", "/api/v1/join/")):
            record.args = (*args[:2], args[2].rsplit("/", 1)[0] + "/***", *args[3:])
        return True


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
    # the proxies whose X-Forwarded-Proto counts: Caddy on aibox1's Docker network by default;
    # an Indie Box's gateway sits on a network of the box's choosing ("*" there: only the
    # box's own containers reach this one)
    import logging
    logging.getLogger("uvicorn.access").addFilter(NoCodes())
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,172.16.0.0/12"),
                log_level="info")


if __name__ == "__main__":
    main()
