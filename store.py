"""SQLite storage: plays from the Spotify export, a key/value table, and an API response cache."""
import json
import os
import sqlite3
import time

DB_PATH = os.environ.get("DEEP_CUTS_DB", os.path.join(os.path.dirname(__file__), "deep_cuts.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
  ts TEXT, artist TEXT, track TEXT, ms INTEGER, skipped INTEGER,
  UNIQUE(ts, artist, track)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, at REAL);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def get(k, default=None):
    with db() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(row[0]) if row else default


def put(k, v):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))


def cached(k, fetch, max_age=30 * 86400):
    """Return the cached value for k, or call fetch() and cache it. Only caches non-None."""
    with db() as c:
        row = c.execute("SELECT v, at FROM cache WHERE k=?", (k,)).fetchone()
    if row and time.time() - row[1] < max_age:
        return json.loads(row[0])
    v = fetch()
    if v is not None:
        with db() as c:
            c.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (k, json.dumps(v), time.time()))
    return v


def add_plays(records):
    """Insert Spotify Extended Streaming History records. Skips podcasts and plays under 30s."""
    rows = [
        (r["ts"], r["master_metadata_album_artist_name"], r.get("master_metadata_track_name"),
         r.get("ms_played", 0), int(bool(r.get("skipped"))))
        for r in records
        if r.get("master_metadata_album_artist_name") and r.get("ms_played", 0) >= 30_000
    ]
    with db() as c:
        before = c.total_changes
        c.executemany("INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?)", rows)
        return c.total_changes - before


def norm(name):
    return (name or "").strip().lower()
