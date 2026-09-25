"""Regression checks for bugs found in QA on 2026-09-24.
Run: python test_regressions.py  (offline, uses a throwaway database)"""
import os
import tempfile

os.environ["DEEP_CUTS_DB"] = os.path.join(tempfile.mkdtemp(), "qa.db")

import app as deep_cuts  # noqa: E402

client = deep_cuts.app.test_client()


def issue_001_cross_site_writes_blocked():
    # Regression: ISSUE-001 - any web page could rewrite .env keys and trigger paid Claude calls
    before = os.environ.get("ANTHROPIC_API_KEY")
    r = client.post("/api/settings", json={"anthropic_api_key": "evil"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403, r.status_code
    assert client.post("/api/claude", headers={"Origin": "http://127.0.0.1:8899"}).status_code == 403  # other local port
    assert client.get("/api/status", headers={"Host": "evil.example:8888"}).status_code == 403  # DNS rebinding
    # A no-cors text/plain post (no preflight) must not be parsed as settings
    r = client.post("/api/settings", data='{"anthropic_api_key": "evil"}', content_type="text/plain")
    assert r.status_code == 200 and r.get_json()["saved"] == [], r.get_json()
    assert os.environ.get("ANTHROPIC_API_KEY") == before
    # The app's own page still works
    assert client.post("/api/settings", json={}, headers={"Origin": "http://localhost"}).status_code == 200


def issue_007_standard_export_imports():
    # Regression: ISSUE-007 - StreamingHistory_music_*.json (account data export) imported 0 plays
    import io
    import json

    def upload(name, records):
        body = {"files": (io.BytesIO(json.dumps(records).encode()), name)}
        return client.post("/api/upload", data=body, content_type="multipart/form-data")

    standard = [
        {"endTime": "2024-03-01 14:15", "artistName": "Low", "trackName": "Words", "msPlayed": 215000},
        {"endTime": "2024-03-01 14:20", "artistName": "Low", "trackName": "Skip", "msPlayed": 4000},  # under 30s
        {"endTime": "not a time", "artistName": "Low", "trackName": "Bad row", "msPlayed": 215000},
    ]
    extended = [
        {"ts": "2024-04-02T20:15:00Z", "master_metadata_album_artist_name": "Duster", "ms_played": 240000},
        {"ts": "2024-04-02T21:00:00Z", "master_metadata_album_artist_name": None, "ms_played": 900000},  # podcast
        {"ts": "2024-04-04T24:15:00Z", "master_metadata_album_artist_name": "Duster", "ms_played": 240000},  # hour 24
    ]
    assert upload("StreamingHistory_music_0.json", standard).get_json() == {"added": 1}
    assert upload("Streaming_History_Audio_2024.json", extended).get_json() == {"added": 1}
    assert deep_cuts.store.parse_plays(standard)[0][0] == "2024-03-01T14:15:00Z"


def issue_002_not_found_is_404():
    # Regression: ISSUE-002 - every 404 (favicon.ico on each page load) came back as a 500 with a traceback
    for path in ("/favicon.ico", "/no-such-page"):
        r = client.get(path)
        assert r.status_code == 404, (path, r.status_code)
        assert "error" in r.get_json()


def issue_003_login_errors_return_to_app():
    # Regression: ISSUE-003 - /login and /callback failures ended on a raw JSON page
    saved = os.environ.pop("SPOTIFY_CLIENT_ID", None)
    try:
        for path in ("/login", "/callback?code=abc&state=stale", "/callback?error=access_denied"):
            r = client.get(path)
            assert r.status_code == 302, (path, r.status_code)
            assert r.headers["Location"].startswith("/?error="), (path, r.headers["Location"])
    finally:
        if saved is not None:
            os.environ["SPOTIFY_CLIENT_ID"] = saved


def issue_008_rejected_uploads_explained():
    # Regression: ISSUE-008 - a zip or bad JSON showed raw Python exceptions, and one bad file aborted the rest
    import io
    import json

    def post(*files):
        body = {"files": [(io.BytesIO(data), name) for name, data in files]}
        return client.post("/api/upload", data=body, content_type="multipart/form-data")

    r = post(("my_spotify_data.zip", b"PK\x03\x04\x99 zip bytes"))
    assert r.status_code == 400 and "not a valid zip" in r.get_json()["error"], r.get_json()
    r = post(("notes.json", b"this is not json"))
    assert r.status_code == 400 and "not JSON" in r.get_json()["error"], r.get_json()
    music = [{"ts": "2024-05-01T10:00:00Z", "master_metadata_album_artist_name": "Hum", "ms_played": 200000}]
    video = [{"ts": "2024-05-01T11:00:00Z", "master_metadata_album_artist_name": None, "ms_played": 200000}]
    r = post(("Streaming_History_Audio_2024_1.json", json.dumps(music).encode()),
             ("Streaming_History_Video_2024.json", json.dumps(video).encode()))
    assert r.get_json() == {"added": 1, "skipped": ["Streaming_History_Video_2024.json (no music plays)"]}, r.get_json()


def issue_009_trends_without_lastfm_key():
    # Regression: ISSUE-009 - a missing Last.fm key blanked all of Trends, not just the taste drift chart
    deep_cuts.store.add_plays([{"ts": "2024-06-01T09:00:00Z", "master_metadata_album_artist_name": "Codeine",
                                "ms_played": 300000}])
    saved = os.environ.pop("LASTFM_API_KEY", None)
    try:
        r = client.get("/api/trends")
        t = r.get_json()
        assert r.status_code == 200 and t["plays"] > 0 and t["hours"], t
        assert "drift" not in t and "Setup" in t["drift_note"], t.get("drift_note")
    finally:
        if saved is not None:
            os.environ["LASTFM_API_KEY"] = saved


def issue_collab_of_known_artists():
    # Regression: "Future & Lil Uzi Vert" was recommended to someone who plays both (Last.fm lists collabs as artists)
    rec = deep_cuts.recommend
    known = rec.known_set(["Future", "Lil Uzi Vert", "Travis Scott", "Lil Wayne", "Beyoncé"])
    for collab in ("Future & Lil Uzi Vert", "Future x Lil Uzi Vert", "Lil Uzi Vert feat. Someone", "Future, Drake"):
        assert rec.is_known(collab, known), collab
    assert not rec.is_known("Slowdive & Ride", known)  # a collab of artists you don't play still shows
    assert not rec.is_known("Futurecop!", known)  # no substring matches
    # Regression: Last.fm's misspelled duplicates ("Travi$ Scott", "Lil' Wayne") were recommended as new artists
    for alias in ("Travi$ Scott", "Lil' Wayne", "Beyonce", "Lil Wayne feat. Drake"):
        assert rec.is_known(alias, known), alias
    assert rec.is_known("Scotts", known, ["hip-hop", "travis scott"])  # side project tagged with a known artist
    assert not rec.is_known("Big Scarr", known, ["rap", "memphis"])
    # Loose matching must not reach into parts and tags
    known2 = rec.known_set(["Queen", "Flo Rida"])
    assert not rec.is_known("The Good, the Bad & the Queen", known2)
    assert not rec.is_known("Jackboy", known2, ["rap", "florida"])


def feature_artist_image_pick():
    # Feature: cards use the largest photo, match names loosely, fall back to Deezer, and skip blank placeholders
    sp, requests = deep_cuts.spotify, deep_cuts.spotify.requests
    orig = sp.connected, sp._get, requests.get
    img = lambda w: {"url": f"https://i.scdn.co/{w}", "width": w}
    results = {"artists": {"items": [
        {"name": "Beyoncé Tribute", "images": [img(640)], "external_urls": {"spotify": "wrong"}},
        {"name": "Beyoncé", "images": [img(640), img(320), img(160)], "external_urls": {"spotify": "right"}}]}}

    class Deezer:
        def __init__(self, data): self.data = data
        def json(self): return {"data": self.data}
    try:
        sp.connected, sp._get = (lambda: True), (lambda *a, **k: results)
        assert sp.artist_card("Beyonce") == {"image": "https://i.scdn.co/640", "url": "right"}
        sp.connected = lambda: False  # export-only users get Deezer photos, but not Deezer's blank default
        requests.get = lambda *a, **k: Deezer([{"name": "The Hum", "picture_xl": "https://e-cdns/artist/abc/1000x1000.jpg"}])
        assert sp.artist_card("Hum")["image"] == "https://e-cdns/artist/abc/1000x1000.jpg"
        # Several artists share a name: take the most-followed (the tiny "Home" had a stock silhouette)
        requests.get = lambda *a, **k: Deezer([{"name": "Home", "picture_xl": "https://e-cdns/artist/tiny/x.jpg", "nb_fan": 35},
                                               {"name": "Home", "picture_xl": "https://e-cdns/artist/real/x.jpg", "nb_fan": 1955}])
        assert sp.artist_card("Home")["image"] == "https://e-cdns/artist/real/x.jpg"
        requests.get = lambda *a, **k: Deezer([{"name": "Duster", "picture_xl": "https://e-cdns/artist//1000x1000.jpg"}])
        assert sp.artist_card("Duster")["image"] is None
    finally:
        sp.connected, sp._get, requests.get = orig


def feature_progress_reporting():
    # Feature: long loops publish done/total at /api/progress, and clear it when finished or abandoned
    store = deep_cuts.store
    seen = []
    for x in store.track("demo", ["a", "b", "c"], "Testing"):
        seen.append(client.get("/api/progress").get_json()["demo"])
    assert [s["done"] for s in seen] == [0, 1, 2] and seen[0]["total"] == 3 and seen[0]["label"] == "Testing", seen
    assert "demo" not in client.get("/api/progress").get_json()
    for x in store.track("demo", range(10), "Testing"):
        break  # a loop that stops early (auto_blends) must not leave a stuck bar
    assert "demo" not in store.PROGRESS


def feature_not_music_filter():
    # Feature: podcasts, audiobooks and sleep sounds sold as music tracks are hidden from every chart and seed
    store = deep_cuts.store
    plays = lambda artist, track, mins, n: [{"ts": f"2025-01-{d:02d}T10:00:00Z", "master_metadata_album_artist_name": artist,
                                             "master_metadata_track_name": f"{track} {d}", "ms_played": mins * 60_000}
                                            for d in range(1, n + 1)]
    store.add_plays(plays("Stardust Vibes", "Thunderstorm Sounds for Sleep", 60, 5)  # the real leak that prompted this
                    + plays("Some Show", "Interview", 55, 3)  # podcast in a legacy mixed file
                    + plays("An Author", "Chapter", 4, 6)  # audiobook sold as an album
                    + plays("Chihei Hatakeyama", "Ferrum", 16, 4)  # real ambient artist with long tracks: keep
                    + plays("Sleep", "Dopesmoker", 12, 3)  # the band Sleep: keep
                    + plays("Imagine Dragons", "Thunder", 3, 4))  # "Thunder" on repeat is a pop song: keep
    store.flag_not_music()
    flagged = {r["artist"]: r for r in client.get("/api/not-music").get_json()}
    assert set(flagged) == {"Stardust Vibes", "Some Show", "An Author"}, flagged
    assert flagged["Stardust Vibes"]["hours"] == 5.0 and flagged["Stardust Vibes"]["hidden"]
    artists = lambda: {a for y in client.get("/api/trends").get_json()["top_by_year"].values() for a, _ in y}
    assert "Stardust Vibes" not in artists() and "Chihei Hatakeyama" in artists()
    # Put back sticks, even after the next import re-runs the check
    client.post("/api/not-music", json={"artist": "Stardust Vibes", "hidden": False})
    store.flag_not_music()
    assert "Stardust Vibes" in artists()
    client.post("/api/not-music", json={"artist": "Imagine Dragons", "hidden": True})  # hide by hand
    assert "Imagine Dragons" not in artists()
    assert deep_cuts._is_history_json("MyData/StreamingHistory0.json")  # legacy account-data export
    assert not deep_cuts._is_history_json("MyData/StreamingHistory_podcast_0.json")


def feature_insights():
    # Features: lost loves, mainstream meter, eras, deep cuts (pure functions, fed hand-made data)
    from collections import Counter
    an, rec = deep_cuts.analyze, deep_cuts.recommend
    per_year = {"2020": Counter({"Old Fave": 40, "Steady": 20}), "2021": Counter({"Old Fave": 3, "Steady": 20})}
    lost = an.lost_loves(per_year, Counter({"Old Fave": 0.5, "Steady": 18}), {"Old Fave": "2021-02-01T00:00:00Z", "Steady": "x"})
    assert [r["name"] for r in lost["artists"]] == ["Old Fave"] and lost["artists"][0]["yearly"] == [40, 3], lost

    q = {"2020-Q1": Counter({"Big": 10, "Small": 1}), "2020-Q2": Counter({"Big": 1, "Small": 10})}
    m = an.mainstream(q, {"Big": 5_000_000, "Small": 20_000})
    assert m == {"quarters": ["2020-Q1", "2020-Q2"], "listeners": [5_000_000, 20_000]}, m

    tags = lambda **kw: Counter(kw)
    q_tags = {"2020-Q1": tags(rock=10, jazz=1), "2020-Q2": tags(rock=10, jazz=1), "2020-Q3": tags(rock=10, jazz=9),
              "2020-Q4": tags(rock=10, jazz=1), "2021-Q1": tags(rock=10, jazz=9), "2021-Q2": tags(rock=10, jazz=9)}
    by_q = {k: Counter({"Band": 10}) for k in q_tags}
    e = an.eras(q_tags, by_q)
    # the lone jazz quarter (Q3 2020) is a blip inside the rock era, not an era of its own
    assert [(x["start"], x["end"], x["tags"][0]) for x in e] == [("2020-Q1", "2020-Q4", "rock"), ("2021-Q1", "2021-Q2", "jazz")], e

    class LF:
        def top_tracks(self, a): return ["Hit", "Deep One", "Deep Two (Remastered)", "Deep Three", "Hit Two"]
    seeds = {"band": ("Band", 5.0)}
    played = {"band": {deep_cuts.store.track_key(t) for t in ("Hit - Live", "Hit Two", "Deep Two")}}
    # "Hit - Live" counts as Hit, "Deep Two" as the remaster: 2 unheard is under min_unheard=3, so no entry
    assert rec.deep_cuts(seeds, played, LF()) == []
    d = rec.deep_cuts(seeds, played, LF(), min_unheard=2)
    assert d[0]["tracks"] == ["Deep One", "Deep Three"] and d[0]["heard"] == 3, d


def feature_playlist_export():
    # Feature: save a list of picks as a private Spotify playlist (Spotify mocked)
    sp = deep_cuts.spotify
    orig = sp._get, sp._post, sp.can_playlist
    posts = []
    catalog = {"Hum": "spotify:track:hum", "Duster": "spotify:track:duster"}

    def get(path, **params):
        if path == "/me":
            return {"id": "me"}
        artist = params["q"].split('"')[1]
        items = [{"uri": catalog[artist], "artists": [{"name": artist}]}] if artist in catalog else []
        return {"tracks": {"items": [{"uri": "spotify:track:cover", "artists": [{"name": "Tribute Band"}]}] + items}}

    def post(path, body):
        posts.append((path, body))
        return {"id": "pl1", "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"}}
    try:
        sp._get, sp._post, sp.can_playlist = get, post, (lambda: True)
        r = client.post("/api/playlist", json={"name": "Test", "items": [{"artist": "Hum"}, {"artist": "Duster", "track": "Echo"},
                                                                         {"artist": "Nobody"}, {"artist": "Hum"}]})
        body = r.get_json()
        assert body == {"url": "https://open.spotify.com/playlist/pl1", "added": 2, "missing": ["Nobody"]}, body
        # Regression: the pre-Feb-2026 endpoints (/users/{id}/playlists, /playlists/{id}/tracks) 403 for dev-mode apps
        assert posts[0] == ("/me/playlists", {"name": "Test", "public": False, "description": "Made with Deep Cuts"})
        assert posts[1] == ("/playlists/pl1/items", {"uris": ["spotify:track:hum", "spotify:track:duster"]})  # no cover, no dupe
        sp.can_playlist = lambda: False  # an old token without the playlist permission
        r = client.post("/api/playlist", json={"items": [{"artist": "Hum"}]})
        assert r.status_code == 400 and "Reconnect" in r.get_json()["error"]
    finally:
        sp._get, sp._post, sp.can_playlist = orig


def feature_zip_upload_extracts_history():
    # Feature: upload the Spotify zip directly; the server extracts the streaming-history entries itself
    import io
    import json
    import zipfile

    music = [{"ts": "2024-08-01T09:00:00Z", "master_metadata_album_artist_name": "Duster", "ms_played": 210000}]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2024_1.json", json.dumps(music))
        z.writestr("Follow.json", "{}")  # a real export also has files that aren't listening history
    r = client.post("/api/upload", data={"files": (io.BytesIO(buf.getvalue()), "my_spotify_data.zip")},
                     content_type="multipart/form-data")
    assert r.get_json() == {"added": 1}, r.get_json()


def feature_heatmap_local_time():
    # Feature: "When you listen" now reflects the viewer's local time, not UTC
    from datetime import datetime

    ts = "2024-07-15T23:30:00Z"  # near midnight UTC, so most timezones land on a different local weekday/hour
    deep_cuts.store.add_plays([{"ts": ts, "master_metadata_album_artist_name": "Mogwai", "ms_played": 300000}])
    t = client.get("/api/trends").get_json()
    local = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    assert t["heatmap"][local.weekday()][local.hour] > 0, (t["heatmap"], local.weekday(), local.hour)


def feature_settings_validates_keys():
    # Feature: a save with a bad Last.fm/Anthropic key is rejected instead of silently "saved"
    orig_lastfm, orig_claude, orig_save_env = deep_cuts.lastfm.check, deep_cuts.claude_mode.check, deep_cuts.save_env
    deep_cuts.lastfm.check = lambda k: False  # simulate Last.fm rejecting the key
    deep_cuts.claude_mode.check = lambda k: True  # simulate Anthropic accepting it
    deep_cuts.save_env = lambda updates: None  # don't touch the real .env file for a test key
    saved_env = os.environ.get("ANTHROPIC_API_KEY")
    try:
        r = client.post("/api/settings", json={"lastfm_api_key": "bad-key", "anthropic_api_key": "good-key"},
                         headers={"Origin": "http://localhost"})
        body = r.get_json()
        assert body["saved"] == ["ANTHROPIC_API_KEY"], body
        assert "lastfm_api_key" in body["errors"] and "Last.fm" in body["errors"]["lastfm_api_key"], body
        assert os.environ.get("LASTFM_API_KEY") != "bad-key"
        assert os.environ.get("ANTHROPIC_API_KEY") == "good-key"
    finally:
        deep_cuts.lastfm.check, deep_cuts.claude_mode.check, deep_cuts.save_env = orig_lastfm, orig_claude, orig_save_env
        if saved_env is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved_env


if __name__ == "__main__":
    for name, check in list(globals().items()):
        if name.startswith(("issue_", "feature_")):
            check()
    print("ok")
