"""MCP endpoint (/mcp) for the Agent Command Center agent: streamable HTTP with
plain JSON responses and no sessions, which MCP clients accept.

The tools mirror the JSON API: list meetings, read one with its full
transcript, store a better summary, choose local or Drive storage, sync the
repo's issues, and work the outbox of Drive and GitHub follow-ups. Agents that
read transcripts must use aibox1's own LLM, never another model host."""
import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import __version__, speakers, vault
from .people import resolve
from .util import read_text

SKILL = Path(__file__).parent / "skills" / "meeting-follow-up" / "SKILL.md"

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """Meetings recorded on aibox1. Each meeting is stored either locally (nothing leaves the box) or in
Google Drive (summary, action items and transcript go to a Drive folder). The person chooses with set_storage;
update_settings sets the default for new recordings. Each meeting has a transcript with speaker labels
("Speaker 1".."Speaker N", or the names people set), a first summary written by the recorder,
and action items. Use update_summary to store a better summary with the context and memory you
have; it becomes the current version and the follow-up agent re-reads it. Keep the box's copy
of the repo's issues fresh with sync_issues so new follow-ups can point at existing issues.
Follow-ups wait in list_outbox (Drive uploads of meetings set to Drive, entries for the meeting log,
GitHub issues and comments people approved): carry each out with your Drive and GitHub tools, then call
ack_outbox_item."""

TOOLS = [
    {"name": "pending_work",
     "description": "What there is to do now: meetings whose current summary is still the recorder's first one "
                    "(write a better one), and approved follow-ups waiting in the outbox (carry them out). Returns "
                    "nothing at all when there is nothing to do, so a routine can use it as its precheck.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_meetings",
     "description": "Meetings on aibox1, newest first: id, title, start time, duration, language, state, "
                    "number of speakers, current summary version and author, and the link.",
     "inputSchema": {"type": "object", "properties": {
         "since": {"type": "string", "description": "ISO 8601 time; only meetings created or updated after it"},
         "state": {"type": "string", "description": "for example done, transcribing, failed"}}}},
    {"name": "get_meeting",
     "description": "One meeting: current summary (markdown), action items with owner and due date, speaker "
                    "names, the list of summary versions, and the full transcript with speaker names.",
     "inputSchema": {"type": "object", "required": ["meeting_id"], "properties": {
         "meeting_id": {"type": "string"},
         "include_transcript": {"type": "boolean", "default": True}}}},
    {"name": "update_summary",
     "description": "Store a better summary as a new version; it becomes the current one. Write it in the "
                    "meeting's language with the sections TL;DR, decisions, action items (owner, due date if "
                    "said) and open questions. Refer to unnamed people by their label, e.g. 'Speaker 2'. "
                    "Also send action_items so follow-ups can be filed.",
     "inputSchema": {"type": "object", "required": ["meeting_id", "markdown"], "properties": {
         "meeting_id": {"type": "string"},
         "markdown": {"type": "string", "description": "the whole summary as markdown, without a title line"},
         "tldr": {"type": "string"},
         "action_items": {"type": "array", "items": {"type": "object", "required": ["text"], "properties": {
             "text": {"type": "string"}, "owner": {"type": "string"}, "due": {"type": "string"}}}},
         "note": {"type": "string", "description": "what you changed and which context you used"}}}},
    {"name": "status",
     "description": "The recorder's state: worker, current job, queue, engines, default storage, and how many "
                    "meetings and pending Drive uploads there are.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_settings",
     "description": "Defaults: storage for new recordings (local or drive), the Drive folder, whether audio goes to Drive.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "update_settings",
     "description": "Change the defaults. storage_default: local (nothing leaves the box) or drive. drive_folder: the "
                    "name of the Drive folder that holds a subfolder per meeting and the meeting log. "
                    "drive_include_audio: also upload the recording.",
     "inputSchema": {"type": "object", "properties": {
         "storage_default": {"type": "string", "enum": ["local", "drive"]},
         "drive_folder": {"type": "string"},
         "drive_include_audio": {"type": "boolean"}}}},
    {"name": "set_storage",
     "description": "Keep one meeting local, or store it in Google Drive. Switching to drive queues its upload; "
                    "switching back to local stops further uploads (what is already in Drive stays there).",
     "inputSchema": {"type": "object", "required": ["meeting_id", "storage"], "properties": {
         "meeting_id": {"type": "string"}, "storage": {"type": "string", "enum": ["local", "drive"]}}}},
    {"name": "rename_speakers",
     "description": "Set names for speaker labels, e.g. {\"Speaker 1\": \"Luis\"}. The names replace the labels in "
                    "the transcript, the summary and the action items.",
     "inputSchema": {"type": "object", "required": ["meeting_id", "names"], "properties": {
         "meeting_id": {"type": "string"}, "names": {"type": "object", "additionalProperties": {"type": "string"}}}}},
    {"name": "new_summary",
     "description": "Have the recorder write a new first summary of a meeting with the box's LLM (queued).",
     "inputSchema": {"type": "object", "required": ["meeting_id"], "properties": {"meeting_id": {"type": "string"}}}},
    {"name": "drive_files",
     "description": "For a drive_store item from the outbox: the Drive folder, the meeting's subfolder, and each "
                    "file with a signed download link (valid two hours). Download each with curl -o into your "
                    "folder and upload it with your Drive tools: the files never pass through the conversation.",
     "inputSchema": {"type": "object", "required": ["proposal_id"], "properties": {"proposal_id": {"type": "string"}}}},
    {"name": "sync_issues",
     "description": "Send the repo's issues (open and recently closed) so the box's follow-up agent can match "
                    "action items against them. Replaces the previous copy.",
     "inputSchema": {"type": "object", "required": ["repo", "issues"], "properties": {
         "repo": {"type": "string", "description": "owner/name, e.g. brainbot-com/indie-recorder-sandbox"},
         "issues": {"type": "array", "items": {"type": "object", "required": ["number", "title"], "properties": {
             "number": {"type": "integer"}, "title": {"type": "string"}, "body": {"type": "string"},
             "state": {"type": "string"}, "html_url": {"type": "string"}}}}}}},
    {"name": "list_outbox",
     "description": "Follow-ups waiting to be carried out. kind drive_store: call drive_files with its id and "
                    "put each file into the Drive folder payload.folder, subfolder payload.subfolder. kind "
                    "drive_log: append payload.markdown to the Google Doc payload.doc in the Drive folder "
                    "payload.folder (create it if missing). kind github_issue: open an issue in payload.repo with "
                    "payload.title and payload.body. kind github_comment: comment payload.body on issue "
                    "payload.issue_number. GitHub bodies contain a hidden marker; post them unchanged so reruns "
                    "can be detected.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "meeting_files",
     "description": "Signed download links (valid two hours) for a meeting's Summary.md, Action items.md and "
                    "Transcript.md. Download a long transcript with curl -o and read the file: it never has to "
                    "pass through the conversation, which would cut it.",
     "inputSchema": {"type": "object", "required": ["meeting_id"], "properties": {"meeting_id": {"type": "string"}}}},
    {"name": "suggest_speakers",
     "description": "Suggest who a speaker label is, e.g. {\"Speaker 2\": {\"name\": \"Anna\", \"why\": \"addressed as Anna at "
                    "00:12:03; leads the sales topics like in earlier meetings\"}}. The person sees each suggestion with its "
                    "reason and decides; nothing is renamed. A new suggestion of yours for a label replaces your older one; an "
                    "empty name withdraws it.",
     "inputSchema": {"type": "object", "required": ["meeting_id", "suggestions"], "properties": {
         "meeting_id": {"type": "string"},
         "suggestions": {"type": "object", "additionalProperties": {"type": "object", "properties": {
             "name": {"type": "string"}, "why": {"type": "string"}}}}}}},
    {"name": "agent_setup",
     "description": "The meeting-follow-up skill (SKILL.md): how a person's own agent sets itself up to write "
                    "better summaries and store meetings in Google Drive, and what each run does.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ack_outbox_item",
     "description": "Report that you carried out an outbox item, with the URL of the Drive folder, doc, issue "
                    "or comment.",
     "inputSchema": {"type": "object", "required": ["proposal_id", "url"], "properties": {
         "proposal_id": {"type": "string"}, "url": {"type": "string"}, "note": {"type": "string"}}}},
]


class ToolError(Exception):
    pass


def mount_mcp(app, s, db, store, followup, token_row, worker=None, engines_health=None):
    """Mounts /mcp and returns call(name, args, who), which the connector for
    brainbot:agent command (api.py) shares. Every tool answers for one caller:
    their meetings, their outbox, their settings (people.py)."""
    def agent(request):
        # Header only: a token in the query string would end up in access logs.
        tok = token_row(request)
        return resolve(db, tok, request.headers) if tok and tok["role"] in ("agent", "admin") else None

    def visible(who, mid):
        m = store.meta(mid) if mid else None
        return m if who.sees(m) else None

    def author(who):
        """Who makes a suggestion: the person in the app, or an agent (theirs through
        the connector, or one with a recorder token of its own)."""
        if who.token.get("trusts_identity"):
            return "person" if who.caller == "user" else "agent"
        return "agent" if who.token["role"] == "agent" else "person"

    def speaker_cards(mid):
        """Each speaker with what helps to name them, the most talkative first; the
        clip is a signed link (two hours) to seven seconds of their voice."""
        from .signing import sign
        prof = speakers.profile(store.transcript(mid) or {})
        names, sugg, audio = store.names(mid), speakers.hints(store.folder(mid)), store.audio(mid).exists()
        cards = [{"label": label, "name": names.get(label, ""), "suggestions": sugg.get(label, []),
                  **{k: p[k] for k in ("share", "words", "turns", "first", "lines")},
                  "clip_url": f"{s.files_base_url}/{sign(s, mid, 'clip:' + label)}" if audio and p["clip"] else None}
                 for label, p in prof.items()]
        return sorted(cards, key=lambda c: -c["words"])

    def outbox(who):
        return [p for p in followup.outbox() if visible(who, p["meeting_id"])]

    def call(name, args, who):
        if name == "pending_work":
            since = s.agent_since
            summaries = [{"meeting_id": m["id"], "title": m["title"], "started_at": m["started_at"],
                          "language": m["language"], "url": store.link(m["id"])}
                         for m in store.list(limit=1000, who=who)
                         if m["state"] == "done" and (m["summary_author"] or "").startswith("recorder")
                         and (m["started_at"] or m["created_at"]) >= since]
            box = [{"proposal_id": p["id"], "kind": p["kind"], "meeting_id": p["meeting_id"]} for p in outbox(who)]
            return "" if not (summaries or box) else {"summaries": summaries, "outbox": box}
        if name == "list_meetings":
            rows = store.list(limit=1000, who=who)
            if args.get("since"):
                rows = [m for m in rows if (m["updated_at"] or m["created_at"]) > args["since"]]
            if args.get("state"):
                rows = [m for m in rows if m["state"] == args["state"]]
            return [{k: m[k] for k in ("id", "title", "started_at", "duration_s", "language", "state", "speakers",
                                       "summary_version", "summary_author", "updated_at", "storage", "drive_url",
                                       "tldr")} | {"url": store.link(m["id"])}
                    for m in rows]
        if name == "status":
            rows = store.list(limit=10000, who=who)
            states = {}
            for m in rows:
                states[m["state"]] = states.get(m["state"], 0) + 1
            box = outbox(who)
            return {"worker": bool(worker and worker.thread and worker.thread.is_alive()),
                    "current_job": worker.current if worker else None,
                    "queued_jobs": len(worker.queue()) if worker else None,
                    "engine": s.engine, "llm": s.llm_model, "engines": engines_health() if engines_health else {},
                    "meetings": len(rows), "by_state": states,
                    "in_drive": sum(1 for m in rows if m.get("drive_url")),
                    "pending_drive": sum(1 for p in box if p["kind"].startswith("drive_")),
                    "settings": store.settings(who.user_id), "followup": followup.executor,
                    "targets": sorted(s.followup_targets)}
        if name == "get_settings":
            return store.settings(who.user_id)
        if name == "update_settings":
            try:
                return store.update_settings(**{k: args[k] for k in ("storage_default", "drive_folder",
                                                                       "drive_include_audio") if k in args},
                                             user_id=who.user_id)
            except ValueError as e:
                raise ToolError(str(e))
        if name == "drive_files":
            p = next((x for x in outbox(who) if x["id"] == args.get("proposal_id")), None)
            if not p or p["kind"] != "drive_store":
                raise ToolError("no drive_store item with that proposal_id in the outbox")
            from .signing import sign
            mid = p["meeting_id"]
            return {"folder": p["payload"]["folder"], "subfolder": p["payload"]["subfolder"],
                    "files": [{"name": n, "bytes": vault.VAULT.size(path),
                               "url": f"{s.files_base_url}/{sign(s, mid, n)}"} for n, path in store.drive_files(mid)]}
        if name == "agent_setup":
            # the routine's prompt is the skill's "Each run" part; one copy keeps the answer
            # well under what an agent's data_connector passes on (12,000 characters)
            return {"skill": SKILL.read_text(),
                    "routine": {"title": "Meeting follow-up", "schedule": "every hour", "quiet": "[Nothing new]",
                                "prompt": "the whole text under \"## Each run\" in the skill"}}
        mid = args.get("meeting_id", "")
        if name in ("get_meeting", "update_summary", "set_storage", "rename_speakers", "new_summary", "meeting_files",
                    "suggest_speakers") and not visible(who, mid):
            raise ToolError(f"no meeting {mid!r}")
        if name == "suggest_speakers":
            try:
                return speakers.suggest(store.folder(mid), set(store.names(mid)), args.get("suggestions") or {}, author(who))
            except ValueError as e:
                raise ToolError(str(e))
        if name == "meeting_files":
            from .signing import sign
            return {"meeting_id": mid, "files": [{"name": n, "bytes": vault.VAULT.size(path),
                                                  "url": f"{s.files_base_url}/{sign(s, mid, n)}"}
                                                 for n, path in store.drive_files(mid) if not n.startswith("Audio")]}
        if name == "set_storage":
            try:
                store.set_storage(mid, args.get("storage"))
            except ValueError as e:
                raise ToolError(str(e))
            if args["storage"] == "drive" and store.meta(mid)["state"] == "done":
                import threading
                threading.Thread(target=followup.run, kwargs={"meeting_ids": [mid]}, daemon=True).start()
            return {"meeting_id": mid, "storage": args["storage"]}
        if name == "rename_speakers":
            names = args.get("names") or {}
            if not isinstance(names, dict):
                raise ToolError("names must be an object")
            return {"meeting_id": mid, "names": store.set_names(mid, {str(k): str(v) for k, v in names.items()})}
        if name == "new_summary":
            if not worker:
                raise ToolError("the worker is not running")
            worker.enqueue(mid, kind="summarize")
            return {"meeting_id": mid, "queued": True}
        if name == "get_meeting":
            m = store.meta(mid)
            out = {**m, "url": store.link(mid), "speaker_names": store.names(mid),
                   "summary_md": _read(store.folder(mid) / "summary.md"),
                   "action_items": store.actions(mid).get("items", []),
                   "summary_versions": [{k: v.get(k) for k in ("version", "author", "created_at", "note")}
                                        for v in store.summary_versions(mid)],
                   "speakers": speaker_cards(mid)}
            if args.get("include_transcript", True):
                out["transcript_md"] = _read(store.folder(mid) / "transcript.md")
            return out
        if name == "update_summary":
            if not (args.get("markdown") or "").strip():
                raise ToolError("markdown is required")
            current = store.summary(mid) or {}
            summary = {"markdown": args["markdown"], "language": current.get("language"),
                       "title": current.get("title")}
            for k in ("tldr", "note"):
                if args.get(k):
                    summary[k] = args[k]
            if args.get("action_items"):
                summary["action_items"] = [{"text": a.get("text", ""), "owner": a.get("owner", ""),
                                            "due": a.get("due", "")} for a in args["action_items"]]
            version = store.add_summary(mid, summary, author=f"agent ({who.name})")
            return {"meeting_id": mid, "version": version, "url": store.link(mid)}
        if name == "sync_issues":
            return followup.sync_issues(args.get("repo", ""), args.get("issues") or [])
        if name == "list_outbox":
            return [{"proposal_id": p["id"], "kind": p["kind"], "meeting_id": p["meeting_id"],
                     "payload": p["payload"]} for p in outbox(who)]
        if name == "ack_outbox_item":
            row = db.one("SELECT meeting_id FROM proposals WHERE id = ?", args.get("proposal_id", ""))
            if not row or not visible(who, row["meeting_id"]):
                raise ToolError("no such outbox item")
            res = followup.ack(args.get("proposal_id", ""), args.get("url", ""), args.get("note"), who=who.name)
            if res.get("error"):
                raise ToolError(res["error"])
            return res
        raise ToolError(f"unknown tool {name!r}")

    def handle(msg, who):
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:
            return None  # a notification, e.g. notifications/initialized
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                result = {"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "indie-recorder", "version": __version__},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                data = call(params.get("name"), params.get("arguments") or {}, who)
                text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        except ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:300]}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @app.post("/mcp")
    async def mcp_post(request: Request):
        who = agent(request)
        if not who:
            return JSONResponse({"error": "agent token required"}, status_code=401)
        try:
            msg = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                                status_code=400)
        batch = isinstance(msg, list)
        replies = [r for r in (handle(m, who) for m in (msg if batch else [msg])) if r is not None]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies if batch else replies[0])

    @app.get("/mcp")
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.delete("/mcp")
    def mcp_delete():
        return Response(status_code=405, headers={"Allow": "POST"})

    return call


def _read(path):
    try:
        return read_text(path)
    except FileNotFoundError:
        return ""
