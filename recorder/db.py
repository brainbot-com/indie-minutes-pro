"""SQLite index next to the meeting folders. One short-lived connection per call,
so the web app, the worker thread and the CLIs can share the file safely (WAL).
With a key (encryption at rest) it is SQLCipher: the whole file is encrypted."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:            # a machine without the wheel can still use an index without a key
    sqlcipher = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('device', 'agent', 'admin')),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS recordings (
  id TEXT PRIMARY KEY,
  token_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  mime TEXT NOT NULL,
  title TEXT,
  language TEXT,
  started_at TEXT,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'uploading',
  total_chunks INTEGER,
  meeting_id TEXT,
  UNIQUE (token_id, client_id)
);
CREATE TABLE IF NOT EXISTS chunks (
  recording_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  received_at TEXT NOT NULL,
  PRIMARY KEY (recording_id, idx)
);
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  title TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  duration_s REAL,
  language TEXT,
  source TEXT,
  recording_id TEXT,
  state TEXT NOT NULL,
  engine TEXT,
  error TEXT,
  speakers INTEGER,
  tldr TEXT,
  summary_version INTEGER NOT NULL DEFAULT 0,
  summary_author TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  engine TEXT,
  state TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  rationale TEXT,
  state TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL,
  decided_at TEXT,
  executed_at TEXT,
  result TEXT,
  error TEXT,
  UNIQUE (meeting_id, item_key)
);
CREATE TABLE IF NOT EXISTS mappings (
  meeting_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  target_kind TEXT NOT NULL,
  target TEXT NOT NULL,
  proposal_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (meeting_id, item_key)
);
CREATE TABLE IF NOT EXISTS followup_seen (
  meeting_id TEXT PRIMARY KEY,
  summary_version INTEGER NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  ext_id TEXT UNIQUE,
  email TEXT,
  name TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS pair_codes (
  code_hash TEXT PRIMARY KEY,
  user_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  token_id TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state, id);
CREATE INDEX IF NOT EXISTS proposals_state ON proposals (state);
"""


# Columns added after the first release; ALTER fails harmlessly once they exist.
MIGRATIONS = [
    "ALTER TABLE recordings ADD COLUMN storage TEXT",
    "ALTER TABLE meetings ADD COLUMN storage TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE meetings ADD COLUMN drive_url TEXT",
    # people: a token belongs to a person, a meeting to the person who recorded it
    "ALTER TABLE tokens ADD COLUMN user_id TEXT",
    "ALTER TABLE tokens ADD COLUMN trusts_identity INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE meetings ADD COLUMN owner_id TEXT",
    "ALTER TABLE recordings ADD COLUMN seal_kid TEXT",
    "ALTER TABLE recordings ADD COLUMN seal_epk TEXT",
]


class DB:
    def __init__(self, path, key=None):
        self.path = str(path)
        self.key = key
        self.lib = sqlite3
        if key:
            if sqlcipher is None:
                raise RuntimeError("an encrypted index needs the sqlcipher3 module")
            self.lib = sqlcipher
            self._encrypt_plain_index()
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            for sql in MIGRATIONS:
                try:
                    con.execute(sql)
                except self.lib.OperationalError:
                    pass

    def setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", key)
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 key, str(value))

    def user_setting(self, user_id, key, default=None):
        """A person's own setting, else the box-wide one, else the default."""
        row = self.one("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", user_id, key) if user_id else None
        return row["value"] if row else self.setting(key, default)

    def set_user_setting(self, user_id, key, value):
        self.run("INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)"
                 " ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", user_id, key, str(value))

    def _encrypt_plain_index(self):
        """An index from before encryption at rest becomes SQLCipher, once; the
        copy is checked table by table before it replaces the plain file."""
        path = Path(self.path)
        if not path.exists():
            return
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return
        tmp = path.with_name(path.name + ".encrypting")
        tmp.unlink(missing_ok=True)
        con = sqlcipher.connect(self.path, isolation_level=None)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute(f"ATTACH DATABASE '{tmp}' AS enc KEY \"x'{self.key.hex()}'\"")
            con.execute("SELECT sqlcipher_export('enc')")
            tables = [r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")]
            for t in tables:
                a = con.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0]
                b = con.execute(f'SELECT COUNT(*) FROM enc."{t}"').fetchone()[0]
                if a != b:
                    raise RuntimeError(f"encrypting the index: table {t} has {a} rows, the copy {b}")
            con.execute("DETACH DATABASE enc")
        finally:
            con.close()
        os.replace(tmp, path)
        for side in ("-wal", "-shm"):
            Path(self.path + side).unlink(missing_ok=True)

    def export_plain(self, dst):
        """A plain copy of an encrypted index (recorder.cli decrypt, the way back)."""
        with self.connect() as con:
            con.execute(f"ATTACH DATABASE '{dst}' AS plain KEY ''")
            con.execute("SELECT sqlcipher_export('plain')")
            con.execute("DETACH DATABASE plain")

    @contextmanager
    def connect(self):
        con = self.lib.connect(self.path, timeout=30, isolation_level=None)
        if self.key:
            con.execute(f"PRAGMA key = \"x'{self.key.hex()}'\"")
        con.row_factory = self.lib.Row
        con.execute("PRAGMA busy_timeout=30000")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def tx(self):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    def all(self, sql, *args):
        with self.connect() as con:
            return [dict(r) for r in con.execute(sql, args)]

    def one(self, sql, *args):
        with self.connect() as con:
            row = con.execute(sql, args).fetchone()
            return dict(row) if row else None

    def run(self, sql, *args):
        with self.connect() as con:
            return con.execute(sql, args).rowcount


def open_index(settings):
    """The index for the service and the CLIs: loads the keys first (vault.py), so
    with encryption at rest it opens as SQLCipher (and an old plain one is converted)."""
    from . import vault
    v = vault.configure(settings)
    return DB(settings.db_path, key=v.subkey("index.sqlite") if v.on else None)
