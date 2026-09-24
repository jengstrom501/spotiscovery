"""Spotify Web API: PKCE login and a snapshot of your top artists, recent plays, and library."""
import base64
import hashlib
import os
import secrets
import time
from urllib.parse import quote, urlencode

import requests

import store

AUTH = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-top-read user-read-recently-played user-library-read user-follow-read"
RANGES = ["short_term", "medium_term", "long_term"]


def redirect_uri():
    return os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")


def login_url():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    store.put("pkce", {"verifier": verifier, "state": state})
    return AUTH + "?" + urlencode({
        "client_id": os.environ["SPOTIFY_CLIENT_ID"], "response_type": "code",
        "redirect_uri": redirect_uri(), "code_challenge_method": "S256",
        "code_challenge": challenge, "scope": SCOPES, "state": state})


def finish_login(code, state):
    pkce = store.get("pkce") or {}
    if not state or state != pkce.get("state"):
        raise ValueError("OAuth state mismatch, try connecting again")
    _save_token(requests.post(TOKEN, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
        "client_id": os.environ["SPOTIFY_CLIENT_ID"], "code_verifier": pkce["verifier"]}, timeout=20))


def _save_token(resp):
    resp.raise_for_status()
    t = resp.json()
    old = store.get("token") or {}
    store.put("token", {"access": t["access_token"],
                        "refresh": t.get("refresh_token", old.get("refresh")),
                        "expires": time.time() + t["expires_in"] - 60})


def connected():
    return bool(store.get("token"))


def _access():
    t = store.get("token")
    if not t:
        raise RuntimeError("Spotify not connected")
    if time.time() > t["expires"]:
        _save_token(requests.post(TOKEN, data={
            "grant_type": "refresh_token", "refresh_token": t["refresh"],
            "client_id": os.environ["SPOTIFY_CLIENT_ID"]}, timeout=20))
        t = store.get("token")
    return t["access"]


def _get(path_or_url, **params):
    url = path_or_url if path_or_url.startswith("http") else API + path_or_url
    r = requests.get(url, params=params, headers={"Authorization": "Bearer " + _access()}, timeout=20)
    r.raise_for_status()
    return r.json()


def _safe(fn, default):
    """One missing scope or retired endpoint shouldn't sink the whole sync."""
    try:
        return fn()
    except requests.HTTPError as e:
        print(f"spotify: {e}")
        return default


def sync(max_saved=2000):
    """Pull everything we use into kv['spotify'] and return it."""
    snap = {"top": {}, "recent": [], "followed": [], "saved_artists": [], "at": time.time()}
    for rng in RANGES:
        items = _safe(lambda: _get("/me/top/artists", time_range=rng, limit=50)["items"], [])
        snap["top"][rng] = [a["name"] for a in items]
    snap["recent"] = [
        {"artist": i["track"]["artists"][0]["name"], "track": i["track"]["name"], "ts": i["played_at"]}
        for i in _safe(lambda: _get("/me/player/recently-played", limit=50)["items"], [])]

    def followed():
        out, url = [], API + "/me/following?type=artist&limit=50"
        while url:
            page = _get(url)["artists"]
            out += [a["name"] for a in page["items"]]
            url = page.get("next")
        return out
    snap["followed"] = _safe(followed, [])

    def saved():
        names, url = set(), API + "/me/tracks?limit=50"
        while url and len(names) < max_saved:  # ponytail: capped page walk, huge libraries only lose the tail
            page = _get(url)
            for it in page["items"]:
                names.update(a["name"] for a in it["track"]["artists"])
            url = page.get("next")
        return sorted(names)
    snap["saved_artists"] = _safe(saved, [])
    store.put("spotify", snap)
    return snap


def artist_card(name):
    """Image + link for a recommendation. Falls back to a search link if the API call fails."""
    fallback = {"image": None, "url": "https://open.spotify.com/search/" + quote(name)}
    if not connected():
        return fallback

    def fetch():
        items = _get("/search", q=name, type="artist", limit=1)["artists"]["items"]
        if not items or store.norm(items[0]["name"]) != store.norm(name):
            return fallback
        a = items[0]
        return {"image": a["images"][-1]["url"] if a.get("images") else None,
                "url": a["external_urls"]["spotify"]}
    try:
        return store.cached("spotify:card:" + store.norm(name), fetch)
    except (requests.RequestException, RuntimeError):
        return fallback
