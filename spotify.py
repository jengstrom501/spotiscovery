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
SCOPES = "user-top-read user-read-recently-played user-library-read user-follow-read playlist-modify-private"
PLAYLIST_SCOPE = "playlist-modify-private"
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
                        "scope": t.get("scope", old.get("scope", "")),
                        "expires": time.time() + t["expires_in"] - 60})


def connected():
    return bool(store.get("token"))


def can_playlist():
    """Tokens from before playlist export lack the scope; the page asks for a reconnect then."""
    return PLAYLIST_SCOPE in (store.get("token") or {}).get("scope", "").split()


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


def _post(path, body):
    r = requests.post(API + path, json=body, headers={"Authorization": "Bearer " + _access()}, timeout=20)
    if not r.ok:  # surface Spotify's own reason, not just "403 Client Error"
        try:
            reason = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            reason = r.reason
        raise RuntimeError(f"Spotify refused {path} ({r.status_code}): {reason}")
    return r.json()


def find_track(artist, track=None):
    """Spotify URI for a named track, or for the artist's most popular match when no track is given."""
    q = f'artist:"{artist}"' + (f' track:"{track}"' if track else "")
    items = _get("/search", q=q, type="track", limit=10)["tracks"]["items"]
    want = store.norm_loose(artist)
    hit = next((t for t in items if any(store.norm_loose(a["name"]) == want for a in t["artists"])), None)
    return hit and hit["uri"]


def create_playlist(name, items, description="Made with Spotiscovery"):
    """items: [{"artist", "track"?}]. Makes a private playlist and returns its link plus what couldn't be found."""
    uris, missing = [], []
    for it in store.track("playlist", items, "Finding each song on Spotify"):
        uri = find_track(it["artist"], it.get("track"))
        if uri and uri not in uris:
            uris.append(uri)
        elif not uri:
            missing.append(it.get("track") or it["artist"])
    if not uris:
        raise RuntimeError("None of these are on Spotify, so no playlist was made.")
    # Spotify's Feb 2026 API change: /users/{id}/playlists and /playlists/{id}/tracks return 403 for
    # development-mode apps (which every self-hosted Spotiscovery is); these are the replacements.
    pl = _post("/me/playlists", {"name": name, "public": False, "description": description})
    for i in range(0, len(uris), 100):  # Spotify takes up to 100 items per call
        _post(f"/playlists/{pl['id']}/items", {"uris": uris[i:i + 100]})
    return {"url": pl["external_urls"]["spotify"], "added": len(uris), "missing": missing}


def _safe(fn, default):
    """One missing scope or retired endpoint shouldn't sink the whole sync."""
    try:
        return fn()
    except requests.HTTPError as e:
        print(f"spotify: {e}")
        return default


def sync(max_saved=2000):
    """Pull everything we use into kv['spotify'] and return it."""
    try:
        return _sync(max_saved)
    finally:
        store.PROGRESS.pop("sync", None)


def _sync(max_saved):
    snap = {"top": {}, "recent": [], "followed": [], "saved_artists": [], "at": time.time()}
    store.progress("sync", 0, 0, "Reading your top artists and follows")
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
            store.progress("sync", page["offset"] + len(page["items"]), page["total"], "Reading your saved tracks")
            for it in page["items"]:
                names.update(a["name"] for a in it["track"]["artists"])
            url = page.get("next")
        return sorted(names)
    snap["saved_artists"] = _safe(saved, [])
    store.put("spotify", snap)
    return snap


def _match(items, name, fans=None):
    """The search result that is really this artist (search ranks popular near-misses first). When several share
    the name ("Home" x5 on Deezer), fans picks the most-followed one, since that's who Last.fm meant."""
    want = store.norm_loose(name)
    hits = [a for a in items if store.norm_loose(a["name"]) == want]
    return max(hits, key=fans) if hits and fans else (hits[0] if hits else None)


def deezer_image(name):
    """Fallback artist photo from Deezer's public search (no key). None if Deezer only has its blank default."""
    try:
        items = requests.get("https://api.deezer.com/search/artist", params={"q": name, "limit": 10},
                             timeout=10).json().get("data", [])
    except (requests.RequestException, ValueError):
        return None
    pic = (_match(items, name, fans=lambda a: a.get("nb_fan", 0)) or {}).get("picture_xl")
    return pic if pic and "/artist//" not in pic else None  # empty image hash = Deezer's grey placeholder


def artist_card(name):
    """Image + link for a recommendation: Spotify's largest photo, else Deezer's, else a search link.
    Hits are cached 30 days, misses 1 day (the artist may get a photo, or a transient error may clear)."""
    def fetch():
        card = {"image": None, "url": "https://open.spotify.com/search/" + quote(name)}
        if connected():
            try:
                a = _match(_get("/search", q=name, type="artist", limit=5)["artists"]["items"], name)
                if a:  # Spotify lists images widest first
                    card = {"image": a["images"][0]["url"] if a.get("images") else None,
                            "url": a["external_urls"]["spotify"]}
            except (requests.RequestException, RuntimeError, KeyError):
                pass
        card["image"] = card["image"] or deezer_image(name)
        return {**card, "miss": not card["image"]}
    card = store.cached("card2:" + store.norm(name), fetch, max_age=lambda v: 86400 if v.get("miss") else 30 * 86400)
    return {"image": card["image"], "url": card["url"]}
