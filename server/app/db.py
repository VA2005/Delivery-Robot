"""SQLite schema + helpers."""
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "navbot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,           -- student / lecturer / employee ID
    name            TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('recipient','staff','admin')),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS compartments (
    id    INTEGER PRIMARY KEY,                  -- 1 or 2
    state TEXT NOT NULL DEFAULT 'EMPTY'
          CHECK (state IN ('EMPTY','RESERVED','LOADED','UNLOCKED'))
);
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id TEXT NOT NULL REFERENCES users(id),
    compartment  INTEGER NOT NULL REFERENCES compartments(id),
    details      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'CREATED'
                 CHECK (status IN ('CREATED','LOADED','UNLOCKED','COMPLETED','CANCELLED')),
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS used_tokens (        -- anti-replay: one row per consumed QR token
    jti     TEXT PRIMARY KEY,
    used_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    user_id TEXT,
    action  TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail  TEXT NOT NULL DEFAULT ''
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)
        db.executemany("INSERT OR IGNORE INTO compartments(id) VALUES (?)", [(1,), (2,)])


def audit(db, user_id, action, success, detail=""):
    db.execute(
        "INSERT INTO audit_log(ts,user_id,action,success,detail) VALUES (?,?,?,?,?)",
        (time.time(), user_id, action, int(success), detail),
    )
