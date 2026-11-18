"""Follow-up agent: turns meeting action items into GitHub proposals (comment
on an existing issue or open a new one) and a "Meeting log" entry for Drive.

Dry-run by default: every proposal waits on the review page (/review) until a
person approves it. FOLLOWUP_AUTO=1 (or --auto) approves new proposals at once.
Who carries out approved proposals (FOLLOWUP_EXECUTOR):
  agent   the outbox: the Agent Command Center agent, which has GitHub and Drive
          access, applies them and acknowledges each with its URL. It also
          syncs the repo's issues to the box (sync_issues) so proposals can
          point at existing issues. Nothing here needs a GitHub or Drive login.
  direct  this service calls GITHUB_API_URL and DRIVE_API_URL itself; used
          with the mock GitHub and Drive (mock/server.py) for tests and demos.
Idempotent: one proposal per (meeting, item), one mapping per executed item,
and a marker in every GitHub body so a lost database cannot cause duplicates.

CLI (JSON out; options can also come as JSON on stdin with --json-in -):
  python -m recorder.followup run [--auto] [--meeting ID ...]
  python -m recorder.followup list [--state proposed] [--meeting ID]
  python -m recorder.followup approve ID ... | reject ID ...
  python -m recorder.followup outbox
  python -m recorder.followup ack ID --url URL [--note TEXT]
"""
import argparse
import fcntl
import json
import re
import sys
from contextlib import contextmanager

import httpx

from .llm import chat_json
from .util import new_id, now_iso, read_json, write_json

DECIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer"},
                    "action": {"type": "string", "enum": ["comment", "new_issue", "log_only"]},
                    "issue_number": {"type": "integer"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["item", "action", "issue_number", "title", "body", "rationale"],
            },
        }
    },
    "required": ["decisions"],
}

DECIDE_SYSTEM = """You file follow-ups for action items from a meeting into a GitHub issue tracker.

For every action item choose one action:
- "comment": one of its candidate issues clearly tracks the same piece of work. Set issue_number to that issue and write a short comment saying what was agreed in the meeting (owner, due date if any).
- "new_issue": a concrete piece of work worth tracking that no candidate covers. Write a short, specific title and a body with what has to be done and why, based only on the action item and the meeting summary.
- "log_only": personal reminders, trivial tasks or things that do not belong in an engineering tracker. They still go to the meeting log.

Rules: never quote or invent transcript text, never invent owners or deadlines, keep bodies under 120 words, write in the language of the action item, set issue_number to 0 unless the action is "comment", and give a one-sentence rationale."""

STOP = set("""the and for with that this from will have into about need needs should would could
und der die das mit für von wird werden noch eine einen dass soll sollte bis""".split())


class GitHub:
    """GitHub's REST API, or the mock with the same shapes (no token needed)."""

    def __init__(self, settings):
        self.repo = settings.github_repo
        self.base = settings.github_api_url
        self.token_file = settings.github_token_file
        self._issues = None

    def _token(self):
        try:
            return self.token_file.read_text().strip()
        except OSError:
            return ""

    def available(self):
        return bool(self._token()) or not self.base.startswith("https://api.github.com")

    def _client(self):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "indie-recorder"}
        if self._token():
            headers["Authorization"] = f"Bearer {self._token()}"
        return httpx.Client(base_url=self.base, timeout=30, headers=headers)

    def issues(self):
        """Open and closed issues of the sandbox repo (pull requests left out)."""
        if self._issues is None:
            out = []
            with self._client() as c:
                for page in range(1, 6):
                    r = c.get(f"/repos/{self.repo}/issues", params={"state": "all", "per_page": 100, "page": page})
                    r.raise_for_status()
                    batch = [i for i in r.json() if "pull_request" not in i]
                    out += batch
                    if len(r.json()) < 100:
                        break
            self._issues = out
        return self._issues

    def find_marker(self, marker, issue_number=None):
        if issue_number:
            with self._client() as c:
                r = c.get(f"/repos/{self.repo}/issues/{issue_number}/comments", params={"per_page": 100})
                r.raise_for_status()
                hit = next((x for x in r.json() if marker in (x.get("body") or "")), None)
                return hit and hit["html_url"]
        hit = next((i for i in self.issues() if marker in (i.get("body") or "")), None)
        return hit and hit["html_url"]

    def create_issue(self, title, body):
        with self._client() as c:
            r = c.post(f"/repos/{self.repo}/issues", json={"title": title, "body": body, "labels": ["meeting-action"]})
            r.raise_for_status()
            self._issues = None
            return r.json()["html_url"]

    def comment(self, number, body):
        with self._client() as c:
            r = c.post(f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})
            r.raise_for_status()
            return r.json()["html_url"]


class Drive:
    """Appends to a doc and stores files in a folder. Only the mock exists
    here; real Drive updates are the Agent Command Center agent's job (outbox)."""

    def __init__(self, settings):
        self.base = settings.drive_api_url

    def available(self):
        return bool(self.base)

    def append(self, folder, doc, markdown):
        r = httpx.post(f"{self.base}/append", json={"folder": folder, "doc": doc, "markdown": markdown}, timeout=30)
        r.raise_for_status()
        return r.json()["url"]

    def store(self, folder, subfolder, files):
        """files: [(name, path)]. Text goes as text, anything else base64."""
        import base64
        body = []
        for name, path in files:
            data = path.read_bytes()
            try:
                body.append({"name": name, "text": data.decode()})
            except UnicodeDecodeError:
                body.append({"name": name, "base64": base64.b64encode(data).decode()})
        r = httpx.post(f"{self.base}/store", json={"folder": folder, "subfolder": subfolder, "files": body}, timeout=120)
        r.raise_for_status()
        return r.json()["url"]


def _terms(text):
    return {w for w in re.findall(r"[a-zäöüß0-9][a-zäöüß0-9-]{2,}", (text or "").lower()) if w not in STOP}


def candidates(item_text, issues, n=5):
    """Issues that share the most words with the action item."""
    want = _terms(item_text)
    scored = []
    for i in issues:
        have = _terms(i["title"]) | _terms((i.get("body") or "")[:600])
        score = len(want & have) / (len(want) or 1)
        if score >= 0.2:
            scored.append((score, i))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:n]]


def marker(mid, key):
    return f"<!-- indie-recorder meeting={mid} item={key} -->"


class Followup:
    def __init__(self, settings, db, store):
        self.s = settings
        self.db = db
        self.store = store
        self.github = GitHub(settings)
        self.drive = Drive(settings)
        self.executor = settings.followup_executor
        self.index_path = settings.data_dir / "github-issues.json"

    @contextmanager
    def _lock(self):
        with open(self.s.data_dir / ".followup.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    # -- reading ---------------------------------------------------------------

    def list(self, state=None, meeting_id=None):
        sql, args = "SELECT * FROM proposals WHERE 1 = 1", []
        if state:
            sql += " AND state = ?"
            args.append(state)
        if meeting_id:
            sql += " AND meeting_id = ?"
            args.append(meeting_id)
        rows = self.db.all(sql + " ORDER BY created_at DESC", *args)
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["result"] = json.loads(r["result"]) if r["result"] else None
        return rows

    def enabled(self, kind):
        """github_* proposals only while GitHub is a target (FOLLOWUP_TARGETS)."""
        return kind.split("_")[0] in self.s.followup_targets

    def outbox(self):
        """Approved proposals nobody has carried out yet, for targets still enabled."""
        return [p for p in self.list(state="approved") if self.enabled(p["kind"])]

    def issues(self):
        """Existing issues to match action items against: from the API (direct)
        or from the copy the Command Center agent synced (agent)."""
        if self.executor == "direct":
            return self.github.issues() if self.github.available() else []
        index = read_json(self.index_path, {}) or {}
        return index.get("issues", []) if index.get("repo") == self.s.github_repo else []

    def can_file_issues(self):
        if "github" not in self.s.followup_targets:
            return False
        return self.executor == "agent" or self.github.available()

    def sync_issues(self, repo, issues):
        keep = ("number", "title", "body", "state", "html_url")
        rows = [{k: (i.get(k) or "")[:2000] if k == "body" else i.get(k) for k in keep} for i in issues]
        write_json(self.index_path, {"repo": repo, "synced_at": now_iso(), "issues": rows})
        return {"repo": repo, "issues": len(rows)}

    # -- proposing -------------------------------------------------------------

    def pending_meetings(self, meeting_ids=None):
        rows = self.db.all(
            "SELECT m.id, m.summary_version FROM meetings m LEFT JOIN followup_seen f ON f.meeting_id = m.id"
            " WHERE m.state = 'done' AND m.summary_version > 0"
            " AND (f.summary_version IS NULL OR f.summary_version < m.summary_version) ORDER BY m.created_at")
        if meeting_ids:
            rows = [r for r in rows if r["id"] in meeting_ids]
        return rows

    def propose(self, meeting_ids=None):
        with self._lock():
            report = {"meetings": [], "proposed": [], "errors": []}
            for row in self.pending_meetings(meeting_ids):
                mid = row["id"]
                try:
                    new = self._propose_meeting(mid)
                    report["proposed"] += new
                    report["meetings"].append(mid)
                    self.db.run("INSERT INTO followup_seen (meeting_id, summary_version, seen_at) VALUES (?, ?, ?)"
                                " ON CONFLICT (meeting_id) DO UPDATE SET summary_version = excluded.summary_version,"
                                " seen_at = excluded.seen_at", mid, row["summary_version"], now_iso())
                except Exception as e:
                    report["errors"].append({"meeting_id": mid, "error": str(e)[:300]})
            return report

    def _known(self, mid, key):
        return (self.db.one("SELECT 1 FROM proposals WHERE meeting_id = ? AND item_key = ?", mid, key)
                or self.db.one("SELECT 1 FROM mappings WHERE meeting_id = ? AND item_key = ?", mid, key))

    def _add(self, mid, key, kind, payload, rationale, approved=False):
        pid = new_id("prop")
        n = self.db.run("INSERT OR IGNORE INTO proposals (id, meeting_id, item_key, kind, payload, rationale, created_at,"
                        " state, decided_at, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", pid, mid, key, kind,
                        json.dumps(payload, ensure_ascii=False), rationale, now_iso(),
                        "approved" if approved else "proposed", now_iso() if approved else None,
                        json.dumps({"decided_by": "storage choice"}) if approved else None)
        return pid if n else None

    def _propose_meeting(self, mid):
        meta = self.store.meta(mid)
        summary = self.store.summary(mid) or {}
        actions = self.store.actions(mid).get("items", [])
        link = self.store.link(mid)
        title = meta.get("title") or summary.get("title") or mid
        created = []
        new_items = [a for a in actions if not self._known(mid, a["key"])]
        if new_items and self.can_file_issues():
            issues = self.issues()
            listing = []
            for i, a in enumerate(new_items):
                cands = candidates(a["text"], issues)
                listing.append({"item": i, "text": a["text"], "owner": a["owner"], "due": a["due"],
                                "candidates": [{"number": c["number"], "title": c["title"], "state": c["state"],
                                                "body": (c.get("body") or "")[:300]} for c in cands]})
            user = json.dumps({"meeting": {"title": title, "tldr": summary.get("tldr", "")},
                               "action_items": listing}, ensure_ascii=False)
            out, _ = chat_json(self.s, DECIDE_SYSTEM, user, DECIDE_SCHEMA, timeout=900)
            by_item = {d["item"]: d for d in out.get("decisions", [])}
            known_numbers = {c["number"] for x in listing for c in x["candidates"]}
            for i, a in enumerate(new_items):
                d = by_item.get(i) or {"action": "log_only", "rationale": "no decision returned"}
                body_tail = (f"\n\nOwner: {a['owner'] or 'not set'} · Due: {a['due'] or 'not set'}\n"
                             f"Meeting: [{title}]({link}#a-{a['key']}) (aibox1, VPN only)\n\n{marker(mid, a['key'])}")
                if d["action"] == "comment" and d.get("issue_number") in known_numbers:
                    issue = next(c for c in issues if c["number"] == d["issue_number"])
                    payload = {"repo": self.s.github_repo, "issue_number": issue["number"],
                               "issue_title": issue["title"], "issue_url": issue["html_url"],
                               "body": (d.get("body") or a["text"]).strip() + body_tail, "item": a}
                    pid = self._add(mid, a["key"], "github_comment", payload, d.get("rationale"))
                elif d["action"] == "new_issue":
                    payload = {"repo": self.s.github_repo, "title": (d.get("title") or a["text"])[:120],
                               "body": (d.get("body") or a["text"]).strip() + body_tail, "item": a}
                    pid = self._add(mid, a["key"], "github_issue", payload, d.get("rationale"))
                else:
                    continue
                if pid:
                    created.append(pid)
        if meta.get("storage") == "drive" and "drive" in self.s.followup_targets:
            # The person chose Drive for this meeting: that is the approval.
            version = summary.get("version", 1)
            settings = self.store.settings(meta.get("owner_id"))
            store_key = f"drive-store-v{version}"
            if not self._known(mid, store_key):
                files = [name for name, _ in self.store.drive_files(mid)]
                payload = {"folder": settings["drive_folder"], "subfolder": self.store.drive_folder_name(mid),
                           "files": files, "meeting_id": mid, "meeting_url": link, "summary_version": version}
                pid = self._add(mid, store_key, "drive_store", payload,
                                "This meeting is set to Google Drive: its files go there.", approved=True)
                if pid:
                    created.append(pid)
            log_key = f"meeting-log-v{version}"
            if not self._known(mid, log_key):
                payload = {"doc": self.s.drive_log_doc, "folder": settings["drive_folder"], "meeting_id": mid,
                           "meeting_url": link, "summary_version": version,
                           "markdown": self._log_entry(meta, summary, actions, link)}
                pid = self._add(mid, log_key, "drive_log", payload,
                                "Every meeting stored in Drive gets an entry in the running meeting log.", approved=True)
                if pid:
                    created.append(pid)
        return created

    def _log_entry(self, meta, summary, actions, link):
        date = (meta.get("started_at") or meta.get("created_at") or "")[:10]
        lines = [f"## {date} · {meta.get('title') or summary.get('title') or meta['id']}", "",
                 summary.get("tldr") or "", ""]
        if summary.get("decisions"):
            lines += ["Decisions:"] + [f"- {d}" for d in summary["decisions"]] + [""]
        if actions:
            lines += ["Action items:"]
            for a in actions:
                extra = ", ".join(x for x in (a["owner"], a["due"] and f"due {a['due']}") if x)
                lines.append(f"- {a['text']}" + (f" ({extra})" if extra else ""))
            lines.append("")
        lines.append(f"Details on aibox1 (VPN only): {link}")
        return "\n".join(lines)

    # -- deciding and executing --------------------------------------------------

    def decide(self, pid, approve, who="cli"):
        p = self.db.one("SELECT * FROM proposals WHERE id = ?", pid)
        if not p:
            return {"id": pid, "error": "no such proposal"}
        if p["state"] != "proposed":
            return {"id": pid, "state": p["state"], "note": "already decided"}
        if approve and not self.enabled(p["kind"]):
            return {"id": pid, "state": p["state"], "error": f"{p['kind']} is switched off (FOLLOWUP_TARGETS)"}
        state = "approved" if approve else "rejected"
        self.db.run("UPDATE proposals SET state = ?, decided_at = ?, result = ? WHERE id = ? AND state = 'proposed'",
                    state, now_iso(), json.dumps({"decided_by": who}), pid)
        if approve:
            return self.execute(pid)
        return {"id": pid, "state": state}

    def execute(self, pid):
        # Serialized across processes (web approve vs. the hourly timer); the
        # mapping check below then makes a second call a no-op.
        with self._lock():
            return self._execute(pid)

    def _execute(self, pid):
        p = self.list_one(pid)
        if p["state"] != "approved":
            return {"id": pid, "state": p["state"]}
        mid, key, payload = p["meeting_id"], p["item_key"], p["payload"]
        done = self.db.one("SELECT * FROM mappings WHERE meeting_id = ? AND item_key = ?", mid, key)
        if done:
            return self._executed(pid, done["target_kind"], done["target"], note="already mapped")
        direct = self.executor == "direct" and (
            self.drive.available() if p["kind"].startswith("drive_") else self.github.available())
        if not direct:
            write_json(self.s.outbox_dir / f"{pid}.json", {**p, "state": "approved"})
            return {"id": pid, "state": "approved", "outbox": True}
        try:
            mark = marker(mid, key)
            if p["kind"] == "drive_store":
                url = self.drive.store(payload["folder"], payload["subfolder"], self.store.drive_files(mid))
            elif p["kind"] == "drive_log":
                url = self.drive.append(payload["folder"], payload["doc"], payload["markdown"])
            elif p["kind"] == "github_comment":
                url = self.github.find_marker(mark, payload["issue_number"]) or \
                    self.github.comment(payload["issue_number"], payload["body"])
            else:
                url = self.github.find_marker(mark) or self.github.create_issue(payload["title"], payload["body"])
        except httpx.HTTPError as e:
            self.db.run("UPDATE proposals SET error = ? WHERE id = ?", str(e)[:300], pid)
            return {"id": pid, "state": "approved", "error": str(e)[:300]}
        return self._executed(pid, p["kind"], url)

    def ack(self, pid, url, note=None, who="agent"):
        """The Command Center agent reports that it carried out a proposal."""
        p = self.db.one("SELECT * FROM proposals WHERE id = ?", pid)
        if not p:
            return {"id": pid, "error": "no such proposal"}
        if p["state"] == "executed":
            return {"id": pid, "state": "executed", "note": "already acknowledged"}
        if p["state"] != "approved":
            return {"id": pid, "error": f"proposal is {p['state']}, not approved"}
        res = self._executed(pid, p["kind"], url, note=note, who=who)
        (self.s.outbox_dir / f"{pid}.json").unlink(missing_ok=True)
        return res

    def _executed(self, pid, kind, target, note=None, who=None):
        p = self.db.one("SELECT * FROM proposals WHERE id = ?", pid)
        if kind == "drive_store":
            self.db.run("UPDATE meetings SET drive_url = ? WHERE id = ?", target, p["meeting_id"])
        self.db.run("INSERT OR IGNORE INTO mappings (meeting_id, item_key, target_kind, target, proposal_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)", p["meeting_id"], p["item_key"], kind, target, pid, now_iso())
        prev = json.loads(p["result"]) if p["result"] else {}
        result = {**prev, "url": target, **({"note": note} if note else {}), **({"acked_by": who} if who else {})}
        self.db.run("UPDATE proposals SET state = 'executed', executed_at = ?, result = ?, error = NULL WHERE id = ?",
                    now_iso(), json.dumps(result), pid)
        return {"id": pid, "state": "executed", "url": target}

    def list_one(self, pid):
        rows = [p for p in self.list() if p["id"] == pid]
        return rows[0] if rows else {"id": pid, "state": "missing"}

    def run(self, auto=None, meeting_ids=None):
        """Propose for new or updated summaries, then execute what is approved."""
        auto = self.s.followup_auto if auto is None else auto
        report = self.propose(meeting_ids)
        report["mode"] = "auto" if auto else "dry-run"
        report["targets"] = sorted(self.s.followup_targets)
        if auto:
            for pid in report["proposed"]:
                self.db.run("UPDATE proposals SET state = 'approved', decided_at = ?, result = ? WHERE id = ?"
                            " AND state = 'proposed'", now_iso(), json.dumps({"decided_by": "auto"}), pid)
        report["executed"] = [self.execute(p["id"]) for p in self.list(state="approved")]
        report["pending_review"] = len(self.list(state="proposed"))
        return report


def main(argv=None):
    from .config import Settings
    from .db import DB
    from .store import Store

    ap = argparse.ArgumentParser(prog="python -m recorder.followup", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["run", "list", "approve", "reject", "outbox", "ack"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--meeting", action="append")
    ap.add_argument("--state")
    ap.add_argument("--url")
    ap.add_argument("--note")
    ap.add_argument("--json-in", help="read options as JSON from this file, - for stdin")
    a = ap.parse_args(argv)
    if a.json_in:
        opts = json.load(sys.stdin if a.json_in == "-" else open(a.json_in))
        a.auto = opts.get("auto", a.auto)
        a.meeting = opts.get("meetings", a.meeting)
        a.ids = opts.get("ids", a.ids)
        a.url = opts.get("url", a.url)
        a.note = opts.get("note", a.note)
    s = Settings()
    s.ensure_dirs()
    db = DB(s.db_path)
    f = Followup(s, db, Store(s, db))
    if a.command == "run":
        out = f.run(auto=True if a.auto else None, meeting_ids=a.meeting)
    elif a.command == "list":
        out = f.list(state=a.state, meeting_id=(a.meeting or [None])[0])
    elif a.command in ("approve", "reject"):
        out = [f.decide(pid, approve=a.command == "approve", who="cli") for pid in a.ids]
    elif a.command == "outbox":
        out = f.outbox()
    else:
        if len(a.ids) != 1 or not a.url:
            ap.error("ack needs one proposal id and --url")
        out = f.ack(a.ids[0], a.url, a.note, who="cli")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
