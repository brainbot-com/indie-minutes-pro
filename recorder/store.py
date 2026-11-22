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
from .util import local_date, now_iso, read_json, write_json, write_text

STORAGE = ("local", "drive")


class Store:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def folder(self, mid):
        return self.s.meetings_dir / mid

    def link(self, mid):
        return f"{self.s.public_url}/m/{mid}"

    # -- settings the app and the web view can change ---------------------------

    def settings(self, user_id=None):
        """A person's settings (their own where they set one, else the box's)."""
        db, s = self.db, self.s
        return {"storage_default": db.user_setting(user_id, "storage_default", s.storage_default),
                "drive_folder": db.user_setting(user_id, "drive_folder", s.drive_folder),
                "drive_include_audio": db.user_setting(user_id, "drive_include_audio", "0") == "1"}

    def update_settings(self, storage_default=None, drive_folder=None, drive_include_audio=None, user_id=None):
        put = (lambda k, v: self.db.set_user_setting(user_id, k, v)) if user_id else self.db.set_setting
        if storage_default is not None:
            if storage_default not in STORAGE:
                raise ValueError(f"storage_default must be one of {STORAGE}")
            put("storage_default", storage_default)
        if drive_folder is not None:
            name = drive_folder.strip()
            if not name or "/" in name or len(name) > 120:
                raise ValueError("drive_folder must be a folder name (no slashes, at most 120 characters)")
            put("drive_folder", name)
        if drive_include_audio is not None:
            put("drive_include_audio", "1" if drive_include_audio else "0")
        return self.settings(user_id)

    def set_storage(self, mid, storage):
        """local: nothing about this meeting leaves the box. drive: its summary,
        action items and transcript (and audio, if set) go to Google Drive."""
        if storage not in STORAGE:
            raise ValueError(f"storage must be one of {STORAGE}")
        self.db.run("UPDATE meetings SET storage = ?, updated_at = ? WHERE id = ?", storage, now_iso(), mid)
        if storage == "drive":
            # let the follow-up agent pick the meeting up again
            self.db.run("DELETE FROM followup_seen WHERE meeting_id = ?", mid)
        self.write_meta(mid)

    def drive_folder_name(self, mid):
        m = self.meta(mid)
        when = local_date(m.get("started_at") or m.get("created_at"), self.s.display_tz)
        title = (m.get("title") or mid).replace("/", "-").strip()
        return f"{when} {title}"[:120]

    def drive_files(self, mid):
        """(name in Drive, local path) of what a Drive copy holds."""
        f = self.folder(mid)
        items = self.actions(mid).get("items", [])
        lines = [f"# Action items: {self.meta(mid).get('title') or mid}", ""]
        for a in items:
            extra = ", ".join(x for x in (a["owner"], a["due"] and f"due {a['due']}") if x)
            lines.append(f"- {a['text']}" + (f" ({extra})" if extra else ""))
        if not items:
            lines.append("None recorded.")
        write_text(f / "actions.md", "\n".join(lines) + "\n")
        out = [("Summary.md", f / "summary.md"), ("Action items.md", f / "actions.md"),
               ("Transcript.md", f / "transcript.md")]
        if self.settings(self.meta(mid).get("owner_id"))["drive_include_audio"] and self.original(mid):
            out.append((f"Audio{self.original(mid).suffix}", self.original(mid)))
        return [(name, path) for name, path in out if path.exists()]

    # -- index ---------------------------------------------------------------

    def meta(self, mid):
        return self.db.one("SELECT * FROM meetings WHERE id = ?", mid)

    def list(self, limit=200, who=None):
        """Every meeting (who=None, the box itself), else what that caller may see."""
        if who is None or who.admin:
            return self.db.all("SELECT * FROM meetings ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?", limit)
        return self.db.all("SELECT * FROM meetings WHERE owner_id IS ? ORDER BY COALESCE(started_at, created_at) DESC"
                           " LIMIT ?", who.user_id, limit)

    def set_state(self, mid, state, **fields):
        fields["state"] = state
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.run(f"UPDATE meetings SET {cols} WHERE id = ?", *fields.values(), mid)

    def create(self, mid, *, title=None, started_at=None, source=None, recording_id=None, language=None,
               storage=None, owner_id=None):
        self.folder(mid).mkdir(parents=True, exist_ok=False)
        storage = storage if storage in STORAGE else self.settings(owner_id)["storage_default"]
        self.db.run(
            "INSERT INTO meetings (id, title, created_at, started_at, source, recording_id, language, state, storage,"
            " owner_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            mid, title, now_iso(), started_at, source, recording_id, language, storage, owner_id)
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
