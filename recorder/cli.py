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
from .db import DB
from .engines import NAMES, Engines
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
    sub.add_parser("meetings")
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
    db = DB(s.db_path)
    out = None
    if a.cmd == "token":
        if a.action == "add":
            print(create_token(db, a.name, a.role))
            return
        if a.action == "list":
            out = db.all("SELECT id, name, role, created_at, last_used_at, revoked_at FROM tokens ORDER BY created_at")
        else:
            out = {"revoked": db.run("UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                                     now_iso(), a.id)}
    elif a.cmd == "meetings":
        out = Store(s, db).list()
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
