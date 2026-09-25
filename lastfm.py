"""Cached Last.fm client. Similarity and tags come from here, since Spotify removed those APIs."""
import os
import threading
import time

import requests

import store

API = "https://ws.audioscrobbler.com/2.0/"
_lock = threading.Lock()
_last = [0.0]


def _call(method, **params):
    """Exact name first, autocorrect only if Last.fm doesn't know it: autocorrect can swap a real artist for a
    namesake stub ("Parcels", 821k listeners -> "The Parcels", 3k), which poisons tags, similar and listeners."""
    data = _raw(method, autocorrect=0, **params)
    return data if data or "artist" not in params else _raw(method, autocorrect=1, **params)


def _raw(method, **params):
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        raise RuntimeError("Add a Last.fm API key in Setup.")
    with _lock:  # ponytail: global throttle ~4 req/s, Last.fm's documented limit is 5/s
        wait = 0.25 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = requests.get(API, params={"method": method, "api_key": key, "format": "json", **params}, timeout=20)
    data = r.json() if r.content else {}
    if "error" in data:
        if data["error"] == 6:  # artist/tag not found
            return {}
        raise RuntimeError(f"Last.fm {method}: {data.get('message')}")
    return data


def check(key):
    """Live validity check for a candidate API key, bypassing the cache (a cached Radiohead lookup made under
    a previous key would otherwise mask a bad new one). True/False on a definitive answer, None if Last.fm
    couldn't be reached (a save shouldn't be blocked by a network hiccup)."""
    old = os.environ.get("LASTFM_API_KEY")
    os.environ["LASTFM_API_KEY"] = key
    try:
        _call("artist.getInfo", artist="Radiohead")
        return True
    except RuntimeError as e:
        return False if "Invalid API key" in str(e) else None
    except requests.RequestException:
        return None
    finally:
        if old is None:
            os.environ.pop("LASTFM_API_KEY", None)
        else:
            os.environ["LASTFM_API_KEY"] = old


def _key(*parts):
    return "lastfm2:" + ":".join(store.norm(str(p)) for p in parts)  # 2: entries cached before exact-name lookups


def similar(artist, limit=50):
    """[(name, match 0..1)]"""
    def fetch():
        d = _call("artist.getSimilar", artist=artist, limit=limit)
        return [(a["name"], float(a["match"])) for a in d.get("similarartists", {}).get("artist", [])]
    return store.cached(_key("similar", artist), fetch)


def top_tags(artist):
    """[(tag, count 0..100)] best first"""
    def fetch():
        d = _call("artist.getTopTags", artist=artist)
        return [(t["name"].lower(), int(t["count"])) for t in d.get("toptags", {}).get("tag", [])[:15]]
    return store.cached(_key("tags", artist), fetch)


def top_tracks(artist, limit=20):
    """[track name] most played on Last.fm first"""
    def fetch():
        d = _call("artist.getTopTracks", artist=artist, limit=limit)
        return [t["name"] for t in d.get("toptracks", {}).get("track", [])]
    return store.cached(_key("toptracks", artist, limit), fetch)


def info(artist):
    """{'name', 'listeners', 'tags', 'url'} or None if Last.fm doesn't know the artist."""
    def fetch():
        a = _call("artist.getInfo", artist=artist).get("artist")
        if not a:
            return {"missing": True}
        return {"name": a["name"], "listeners": int(a.get("stats", {}).get("listeners", 0)),
                "tags": [t["name"].lower() for t in a.get("tags", {}).get("tag", [])],
                "url": a.get("url")}
    v = store.cached(_key("info", artist), fetch)
    return None if v.get("missing") else v


def tag_artists(tag, limit=300):
    """Top artists for a tag, most representative first."""
    def fetch():
        d = _call("tag.getTopArtists", tag=tag, limit=limit)
        return [a["name"] for a in d.get("topartists", {}).get("artist", [])]
    return store.cached(_key("tagtop", tag, limit), fetch)
