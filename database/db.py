"""SQLite connection management, schema migrations and backups.

The database is never reset: migrations only ever add tables/columns.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,              -- Moodle course id
    name TEXT NOT NULL,
    short_name TEXT,
    url TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cmid INTEGER,                        -- Moodle course-module id
    course_id INTEGER REFERENCES courses(id),
    modname TEXT NOT NULL,               -- assign, quiz, forum, resource, ...
    category TEXT NOT NULL,              -- assignment, quiz, lab, task, resource, forum, other
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    section TEXT,
    opens_at TEXT,                       -- ISO-8601 with timezone
    due_at TEXT,
    cutoff_at TEXT,
    closes_at TEXT,
    status TEXT,                         -- normalised: submitted, not_submitted, draft, attempted, not_attempted, not_required, unknown
    status_text TEXT,                    -- raw status shown by Moodle
    grading_status TEXT,
    grade TEXT,
    attempts_allowed TEXT,
    time_limit TEXT,
    time_remaining TEXT,
    requirements TEXT,
    content_hash TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_checked TEXT,
    last_changed TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_course ON activities(course_id);
CREATE INDEX IF NOT EXISTS idx_activities_due ON activities(due_at);

CREATE TABLE IF NOT EXISTS deadline_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER REFERENCES activities(id),
    course_id INTEGER REFERENCES courses(id),
    filename TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    size_bytes INTEGER,
    mime_type TEXT,
    local_path TEXT,
    sha256 TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    downloaded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_activity ON files(activity_id);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id INTEGER,
    course_id INTEGER REFERENCES courses(id),
    forum_name TEXT,
    subject TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author TEXT,
    posted TEXT,
    excerpt TEXT,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                  -- new_activity, deadline_changed, new_file, announcement, status_changed
    activity_id INTEGER,
    course_id INTEGER,
    title TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    notified_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,     -- guarantees a notification is sent at most once
    kind TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL,                -- sent, failed
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    courses INTEGER,
    activities INTEGER,
    new_activities INTEGER,
    changes INTEGER,
    error TEXT
);
"""


class Database:
    """A small thread-safe wrapper: one SQLite connection per thread."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self.migrate()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 30000")
            if str(self.path) != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection()
        with self._write_lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.connection().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connection().execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def migrate(self) -> None:
        conn = self.connection()
        with self._write_lock:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()

    def backup(self, backup_dir: Path, keep: int = 7) -> Path:
        """Write a consistent copy using SQLite's online backup API and prune old copies."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"agent-{datetime.now():%Y%m%d-%H%M%S}.db"
        dest = sqlite3.connect(str(target))
        try:
            self.connection().backup(dest)
        finally:
            dest.close()
        backups = sorted(backup_dir.glob("agent-*.db"))
        for old in backups[:-keep] if keep > 0 else []:
            try:
                old.unlink()
            except OSError:
                log.warning("Could not delete old backup %s", old)
        log.info("Database backup written to %s", target)
        return target

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
