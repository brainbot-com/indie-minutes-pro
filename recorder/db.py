"""SQLite index next to the meeting folders. One short-lived connection per call,
so the web app, the worker thread and the CLIs can share the file safely (WAL)."""
import sqlite3
from contextlib import contextmanager

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
]


class DB:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            for sql in MIGRATIONS:
                try:
                    con.execute(sql)
                except sqlite3.OperationalError:
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

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
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
