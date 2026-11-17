"""Meeting folders on disk plus their rows in the SQLite index.

data/meetings/<id>/
  meta.json            title, times, source, engine
  original.<ext>       the uploaded audio, as received
  audio.wav            16 kHz mono PCM, what the engines read
  transcript.json      words (text, start, end, speaker), segments, engine info
  transcript.md        rendered with the current speaker names
  speakers.json        {"Speaker 1": "Luis", ...}; empty means unnamed
  summaries/v001.json  every summary version, first by the recorder, later by agents
  summary.json         the current version
  summary.md           the current version, rendered with speaker names
  actions.json         action items of the current version, with stable keys
  engine/              raw engine output and logs
"""
import shutil

from . import render
from .util import now_iso, read_json, write_json, write_text


class Store:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def folder(self, mid):
        return self.s.meetings_dir / mid

    def link(self, mid):
        return f"{self.s.public_url}/m/{mid}"

    # -- index ---------------------------------------------------------------

    def meta(self, mid):
        return self.db.one("SELECT * FROM meetings WHERE id = ?", mid)

    def list(self, limit=200):
        return self.db.all("SELECT * FROM meetings ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?", limit)

    def set_state(self, mid, state, **fields):
        fields["state"] = state
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.run(f"UPDATE meetings SET {cols} WHERE id = ?", *fields.values(), mid)

    def create(self, mid, *, title=None, started_at=None, source=None, recording_id=None, language=None):
        self.folder(mid).mkdir(parents=True, exist_ok=False)
        self.db.run(
            "INSERT INTO meetings (id, title, created_at, started_at, source, recording_id, language, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')",
            mid, title, now_iso(), started_at, source, recording_id, language)
        self.write_meta(mid)
        return mid

    def write_meta(self, mid):
        write_json(self.folder(mid) / "meta.json", {**self.meta(mid), "url": self.link(mid)})

    def delete(self, mid):
        shutil.rmtree(self.folder(mid), ignore_errors=True)
        self.db.run("DELETE FROM meetings WHERE id = ?", mid)
        for table in ("jobs", "proposals", "mappings", "followup_seen"):
            self.db.run(f"DELETE FROM {table} WHERE meeting_id = ?", mid)

    # -- files ---------------------------------------------------------------

    def original(self, mid):
        found = sorted(self.folder(mid).glob("original.*"))
        return found[0] if found else None

    def audio(self, mid):
        return self.folder(mid) / "audio.wav"

    def transcript(self, mid):
        return read_json(self.folder(mid) / "transcript.json")

    def names(self, mid):
        return read_json(self.folder(mid) / "speakers.json", {}) or {}

    def summary(self, mid):
        return read_json(self.folder(mid) / "summary.json")

    def summary_versions(self, mid):
        d = self.folder(mid) / "summaries"
        return [read_json(p) for p in sorted(d.glob("v*.json"))] if d.exists() else []

    def actions(self, mid):
        return read_json(self.folder(mid) / "actions.json", {"items": []})

    def save_transcript(self, mid, transcript):
        f = self.folder(mid)
        write_json(f / "transcript.json", transcript)
        names = self.names(mid)
        for label in transcript.get("speakers", []):
            names.setdefault(label, "")
        write_json(f / "speakers.json", names)
        self.set_state(mid, self.meta(mid)["state"],
                       language=transcript.get("language"),
                       duration_s=transcript.get("duration_s"),
                       speakers=len(transcript.get("speakers", [])),
                       engine=(transcript.get("engine") or {}).get("name"))
        self.render(mid)

    def add_summary(self, mid, summary, author):
        """Store a new summary version and make it the current one."""
        versions = self.summary_versions(mid)
        version = (versions[-1]["version"] if versions else 0) + 1
        prev = versions[-1] if versions else None
        summary = {**summary, "version": version, "author": author, "created_at": now_iso()}
        if not summary.get("action_items") and prev and summary.get("markdown"):
            # An agent sent prose only: keep the structured items of the last version.
            summary["action_items"] = prev.get("action_items", [])
            summary["action_items_from"] = prev["version"]
        f = self.folder(mid)
        write_json(f / "summaries" / f"v{version:03d}.json", summary)
        write_json(f / "summary.json", summary)
        fields = {"summary_version": version, "summary_author": author, "tldr": summary.get("tldr")}
        meta = self.meta(mid)
        if summary.get("title") and not (meta.get("title") or "").strip():
            fields["title"] = summary["title"]
        self.set_state(mid, meta["state"], **fields)
        self.render(mid)
        return version

    def set_names(self, mid, names):
        current = self.names(mid)
        for label, name in names.items():
            if label in current:
                current[label] = (name or "").strip()
        write_json(self.folder(mid) / "speakers.json", current)
        self.render(mid)
        return current

    def render(self, mid):
        """Re-render the markdown files and actions.json with the current names."""
        f = self.folder(mid)
        meta = self.meta(mid)
        names = self.names(mid)
        tz = self.s.display_tz
        t = self.transcript(mid)
        if t:
            write_text(f / "transcript.md", render.render_transcript_md(t, names, meta, tz))
        s = self.summary(mid)
        if s:
            write_text(f / "summary.md", render.render_summary_md(s, names, meta, tz, self.link(mid)))
            write_json(f / "actions.json", render.build_actions(mid, s, names))
        self.write_meta(mid)
