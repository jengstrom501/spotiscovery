"""Spotiscovery: local web app. Run: python app.py, then open http://127.0.0.1:8888"""
import json
import os
import re
import traceback
import zipfile
from datetime import date
from urllib.parse import quote

import requests
from flask import Flask, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))

# Minimal .env loader, so there's no python-dotenv dependency. Real env vars win.
if os.path.exists(os.path.join(HERE, ".env")):
    for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

import analyze  # noqa: E402  (after .env so modules see the keys)
import claude_mode  # noqa: E402
import explore  # noqa: E402
import insights  # noqa: E402
import lastfm  # noqa: E402
import recommend  # noqa: E402
import spotify  # noqa: E402
import store  # noqa: E402

app = Flask(__name__, static_folder=os.path.join(HERE, "static"))

SETTINGS_FIELDS = {"spotify_client_id": "SPOTIFY_CLIENT_ID", "lastfm_api_key": "LASTFM_API_KEY",
                   "anthropic_api_key": "ANTHROPIC_API_KEY"}
# Spotify's Client ID has no lightweight "is this real" endpoint (checking it needs a full OAuth round trip),
# so only the two keys below get a live check before they're saved.
VALIDATOR_MODULES = {"LASTFM_API_KEY": lastfm, "ANTHROPIC_API_KEY": claude_mode}
SERVICE_NAMES = {"LASTFM_API_KEY": "Last.fm", "ANTHROPIC_API_KEY": "Anthropic"}
LOCAL_HOSTS = ("127.0.0.1", "localhost")


@app.before_request
def local_only():
    """Any web page can send requests to 127.0.0.1. Refuse a foreign Host (DNS rebinding) and writes whose Origin
    isn't this app (cross-site requests). Clients that send no Origin, like curl, are still allowed."""
    if request.host.rsplit(":", 1)[0] not in LOCAL_HOSTS:
        return jsonify(error="Unknown host"), 403
    origin = request.headers.get("Origin")
    if request.method != "GET" and origin and origin != request.host_url.rstrip("/"):
        return jsonify(error="Cross-site request blocked"), 403


# Every response: no framing (a page on another site could overlay this one and trick a click on "Ask Claude" or
# "Save keys"), no MIME sniffing, no Referer, and a CSP that keeps scripts and requests to this app plus the pinned
# jsDelivr files, so injected code couldn't send your listening history anywhere else.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob: https:; connect-src 'self' https://cdn.jsdelivr.net; font-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")  # 'unsafe-inline': the page uses inline scripts and onclick


@app.after_request
def security_headers(resp):
    resp.headers.update({"Content-Security-Policy": CSP, "X-Frame-Options": "DENY",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    return resp


def save_env(updates):
    """Upsert KEY=value lines in .env, so keys survive a restart. Never logs values."""
    path = os.path.join(HERE, ".env")
    lines = open(path, encoding="utf-8").readlines() if os.path.exists(path) else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip()
        if k in remaining:
            lines[i] = f"{k}={remaining.pop(k)}\n"
    lines += [f"{k}={v}\n" for k, v in remaining.items()]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):  # 404, 405...: keep the real status, no traceback
        return jsonify(error=e.description), e.code
    traceback.print_exc()
    return jsonify(error=str(e)), 500


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    with store.db() as c:
        plays = c.execute(f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM plays WHERE {store.MUSIC}").fetchone()
    snap = store.get("spotify") or {}
    return jsonify(spotify_app=bool(os.environ.get("SPOTIFY_CLIENT_ID")), connected=spotify.connected(),
                   playlists=spotify.can_playlist(),
                   lastfm=bool(os.environ.get("LASTFM_API_KEY")), claude=claude_mode.available(),
                   plays=plays[0], first=plays[1], last=plays[2], synced_at=snap.get("at"))


@app.get("/api/progress")
def progress():
    return jsonify(dict(store.PROGRESS))  # copy: worker threads add/remove entries while this serializes


@app.post("/api/settings")
def settings():
    """Save API keys. Last.fm and Anthropic keys are checked live first, so a rejected key never silently
    looks "saved"; a key a checker can't reach (offline, package missing) still saves, since we can't tell
    it's actually bad."""
    data = request.get_json(silent=True) or {}  # JSON content type only, so a plain-text form post is ignored
    updates, errors = {}, {}
    for json_key, env_key in SETTINGS_FIELDS.items():
        v = (data.get(json_key) or "").strip()
        if not v:
            continue
        if not re.fullmatch(r"[\w.\-]+", v):  # a newline here would write extra KEY=value lines into .env
            errors[json_key] = "That doesn't look like a key. Keys are letters, digits, - _ and . only."
            continue
        mod = VALIDATOR_MODULES.get(env_key)
        if mod and mod.check(v) is False:
            errors[json_key] = f"{SERVICE_NAMES[env_key]} says this key is invalid."
            continue
        updates[env_key] = v
    if updates:
        for k, v in updates.items():
            os.environ[k] = v
        save_env(updates)
    return jsonify(saved=list(updates), errors=errors)


def back_with_error(msg):
    """/login and /callback are page loads, not fetches: show errors in Setup instead of a raw JSON page."""
    return redirect("/?error=" + quote(msg))


@app.get("/login")
def login():
    if not os.environ.get("SPOTIFY_CLIENT_ID"):
        return back_with_error("Add your Spotify Client ID in Setup first.")
    return redirect(spotify.login_url())


@app.get("/callback")
def callback():
    if request.args.get("error"):
        return back_with_error(request.args["error"])
    try:
        spotify.finish_login(request.args.get("code"), request.args.get("state"))
        spotify.sync()
    except Exception as e:  # stale OAuth state, failed token exchange, network
        print(f"spotify login failed: {e!r}")
        return back_with_error(str(e))
    return redirect("/")


@app.post("/api/sync")
def sync():
    snap = spotify.sync()
    return jsonify(top=sum(len(v) for v in snap["top"].values()), followed=len(snap["followed"]),
                   saved=len(snap["saved_artists"]))


def _is_history_json(name):
    """A JSON entry worth importing from the Spotify zip: StreamingHistory_music_*, Streaming_History_Audio_*, or
    the older StreamingHistory0.json (music and podcasts mixed; flag_not_music() catches the podcasts).
    The zip also has files we don't care about (Follow.json, Payments.json, ...), filtered out here."""
    base = os.path.basename(name).lower()
    return base.endswith(".json") and (base.startswith(("streaminghistory_music", "streaming_history_audio"))
                                       or re.fullmatch(r"streaminghistory\d+\.json", base) is not None)


@app.post("/api/upload")
def upload():
    """Import every file that has music plays: a JSON export directly, or the zip Spotify emails you (its
    StreamingHistory_music_*.json / Streaming_History_Audio_*.json entries, extracted here). Names what's
    skipped and why, so one video-history file or an empty zip doesn't abort the rest."""
    added, found, skipped = 0, 0, []
    sources = []  # (name, open()) for every JSON to read, so progress can count them up front
    for f in request.files.getlist("files"):
        if not f.filename.lower().endswith(".zip"):
            sources.append((f.filename, lambda f=f: f.stream))
            continue
        try:
            zf = zipfile.ZipFile(f.stream)
        except zipfile.BadZipFile:
            skipped.append(f"{f.filename} (not a valid zip)")
            continue
        entries = [i for i in zf.infolist() if _is_history_json(i.filename)]
        if not entries:
            skipped.append(f"{f.filename} (no streaming history files inside)")
        sources += [(os.path.basename(i.filename), lambda zf=zf, i=i: zf.open(i)) for i in entries]

    for name, open_ in store.track("upload", sources, "Importing your history files"):
        try:
            data = json.load(open_())
        except ValueError:  # JSONDecodeError and UnicodeDecodeError (a corrupt or non-JSON file)
            skipped.append(f"{name} (not JSON)")
            continue
        if not store.parse_plays(data):
            skipped.append(f"{name} (no music plays)")
            continue
        found += 1
        added += store.add_plays(data)
    if added:
        store.flag_not_music()

    if not found:
        return jsonify(error=f"Nothing to import from {', '.join(skipped)}. Upload the zip Spotify sends you, or "
                             "the StreamingHistory_music_*.json / Streaming_History_Audio_*.json files inside it."), 400
    return jsonify(added=added, **({"skipped": skipped} if skipped else {}))


@app.post("/api/playlist")
def playlist():
    """Save a list of recs as a private Spotify playlist. Body: {name, items: [{artist, track?}]}."""
    data = request.get_json(silent=True) or {}
    items = [i for i in data.get("items", []) if isinstance(i, dict) and i.get("artist")][:200]
    if not items:
        return jsonify(error="Nothing to save."), 400
    if not spotify.can_playlist():
        return jsonify(error="Reconnect Spotify in Setup to allow saving playlists."), 400
    return jsonify(spotify.create_playlist(str(data.get("name") or "Spotiscovery")[:100], items))


@app.get("/api/explore")
def explore_range():
    r = explore.explore(request.args.get("start"), request.args.get("end"))
    if "summary" in r:
        r["on_this_day"] = explore.on_this_day()
        lf = lastfm if os.environ.get("LASTFM_API_KEY") else None
        if lf:
            try:
                explore.with_sound(r, lf)
            except Exception as e:  # genres are extra; the listening stats stand on their own
                r["sound_note"] = f"Genres need Last.fm. {e}"
        explore.with_albums(r, lf)  # without Last.fm, full listens fall back to 5+ songs in a row
    return jsonify(r)


@app.get("/api/compare")
def compare_periods():
    a = request.args
    r = explore.compare(a.get("a_start"), a.get("a_end"), a.get("b_start"), a.get("b_end"),
                        lastfm if os.environ.get("LASTFM_API_KEY") else None)
    return jsonify(r), (400 if "error" in r else 200)


@app.get("/api/song")
def song_page():
    a = request.args
    r = explore.song(a.get("artist", ""), a.get("track", ""), lastfm if os.environ.get("LASTFM_API_KEY") else None,
                     a.get("start"), a.get("end"))
    if "error" not in r:
        r.update(spotify.artist_card(r["artist"]))  # the artist's photo heads the song page
    return jsonify(r), (404 if "error" in r else 200)


@app.get("/api/artist")
def artist_page():
    name = request.args.get("name", "")
    r = explore.artist(name, lastfm if os.environ.get("LASTFM_API_KEY") else None,
                       request.args.get("start"), request.args.get("end"))
    if "error" not in r:
        r.update(spotify.artist_card(name))
        r["similar"] = with_cards(r.get("similar", []))
    return jsonify(r)


@app.get("/api/day")
def day_page():
    return jsonify(explore.day(request.args.get("date")))


@app.get("/api/found-early")
def found_early():
    """Every artist you found early, first played in [start, end] (default all time). pending = artists still
    being looked up on MusicBrainz in the background; the page polls until it's 0."""
    if not os.environ.get("LASTFM_API_KEY"):
        return jsonify(finds=[], pending=0, note="Needs a Last.fm key: it knows how big each artist is today.")
    c = explore.plays()
    if not c["rows"]:
        return jsonify(finds=[], pending=0)
    day = lambda k: date.fromisoformat(request.args[k]) if request.args.get(k) else None
    try:
        since, until = day("start"), day("end")
    except ValueError:
        return jsonify(error="Dates must look like 2024-01-31."), 400
    finds, pending = insights.early_finds(c, lastfm, c["rows"][-1].day, since, until, budget_s=8, check=150, task="early")
    return jsonify(finds=with_cards(finds), pending=pending)


@app.get("/api/recap")
def recap():
    r = analyze.recap(lastfm, request.args.get("year") or None)  # no year: latest finished one; "all": all time
    if not r.get("top"):
        return jsonify(r)
    r["top"][0].update(spotify.artist_card(r["top"][0]["name"]))
    y = r["year"]
    prev = str(int(y) - 1) if y and str(int(y) - 1) in r["years"] else None  # compare with last year when there is one
    eras = None
    try:
        eras = analyze.trends(lastfm).get("eras")
    except Exception:  # eras need Last.fm tags; the other insights don't
        pass
    found = insights.find(lastfm if os.environ.get("LASTFM_API_KEY") else None,
                          f"{y}-01-01" if y else None, f"{y}-12-31" if y else None, y or "all time", prev, r, eras)
    photos = {}
    for i in found[:8]:  # photos for the ones that can reach the card
        if i.get("artist"):
            photos.setdefault(i["artist"], spotify.artist_card(i["artist"]).get("image"))
            i["image"] = photos[i["artist"]]
    r["insights"] = found
    r["eras"] = [{"start": e["start"], "end": e["end"], "tags": e["tags"]} for e in (eras or [])]
    return jsonify(r)


# Photo hosts the cards use. The recap image is drawn on a canvas, and a cross-origin image would taint it
# (no PNG export), so photos are relayed through here. Allow-listed so this can't fetch arbitrary URLs.
IMAGE_HOSTS = ("scdn.co", "spotifycdn.com", "dzcdn.net")


@app.get("/api/img")
def img():
    from urllib.parse import urlparse
    u = request.args.get("u", "")
    p = urlparse(u)
    host = p.hostname or ""
    if p.scheme != "https" or not any(host == d or host.endswith("." + d) for d in IMAGE_HOSTS):  # not "evilscdn.co"
        return jsonify(error="Image host not allowed"), 400
    r = requests.get(u, timeout=15, allow_redirects=False)  # the allow-list only vets this URL, so a redirect would skip it
    if not r.ok or not r.headers.get("Content-Type", "").startswith("image/"):
        return jsonify(error="Couldn't fetch image"), 502
    return r.content, 200, {"Content-Type": r.headers["Content-Type"], "Cache-Control": "max-age=86400"}


@app.get("/api/not-music")
def not_music():
    with store.db() as c:
        rows = c.execute("SELECT n.artist, n.reason, n.hidden, COALESCE(SUM(p.ms), 0) FROM not_music n "
                         "LEFT JOIN plays p ON p.artist = n.artist GROUP BY n.artist ORDER BY 4 DESC").fetchall()
    return jsonify([{"artist": a, "reason": r, "hidden": bool(h), "hours": round(ms / 3.6e6, 1)} for a, r, h, ms in rows])


@app.post("/api/not-music")
def set_not_music():
    data = request.get_json(silent=True) or {}
    if not data.get("artist"):
        return jsonify(error="Which artist?"), 400
    store.set_not_music(data["artist"], bool(data.get("hidden")))
    return jsonify(ok=True)


@app.get("/api/trends")
def trends():
    t = analyze.trends(lastfm)
    if t.get("lost"):
        t["lost"]["artists"] = with_cards(t["lost"]["artists"])
    return jsonify(t)


def with_cards(names_or_recs):
    return [{**r, **spotify.artist_card(r["name"])} for r in store.track("cards", names_or_recs, "Fetching artist photos")]


def named_cards(names):
    """Cards for bare artist names, with Last.fm listeners + tags like the similar-sound cards."""
    out = []
    for n in store.track("cards", names, "Looking up each artist"):
        inf = lastfm.info(n) or {"listeners": 0, "tags": []}
        out.append({"name": n, "listeners": inf["listeners"],
                    "tags": [t for t in inf["tags"] if recommend.good_tag(t)][:4]})
    return with_cards(out)


@app.get("/api/similar")
def similar():
    seeds, known = analyze.inputs(now=True)
    if not seeds:
        return jsonify(error="No listening data yet. Connect Spotify or upload your export."), 400
    recs = recommend.similar_recs(seeds, known, lastfm, obscurity=float(request.args.get("obscurity", 0.5)))
    store.put("last_recs", [r["name"] for r in recs])
    return jsonify(with_cards(recs))


@app.post("/api/feedback")
def rec_feedback():
    """Thumbs on a Similar sound card. Body {artist, vote}: 1 = more like this (it becomes a seed), -1 = not for me,
    0 = forget. Either vote keeps the artist out of future picks."""
    data = request.get_json(silent=True) or {}
    artist, vote = str(data.get("artist") or "").strip()[:200], data.get("vote")
    if not artist or vote not in (1, -1, 0):
        return jsonify(error="Needs an artist and a vote of 1, -1 or 0."), 400
    fb = analyze.feedback()
    fb.pop(artist, None) if vote == 0 else fb.update({artist: vote})
    store.put("feedback", fb)
    return jsonify(ok=True, liked=sum(v > 0 for v in fb.values()), passed=sum(v < 0 for v in fb.values()))


@app.get("/api/deep-cuts")
def deep_cuts():
    seeds, _ = analyze.inputs()
    if not seeds:
        return jsonify(error="No listening data yet. Connect Spotify or upload your export."), 400
    return jsonify(with_cards(recommend.deep_cuts(seeds, analyze.played_tracks(), lastfm)))


def profile():
    seeds, known = analyze.inputs()
    return seeds, known, recommend.tag_profile(seeds, lastfm)


@app.get("/api/blends")
def blends():
    seeds, known, prof = profile()
    auto = recommend.auto_blends(prof, known, lastfm)
    for b in auto:
        b["artists"] = named_cards(b["artists"][:8])
    return jsonify(tags=[[t, round(w, 1)] for t, w in list(prof.items())[:30]], blends=auto)


@app.get("/api/blend")
def blend():
    tags = [t.strip().lower() for t in request.args.get("tags", "").split(",") if t.strip()]
    if len(tags) < 2:
        return jsonify(error="Pick at least two tags"), 400
    _, known = analyze.inputs()
    return jsonify(tags=tags, artists=named_cards(recommend.blend(tags, known, lastfm)))


@app.post("/api/claude")
def claude():
    if not claude_mode.available():
        return jsonify(error="Set ANTHROPIC_API_KEY in .env to use Claude mode"), 400
    seeds, known, prof = profile()
    t = analyze.trends(lastfm)
    drift = t.get("drift")
    summary = {
        "top_tags": {k: round(v, 1) for k, v in list(prof.items())[:25]},
        "top_artists": [n for n, _ in recommend.top_seeds(seeds, 30)],
        "already_recommended": store.get("last_recs", [])[:25],
    }
    if drift and len(drift["quarters"]) > 1:
        summary["tag_share_first_vs_latest_quarter"] = {
            tag: [drift["share"][tag][0], drift["share"][tag][-1]] for tag in drift["tags"]}
    out = claude_mode.suggest(summary, known, lastfm)
    for b in out:
        b["artists"] = with_cards(b["artists"])
    return jsonify(out)


if __name__ == "__main__":
    store.flag_not_music()  # data imported before this check existed
    print("Spotiscovery on http://127.0.0.1:8888")
    app.run(host="127.0.0.1", port=8888, threaded=True)
