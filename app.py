"""Deep Cuts: local web app. Run: python app.py, then open http://127.0.0.1:8888"""
import json
import os
import traceback
from urllib.parse import quote

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
import lastfm  # noqa: E402
import recommend  # noqa: E402
import spotify  # noqa: E402
import store  # noqa: E402

app = Flask(__name__, static_folder=os.path.join(HERE, "static"))

SETTINGS_FIELDS = {"spotify_client_id": "SPOTIFY_CLIENT_ID", "lastfm_api_key": "LASTFM_API_KEY",
                   "anthropic_api_key": "ANTHROPIC_API_KEY"}
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
        plays = c.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM plays").fetchone()
    snap = store.get("spotify") or {}
    return jsonify(spotify_app=bool(os.environ.get("SPOTIFY_CLIENT_ID")), connected=spotify.connected(),
                   lastfm=bool(os.environ.get("LASTFM_API_KEY")), claude=claude_mode.available(),
                   plays=plays[0], first=plays[1], last=plays[2], synced_at=snap.get("at"))


@app.post("/api/settings")
def settings():
    data = request.get_json(silent=True) or {}  # JSON content type only, so a plain-text form post is ignored
    updates = {}
    for json_key, env_key in SETTINGS_FIELDS.items():
        v = (data.get(json_key) or "").strip()
        if v:
            updates[env_key] = v
    if updates:
        for k, v in updates.items():
            os.environ[k] = v
        save_env(updates)
    return jsonify(saved=list(updates))


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


@app.post("/api/upload")
def upload():
    """Import every file that has music plays and name the ones skipped, so one video-history file or stray
    zip in a multi-select doesn't abort the rest."""
    added, found, skipped = 0, 0, []
    for f in request.files.getlist("files"):
        try:
            data = json.load(f.stream)
        except ValueError:  # JSONDecodeError and UnicodeDecodeError (a zip, a PDF, a corrupt file)
            skipped.append(f"{f.filename} ({'still zipped' if f.filename.lower().endswith('.zip') else 'not JSON'})")
            continue
        if not store.parse_plays(data):
            skipped.append(f"{f.filename} (no music plays)")
            continue
        found += 1
        added += store.add_plays(data)
    if not found:
        return jsonify(error=f"Nothing to import from {', '.join(skipped)}. Unzip what Spotify sends and upload the "
                             "StreamingHistory_music_*.json or Streaming_History_Audio_*.json files."), 400
    return jsonify(added=added, **({"skipped": skipped} if skipped else {}))


@app.get("/api/trends")
def trends():
    return jsonify(analyze.trends(lastfm))


def with_cards(names_or_recs):
    return [{**r, **spotify.artist_card(r["name"])} for r in names_or_recs]


def named_cards(names):
    """Cards for bare artist names, with Last.fm listeners + tags like the similar-sound cards."""
    out = []
    for n in names:
        inf = lastfm.info(n) or {"listeners": 0, "tags": []}
        out.append({"name": n, "listeners": inf["listeners"],
                    "tags": [t for t in inf["tags"] if recommend.good_tag(t)][:4]})
    return with_cards(out)


@app.get("/api/similar")
def similar():
    seeds, known = analyze.inputs()
    if not seeds:
        return jsonify(error="No listening data yet. Connect Spotify or upload your export."), 400
    recs = recommend.similar_recs(seeds, known, lastfm, obscurity=float(request.args.get("obscurity", 0.5)))
    store.put("last_recs", [r["name"] for r in recs])
    return jsonify(with_cards(recs))


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
    print("Deep Cuts on http://127.0.0.1:8888")
    app.run(host="127.0.0.1", port=8888, threaded=True)
