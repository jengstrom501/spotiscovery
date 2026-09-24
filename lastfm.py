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
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        raise RuntimeError("LASTFM_API_KEY is not set (see README)")
    with _lock:  # ponytail: global throttle ~4 req/s, Last.fm's documented limit is 5/s
        wait = 0.25 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = requests.get(API, params={"method": method, "api_key": key, "format": "json",
                                  "autocorrect": 1, **params}, timeout=20)
    data = r.json() if r.content else {}
    if "error" in data:
        if data["error"] == 6:  # artist/tag not found
            return {}
        raise RuntimeError(f"Last.fm {method}: {data.get('message')}")
    return data


def _key(*parts):
    return "lastfm:" + ":".join(store.norm(str(p)) for p in parts)


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
