"""MCP endpoint (/mcp) for the Agent Command Center agent: streamable HTTP with
plain JSON responses and no sessions, which MCP clients accept.

The tools mirror the JSON API: list meetings, read one with its full
transcript, store a better summary, and work the Drive outbox. Use it only
from agents that run on aibox1 against aibox1's own LLM: the transcripts it
returns must not leave the box."""
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from . import __version__

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """Meetings recorded on aibox1. Each meeting has a transcript with speaker labels
("Speaker 1".."Speaker N", or the names people set), a first summary written by the recorder,
and action items. Use update_summary to store a better summary with the context and memory you
have; it becomes the current version and the follow-up agent re-reads it. Drive updates the
people approved wait in list_drive_outbox; apply them, then call ack_drive_item."""

TOOLS = [
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
    {"name": "list_drive_outbox",
     "description": "Approved Drive updates waiting to be applied: target doc and folder, the markdown to "
                    "append, and the meeting link.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ack_drive_item",
     "description": "Report that you applied a Drive update, with the URL of the doc you changed.",
     "inputSchema": {"type": "object", "required": ["proposal_id", "url"], "properties": {
         "proposal_id": {"type": "string"}, "url": {"type": "string"}, "note": {"type": "string"}}}},
]


class ToolError(Exception):
    pass


def mount_mcp(app, s, db, store, followup, token_row):
    def agent(request):
        # Header only: a token in the query string would end up in access logs.
        tok = token_row(request)
        return tok if tok and tok["role"] in ("agent", "admin") else None

    def call(name, args, tok):
        if name == "list_meetings":
            rows = store.list(limit=1000)
            if args.get("since"):
                rows = [m for m in rows if (m["updated_at"] or m["created_at"]) > args["since"]]
            if args.get("state"):
                rows = [m for m in rows if m["state"] == args["state"]]
            return [{k: m[k] for k in ("id", "title", "started_at", "duration_s", "language", "state", "speakers",
                                       "summary_version", "summary_author", "updated_at")} | {"url": store.link(m["id"])}
                    for m in rows]
        mid = args.get("meeting_id", "")
        if name in ("get_meeting", "update_summary") and not store.meta(mid):
            raise ToolError(f"no meeting {mid!r}")
        if name == "get_meeting":
            m = store.meta(mid)
            out = {**m, "url": store.link(mid), "speaker_names": store.names(mid),
                   "summary_md": _read(store.folder(mid) / "summary.md"),
                   "action_items": store.actions(mid).get("items", []),
                   "summary_versions": [{k: v.get(k) for k in ("version", "author", "created_at", "note")}
                                        for v in store.summary_versions(mid)]}
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
            version = store.add_summary(mid, summary, author=f"agent ({tok['name']})")
            return {"meeting_id": mid, "version": version, "url": store.link(mid)}
        if name == "list_drive_outbox":
            return [{"proposal_id": p["id"], **p["payload"]} for p in followup.outbox()]
        if name == "ack_drive_item":
            res = followup.ack(args.get("proposal_id", ""), args.get("url", ""), args.get("note"), who=tok["name"])
            if res.get("error"):
                raise ToolError(res["error"])
            return res
        raise ToolError(f"unknown tool {name!r}")

    def handle(msg, tok):
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
                data = call(params.get("name"), params.get("arguments") or {}, tok)
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                          "isError": False}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        except ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:300]}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @app.post("/mcp")
    async def mcp_post(request: Request):
        tok = agent(request)
        if not tok:
            return JSONResponse({"error": "agent token required"}, status_code=401)
        try:
            msg = await request.json()
        except ValueError:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                                status_code=400)
        batch = isinstance(msg, list)
        replies = [r for r in (handle(m, tok) for m in (msg if batch else [msg])) if r is not None]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies if batch else replies[0])

    @app.get("/mcp")
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    @app.delete("/mcp")
    def mcp_delete():
        return Response(status_code=405, headers={"Allow": "POST"})


def _read(path):
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""
