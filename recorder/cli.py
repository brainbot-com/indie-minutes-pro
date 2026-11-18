"""Admin CLI, run inside the recorder container:

  docker compose exec recorder python -m recorder.cli token add --name "Luis phone" [--role device|agent|admin]
  docker compose exec recorder python -m recorder.cli token list
  docker compose exec recorder python -m recorder.cli token revoke TOKEN_ID
  docker compose exec recorder python -m recorder.cli meetings
  docker compose exec recorder python -m recorder.cli reprocess MEETING_ID [--engine B] [--summary-only]
  docker compose exec recorder python -m recorder.cli transcribe FILE --engine A-iq4 --out DIR

`transcribe` runs one engine on one file without creating a meeting (the
benchmark uses it). Everything prints JSON except `token add`, which prints
only the new token so it can be piped into a file."""
import argparse
import json
import sys
import time
from pathlib import Path

from . import audio
from .api import create_token
from .config import Settings
from .db import open_index
from .engines import NAMES, Engines
from .people import add_person
from .store import Store
from .util import now_iso, write_json


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m recorder.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token")
    t.add_argument("action", choices=["add", "list", "revoke"])
    t.add_argument("id", nargs="?")
    t.add_argument("--name", default="device")
    t.add_argument("--role", default="device", choices=["device", "agent", "admin"])
    t.add_argument("--user", help="the person the token belongs to (user id)")
    t.add_argument("--trusts-identity", action="store_true",
                   help="brainbot:agent command's service token: speaks for the person in X-Indie-User")
    u = sub.add_parser("user", help="people (accounts come from brainbot:agent command)")
    u.add_argument("action", choices=["add", "list", "claim", "remove"])
    u.add_argument("id", nargs="?", help="claim, remove: the person's user id")
    u.add_argument("--ext-id", help="the person's user id in brainbot:agent command")
    u.add_argument("--email")
    u.add_argument("--name")
    u.add_argument("--token", action="append", default=[],
                   help="claim: give this token, and the meetings its phone recorded, to the person")
    sub.add_parser("encrypt", help="encrypt the files of meetings from before encryption at rest (checked, in place)")
    c = sub.add_parser("connect", help="a connect code for a person's next phone (once, within 15 minutes)")
    c.add_argument("--user", help="the person (user id); without one, a phone of nobody in particular")
    sub.add_parser("meetings")
    d = sub.add_parser("delete", help="delete meetings with all their files, e.g. test meetings")
    d.add_argument("meeting_ids", nargs="+")
    r = sub.add_parser("reprocess")
    r.add_argument("meeting_id")
    r.add_argument("--engine", choices=NAMES)
    r.add_argument("--summary-only", action="store_true")
    tr = sub.add_parser("transcribe")
    tr.add_argument("file", type=Path)
    tr.add_argument("--engine", required=True, choices=NAMES)
    tr.add_argument("--out", type=Path, required=True)
    tr.add_argument("--language")
    tr.add_argument("--cpu", action="store_true", help="run on the CPU (to prove the GPU path is really used)")
    a = ap.parse_args(argv)

    s = Settings()
    s.ensure_dirs()
    db = open_index(s)
    out = None
    if a.cmd == "token":
        if a.action == "add":
            print(create_token(db, a.name, a.role, user_id=a.user, trusts_identity=a.trusts_identity))
            return
        if a.action == "list":
            out = db.all("SELECT id, name, role, created_at, last_used_at, revoked_at FROM tokens ORDER BY created_at")
        else:
            out = {"revoked": db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                     now_iso(), a.id)}
    elif a.cmd == "user":
        if a.action == "add":
            out = add_person(db, ext_id=a.ext_id, email=a.email, name=a.name)
        elif a.action == "list":
            out = db.all("SELECT * FROM users ORDER BY created_at")
        elif a.action == "remove":
            # a person who left, or a test person: their phones stop working; meetings stay
            if not a.id or not db.one("SELECT 1 FROM users WHERE id = ?", a.id):
                raise SystemExit("remove needs an existing user id")
            owned = db.one("SELECT COUNT(*) AS n FROM meetings WHERE owner_id = ?", a.id)["n"]
            if owned:
                raise SystemExit(f"{owned} meeting(s) still belong to this person: delete them or claim them for someone else first")
            out = {"tokens_revoked": db.run("UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", now_iso(), a.id),
                   "settings": db.run("DELETE FROM user_settings WHERE user_id = ?", a.id),
                   "removed": db.run("DELETE FROM users WHERE id = ?", a.id)}
        else:
            if not a.id or not db.one("SELECT 1 FROM users WHERE id = ?", a.id):
                raise SystemExit("claim needs an existing user id")
            out = {"tokens": 0, "meetings": 0}
            for tid in a.token:
                out["tokens"] += db.run("UPDATE tokens SET user_id = ? WHERE id = ?", a.id, tid)
                out["meetings"] += db.run(
                    "UPDATE meetings SET owner_id = ? WHERE recording_id IN"
                    " (SELECT id FROM recordings WHERE token_id = ?)", a.id, tid)
    elif a.cmd == "encrypt":
        from . import vault
        v = vault.VAULT
        if not v.on:
            raise SystemExit("encryption at rest is off (ENCRYPT_AT_REST, DATA_KEY_FILE)")
        out = {"encrypted": 0, "already": 0, "failed": []}
        for root in (s.meetings_dir, s.uploads_dir):
            for f in sorted(root.rglob("*")) if root.exists() else []:
                if not f.is_file() or f.name.endswith(".sealing"):
                    continue
                try:
                    out["encrypted" if v.seal_in_place(f) else "already"] += 1
                except Exception as e:
                    out["failed"].append(f"{f}: {e}")
    elif a.cmd == "connect":
        from .connect import Codes, shown
        if a.user and not db.one("SELECT 1 FROM users WHERE id = ?", a.user):
            raise SystemExit("no such person")
        code, expires = Codes(db).create(a.user)
        out = {"code": shown(code), "url": f"{s.connect_url}#{code}", "expires_at": expires}
    elif a.cmd == "meetings":
        out = Store(s, db).list()
    elif a.cmd == "delete":
        store, out = Store(s, db), {}
        for mid in a.meeting_ids:
            if not store.meta(mid):
                out[mid] = "no such meeting"
            elif db.one("SELECT 1 FROM jobs WHERE meeting_id = ? AND state = 'running'", mid):
                out[mid] = "a job is running for it, try again later"
            else:
                store.delete(mid)
                out[mid] = "deleted"
    elif a.cmd == "reprocess":
        kind = "summarize" if a.summary_only else "transcribe"
        db.run("INSERT INTO jobs (meeting_id, kind, engine, created_at) VALUES (?, ?, ?, ?)",
               a.meeting_id, kind, a.engine, now_iso())
        out = {"queued": a.meeting_id, "kind": kind, "engine": a.engine,
               "note": "the running service picks it up within a few seconds"}
    elif a.cmd == "transcribe":
        a.out.mkdir(parents=True, exist_ok=True)
        wav = a.out / "audio.wav"
        if not wav.exists():
            audio.to_wav16k(a.file, wav)
        t0 = time.monotonic()
        result = Engines(s).get(a.engine).transcribe(wav, a.out, language=a.language,
                                                     log=lambda m: print(m, file=sys.stderr, flush=True),
                                                     **({"device": "cpu"} if a.cpu else {}))
        result.setdefault("timings", {})["job_wall_s"] = round(time.monotonic() - t0, 2)
        write_json(a.out / "transcript.json", result)
        out = {"out": str(a.out / "transcript.json"), "words": len(result["words"]),
               "speakers": len(result["speakers"]), "timings": result.get("timings"),
               "resources": result.get("resources")}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
