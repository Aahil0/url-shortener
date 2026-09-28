"""SQLite access. One short-lived connection per unit of work."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,      -- UNIQUE also creates the lookup index
    original_url TEXT NOT NULL,
    created_at   TEXT NOT NULL,             -- ISO-8601 UTC, sortable as text
    expires_at   TEXT
);
CREATE TABLE IF NOT EXISTS clicks (
    id            INTEGER PRIMARY KEY,
    link_id       INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    clicked_at    TEXT NOT NULL,
    referrer_host TEXT NOT NULL,            -- 'direct' when no Referer header
    device        TEXT NOT NULL,            -- desktop | mobile | tablet | bot
    browser       TEXT NOT NULL,
    visitor_hash  TEXT NOT NULL             -- salted daily hash, never a raw IP
);
CREATE INDEX IF NOT EXISTS idx_clicks_link_time ON clicks(link_id, clicked_at);
"""


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        conn.executescript(SCHEMA)


@contextmanager
def connect(path: str):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
