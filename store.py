"""SQLite storage: plays from the Spotify export, a key/value table, and an API response cache."""
import json
import os
import sqlite3
import time
from datetime import datetime

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


def parse_plays(records):
    """Plays from either Spotify export: extended history (Streaming_History_Audio_*.json, `ts`) or standard
    account data (StreamingHistory_music_*.json, `endTime` in UTC to the minute).
    Skips podcasts, plays under 30s and malformed rows (one bad timestamp would break every trend chart)."""
    rows = []
    for r in records if isinstance(records, list) else []:
        if not isinstance(r, dict):
            continue
        if "endTime" in r:
            artist, track, ms = r.get("artistName"), r.get("trackName"), r.get("msPlayed")
            ts = str(r["endTime"]).replace(" ", "T") + ":00Z"
        else:
            artist, track, ms = r.get("master_metadata_album_artist_name"), r.get("master_metadata_track_name"), r.get("ms_played")
            ts = str(r.get("ts"))
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if artist and isinstance(ms, (int, float)) and ms >= 30_000:
            rows.append((ts, artist, track, int(ms), int(bool(r.get("skipped")))))
    return rows


def add_plays(records):
    """Insert plays from a Spotify export file. Returns how many were new."""
    rows = parse_plays(records)
    with db() as c:
        before = c.total_changes
        c.executemany("INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?)", rows)
        return c.total_changes - before


def norm(name):
    return (name or "").strip().lower()
