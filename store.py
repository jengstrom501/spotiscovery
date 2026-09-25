"""SQLite storage: plays from the Spotify export, a key/value table, and an API response cache."""
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime

DB_PATH = os.environ.get("DEEP_CUTS_DB", os.path.join(os.path.dirname(__file__), "deep_cuts.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
  ts TEXT, artist TEXT, track TEXT, ms INTEGER, skipped INTEGER,
  UNIQUE(ts, artist, track)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, at REAL);
CREATE TABLE IF NOT EXISTS not_music (artist TEXT PRIMARY KEY, reason TEXT, hidden INTEGER);
"""
# Every query over plays filters with this, so hidden podcasts / sleep sounds never reach a chart or a seed.
MUSIC = "artist NOT IN (SELECT artist FROM not_music WHERE hidden = 1)"

# Track titles: broad words, but only counted with long plays and for most of an artist's plays.
NOISE = re.compile(r"\b(sleep|white noise|brown noise|pink noise|rain sounds?|thunder(storm)?s?|nature sounds|"
                   r"ocean waves|meditation|guided|asmr|binaural|relaxation|podcast|audiobook)\b", re.I)
# Artist names: specific phrases only, so bands like Sleep or Guided By Voices aren't caught.
NOISE_ARTIST = re.compile(r"\b(white noise|brown noise|pink noise|rain sounds|nature sounds|sleep sounds|sleep music|"
                          r"meditation|asmr|relax(ation|ing)?|binaural|podcast)\b", re.I)
CHAPTER = re.compile(r"^(chapter|kapitel|cap[ií]tulo|chapitre|part|episode|ep\.?)\s*\d+", re.I)


# Progress of long-running work, read by GET /api/progress so the page can show a real bar.
# ponytail: in-process dict keyed by task name, fine for one local user; nothing survives a restart.
PROGRESS = {}


def progress(task, done, total, label):
    PROGRESS[task] = {"done": done, "total": total, "label": label}


def track(task, items, label):
    """Yield items while publishing done/total under task. The entry is removed when the loop ends or breaks."""
    items = list(items)
    try:
        for i, it in enumerate(items):
            progress(task, i, len(items), label)
            yield it
    finally:
        PROGRESS.pop(task, None)


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
    """Return the cached value for k, or call fetch() and cache it. Only caches non-None.
    max_age can be a function of the cached value, so a miss can expire sooner than a hit."""
    with db() as c:
        row = c.execute("SELECT v, at FROM cache WHERE k=?", (k,)).fetchone()
    if row:
        v = json.loads(row[0])
        if time.time() - row[1] < (max_age(v) if callable(max_age) else max_age):
            return v
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


def flag_not_music():
    """Hide artists that look like podcasts, audiobooks or sleep sounds sold as music tracks. Duration alone would
    catch real ambient artists (16-min drones), so it needs a 30-min median; keywords also need plays over 5 min
    ("Thunder" on repeat is a pop song), and title matches must cover most of the artist's plays.
    INSERT OR IGNORE: a flag the user reversed stays reversed."""
    plays = {}
    with db() as c:
        for artist, track, ms in c.execute("SELECT artist, track, ms FROM plays"):
            plays.setdefault(artist, []).append((track or "", ms))
    flags = []
    for artist, rows in plays.items():
        mid = sorted(ms for _, ms in rows)[len(rows) // 2]
        if mid > 30 * 60_000:
            flags.append((artist, f"Long plays (typically {round(mid / 60_000)} min): likely a podcast or sleep sounds"))
        elif mid > 5 * 60_000 and (NOISE_ARTIST.search(artist) or sum(bool(NOISE.search(t)) for t, _ in rows) > len(rows) / 2):
            flags.append((artist, "Sleep, noise or meditation tracks"))
        elif sum(bool(CHAPTER.match(t)) for t, _ in rows) > len(rows) / 2:
            flags.append((artist, "Numbered chapters or episodes: likely an audiobook or podcast"))
    with db() as c:
        c.executemany("INSERT OR IGNORE INTO not_music VALUES (?, ?, 1)", flags)


def set_not_music(artist, hidden):
    with db() as c:
        c.execute("INSERT INTO not_music VALUES (?, 'Hidden by you', ?) "
                  "ON CONFLICT(artist) DO UPDATE SET hidden = excluded.hidden", (artist, int(hidden)))


def norm(name):
    return (name or "").strip().lower()


def norm_loose(name):
    """For matching one service's artist name to another's: "The Beyoncé" == "beyonce"."""
    n = "".join(ch for ch in unicodedata.normalize("NFKD", norm(name)) if not unicodedata.combining(ch))
    return n[4:] if n.startswith("the ") else n


def track_key(title):
    """Same song across versions: "Nights (Remastered)", "Nights - Live at X" and "nights" all -> "nights"."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", norm_loose(title)).split(" - ")[0]
    return re.sub(r"[\W_]+", "", t)


def squash(name):
    """Spelling-proof key for "is this the same artist": "Travi$ Scott" == "travis scott", "Lil' Wayne" == "lil wayne"."""
    return re.sub(r"[\W_]+", "", norm_loose(name).replace("$", "s"))
