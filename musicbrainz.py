"""MusicBrainz (free, no key): when an artist first released something. Used for "you found them early".
Their rules: identify yourself in the User-Agent and stay under one request per second."""
import threading
import time

import requests

import store

API = "https://musicbrainz.org/ws/2"
UA = {"User-Agent": "Spotiscovery/1.0 (local listening-history app)"}
_lock = threading.Lock()
_last = [0.0]


def _get(path, **params):
    with _lock:  # ponytail: one global throttle at 1 req/s, MusicBrainz's published limit
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    for attempt in range(3):  # 503s come when the shared server is busy; a short wait usually clears them
        r = requests.get(f"{API}/{path}", params={"fmt": "json", **params}, headers=UA, timeout=20)
        if r.status_code != 503 or attempt == 2:
            break
        time.sleep(3)
    r.raise_for_status()
    return r.json()


def _date(s):
    """"2015-02-10", "2015-02" or "2015" -> "2015-02-10" (missing parts default to the first), or None."""
    y, m, d = ((s or "").split("-") + ["01", "01"])[:3]
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}" if y.isdigit() and len(y) == 4 else None


def _key(name):
    return "mb:debut:" + store.norm(name)


def cached_debut(name):
    """(known, date): whether this artist was looked up before, and the date if one was found. Never hits the network."""
    row = store.peek(_key(name), 90 * 86400)
    return (row is not None, row["date"] if row else None)


def debut(name):
    """Earliest release date (YYYY-MM-DD) of the artist MusicBrainz means by this exact name, or None when the
    match isn't clear. Only a top hit with a perfect score and the same name (accents and "The" ignored) counts,
    so a namesake can't give a wrong date. Cached for 90 days, misses included."""
    def fetch():
        hits = _get("artist", query=f'artist:"{name}"', limit=3).get("artists", [])
        best = next((h for h in hits if h.get("score", 0) >= 98 and store.norm_loose(h["name"]) == store.norm_loose(name)), None)
        if not best:
            return {"date": None}
        groups = _get("release-group", artist=best["id"], limit=100).get("release-groups", [])
        dates = [d for d in (_date(g.get("first-release-date")) for g in groups) if d]
        return {"date": min(dates) if dates else None}
    return store.cached(_key(name), fetch, max_age=90 * 86400)["date"]
