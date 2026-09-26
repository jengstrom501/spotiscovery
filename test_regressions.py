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
        break  # a loop that stops early must not leave a stuck bar
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
        assert posts[0] == ("/me/playlists", {"name": "Test", "public": False, "description": "Made with Spotiscovery"})
        assert posts[1] == ("/playlists/pl1/items", {"uris": ["spotify:track:hum", "spotify:track:duster"]})  # no cover, no dupe
        # Several songs per artist: same-title repeats and other artists' covers are skipped, capped at 5
        def get_many(path, **params):
            a = params["q"].split('"')[1]
            names = ["Song A", "Song B", "song a", "Song C"]
            return {"tracks": {"items": [{"uri": "spotify:track:cover", "name": "Song A", "artists": [{"name": "Tribute Band"}]}]
                                        + [{"uri": f"spotify:track:{a}{i}", "name": n, "artists": [{"name": a}]} for i, n in enumerate(names)]}}
        sp._get = get_many
        posts.clear()
        r = client.post("/api/playlist", json={"items": [{"artist": "Hum", "count": 3}, {"artist": "Duster", "count": 99}]})
        assert r.get_json()["added"] == 6 and posts[1][1]["uris"][:3] == ["spotify:track:Hum0", "spotify:track:Hum1", "spotify:track:Hum3"], (r.get_json(), posts)
        sp._get = get
        sp.can_playlist = lambda: False  # an old token without the playlist permission
        r = client.post("/api/playlist", json={"items": [{"artist": "Hum"}]})
        assert r.status_code == 400 and "Reconnect" in r.get_json()["error"]
    finally:
        sp._get, sp._post, sp.can_playlist = orig


def issue_lastfm_autocorrect_namesake():
    # Regression: autocorrect turned "Parcels" (821k listeners) into "The Parcels" (3k), making a big band the
    # "deepest cut". Exact name first; autocorrect only when Last.fm doesn't know the exact name.
    lf = deep_cuts.lastfm
    orig, calls = lf._raw, []

    def raw(method, **p):
        calls.append(p["autocorrect"])
        known = {"Parcels": 821143}
        if p["autocorrect"] == 0:
            return {"artist": {"name": p["artist"], "stats": {"listeners": known[p["artist"]]}}} if p["artist"] in known else {}
        return {"artist": {"name": "Radiohead", "stats": {"listeners": 7_000_000}}}  # autocorrected misspelling
    try:
        lf._raw = raw
        assert lf._call("artist.getInfo", artist="Parcels")["artist"]["stats"]["listeners"] == 821143 and calls == [0]
        assert lf._call("artist.getInfo", artist="Radiohed")["artist"]["name"] == "Radiohead" and calls == [0, 0, 1]
    finally:
        lf._raw = orig


def feature_recap_image_proxy():
    # Feature: the recap card relays photos (a cross-origin image would block PNG export), but only from photo CDNs
    for u in ("http://i.scdn.co/image/x", "https://evilscdn.co/x", "https://example.com/x", "https://127.0.0.1/x"):
        assert client.get("/api/img", query_string={"u": u}).status_code == 400, u


def feature_explore_periods():
    # Feature: every stat for any date range; averages count silent days; whole months/years compare to the
    # previous calendar months; streaks; drill-down to one day. Noon UTC keeps local days stable across timezones.
    from datetime import date
    ex, store = deep_cuts.explore, deep_cuts.store
    hour = lambda day, artist, track: {"ts": f"{day}T12:00:00Z", "master_metadata_album_artist_name": artist,
                                       "master_metadata_track_name": track, "ms_played": 3_600_000,
                                       "reason_end": "trackdone", "platform": "iOS 12.4 (iPhone11,6)", "conn_country": "DK"}
    store.add_plays([hour("2030-03-04", "Explore A", "One"), {**hour("2030-03-04", "Explore A", "Two"), "ts": "2030-03-04T13:30:00Z"},
                     hour("2030-03-05", "Explore A", "One"), hour("2030-03-07", "Explore B", "Three"),
                     hour("2030-03-20", "Explore C", "Later")])  # data runs past the range (ranges clamp at the data's end)
    r = client.get("/api/explore", query_string={"start": "2030-03-04", "end": "2030-03-10"}).get_json()  # Mon..Sun
    s = r["summary"]
    assert (s["hours"], s["listening_days"], s["per_day"], s["per_listening_day"]) == (4.0, 3, 0.57, 1.33), s
    assert r["weekday_avg"][:4] == [2.0, 1.0, 0, 1.0], r["weekday_avg"]
    assert r["streaks"]["longest"]["days"] == 2 and r["streaks"]["longest_gap"]["days"] == 3 and r["streaks"]["current"] == 0
    assert r["top_artists"][0]["name"] == "Explore A" and r["top_artists"][0]["hours"] == 3.0
    assert r["countries"][0]["code"] == "DK" and r["phones"][0]["name"] == "iPhone XS Max"
    assert r["series"]["unit"] == "day" and len(r["series"]["keys"]) == 7
    assert ex.previous_period(date(2030, 3, 1), date(2030, 3, 31)) == (date(2030, 2, 1), date(2030, 2, 28))
    assert ex.previous_period(date(2025, 1, 1), date(2025, 12, 31)) == (date(2024, 1, 1), date(2024, 12, 31))
    assert ex.previous_period(date(2030, 3, 4), date(2030, 3, 10)) == (date(2030, 2, 25), date(2030, 3, 3))
    d = client.get("/api/day", query_string={"date": "2030-03-04"}).get_json()
    assert [p["track"] for p in d["plays"]] == ["One", "Two"] and d["hours"] == 2.0
    # Regression: a range starting where the data starts has no previous period; it must be null, not []
    assert ex.explore(r["data_start"], r["data_start"])["previous"] is None
    otd = ex.on_this_day(date(2031, 3, 4))  # March 4 in earlier years: the 2030 plays above
    assert otd and otd[0]["day"] == "2030-03-04" and otd[0]["artist"] == "Explore A" and otd[0]["hours"] == 2.0, otd

    class LF:
        def top_tags(self, a): return {"Explore A": [("rock", 100)], "Explore B": [("jazz", 100)]}.get(a, [])
    snd = ex.sound(ex._slice(ex.plays(), date(2030, 3, 4), date(2030, 3, 10)), LF())
    assert {k: round(v) for k, v in snd.items()} == {"rock": 75, "jazz": 25}, snd
    for raw, name in [("Android OS 6.0.1 API 23 (samsung, SM-G920A)", "Android"), ("iOS 12.4 (iPhone11,6)", "iPhone / iPad"),
                      ("Partner android_tv Sony", "TV"), ("Windows 10 (10.0.19041; x64)", "Windows PC"), ("not_applicable", None)]:
        assert store.device(raw) == name, (raw, store.device(raw))


def feature_clock_travel_albums_songs_compare():
    # Features: music by the clock, trips abroad, front-to-back vs cherry-picked albums, song pages, compare periods.
    # Times are built in local time (what the app shows), then stored as UTC like Spotify's export.
    from datetime import date, datetime, timedelta, timezone
    ex, store = deep_cuts.explore, deep_cuts.store

    def play(local, artist, track, minutes=4, country="US", album=None):
        ts = (local.astimezone(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")  # ts = end
        return {"ts": ts, "master_metadata_album_artist_name": artist, "master_metadata_track_name": track,
                "ms_played": minutes * 60_000, "conn_country": country, "master_metadata_album_album_name": album}
    rows = []
    for d in range(1, 21):  # 20 days at home: afternoons of Day Band, 3am of Night Owl
        rows += [play(datetime(2032, 1, d, 14, 0), "Day Band", "Sunny", 60), play(datetime(2032, 1, d, 3, 0), "Night Owl", "Loop")]
    rows += [play(datetime(2032, 1, 21, 3, 0) + timedelta(minutes=5 * i), "Night Owl", t) for i, t in enumerate(["Intro", "Loop"] * 2)]
    for d in (10, 11, 12, 14):  # a trip to Denmark with a quiet day in the middle
        rows.append(play(datetime(2032, 2, d, 12, 0), "Trip Band", "Copenhagen", 30, "DK"))
    for rep in range(2):  # an album played front to back twice, and one where only the hit gets played
        start = datetime(2032, 3, 1 + rep, 18, 0)
        rows += [play(start + timedelta(minutes=5 * i), "LP Band", f"Side {i}", 4, album="The LP") for i in range(6)]
    rows += [play(datetime(2032, 3, 5 + i, 20, 0), "Hit Band", "The Hit", 4, album="Greatest Hits") for i in range(8)]
    rows.append(play(datetime(2032, 4, 1, 12, 0), "Day Band", "Later", 60))  # data continues after the ranges
    store.add_plays(rows)

    c = ex.plays()
    jan = ex._slice(c, date(2032, 1, 1), date(2032, 1, 31))
    night = next(p for p in ex.clock(jan)["parts"] if p["name"] == "Late night")
    assert night["artists"][0]["name"] == "Night Owl" and night["artists"][0]["of_theirs"] == 100, night
    assert ex.clock(jan)["three_am"] == "Night Owl"

    trips = ex.travel(c, date(2032, 1, 1), date(2032, 3, 31))
    assert trips["home"] == "US" and [(t["code"], t["start"], t["end"]) for t in trips["trips"]] == [("DK", "2032-02-10", "2032-02-14")], trips

    class LF:
        def album_tracks(self, artist, album): return {"The LP": 8, "Greatest Hits": 12}.get(album)
    al = ex.albums(ex._slice(c, date(2032, 3, 1), date(2032, 3, 31)), LF())
    assert [(a["album"], a["full"]) for a in al["front_to_back"]] == [("The LP", 2)], al  # 6 of 8 in a row = full listen
    assert [(a["album"], a["heard"], a["total"]) for a in al["cherry_picked"]] == [("Greatest Hits", 1, 12)], al

    s = client.get("/api/song", query_string={"artist": "Night Owl", "track": "Loop"}).get_json()
    assert s["plays"] == 22 and s["longest_streak"] == 21 and s["before"] == [{"track": "Intro", "artist": "Night Owl", "times": 2}], s
    assert client.get("/api/song", query_string={"artist": "Nobody", "track": "x"}).status_code == 404

    cmp = client.get("/api/compare", query_string={"a_start": "2032-01-01", "a_end": "2032-01-31",
                                                   "b_start": "2032-03-01", "b_end": "2032-03-31"}).get_json()
    assert cmp["overlap"] == 0 and {x["name"] for x in cmp["only_a"]} == {"Day Band", "Night Owl"}, cmp["overlap"]
    assert cmp["a"]["summary"]["plays"] == 44 and cmp["b"]["start"] == "2032-03-01"


def feature_recap_insights():
    # Feature: the recap leads with what's unusual about you, ranked, one finding per kind (uses the 2032 plays above)
    r = client.get("/api/recap", query_string={"year": "2032"}).get_json()
    ins = r["insights"]
    by_id = {i["id"]: i for i in ins}
    assert by_id["three_am"]["value"] == "Night Owl", ins
    assert by_id["trip"]["value"] == "Copenhagen" and "Denmark" in by_id["trip"]["text"], by_id.get("trip")
    assert by_id["streak"]["value"] == "21 days", by_id.get("streak")  # Jan 1..21
    pf = r["profile"]  # the all-time "about you" numbers, whatever year is picked
    assert pf["plays"] > r["plays"] and pf["artists"] >= r["artists"] and pf["years_on"] >= 0, pf
    assert pf["months"][0] <= "2032-01" <= pf["months"][-1] and len(pf["months"]) == len(pf["hours_by_month"]), pf["months"][:3]
    assert abs(sum(pf["hours_by_month"]) - pf["hours"]) < 1 + len(pf["months"]) * 0.05  # months add up to the total
    assert [i["score"] for i in ins] == sorted((i["score"] for i in ins), reverse=True)
    assert len({i["kind"] for i in ins}) == len(ins)
    assert "insights" in dir(deep_cuts) and deep_cuts.insights.PRIORITY[0] == "early"
    assert all("—" not in i["text"] for i in ins)  # UI copy rule: no em dashes


def feature_found_early():
    # Feature: "you found them early" = first played within ~2 years of their first release (MusicBrainz) and big
    # today (Last.fm). Uses the 2032 plays: Day Band first played 2032-01-01. Both services mocked.
    from datetime import date
    ins, mb, ex = deep_cuts.insights, deep_cuts.insights.musicbrainz, deep_cuts.explore
    debuts = {"Day Band": "2031-08-01",  # first play 5 months after their first release
              "Night Owl": "2020-01-01"}  # first play 12 years after: not early
    orig = mb.cached_debut, ex.plays
    mb.cached_debut = lambda name: (True, debuts.get(name))

    class LF:
        def info(self, a): return {"listeners": {"Day Band": 3_000_000, "Night Owl": 5_000_000}.get(a, 50_000)}
    try:
        c = ex.plays()
        got = ins.found_early(c, LF(), date(2034, 1, 1))
        assert got and got["name"] == "Day Band" and got["listeners"] == 3_000_000 and 100 < got["gap_days"] < 200, got
        finds, pending = ins.early_finds(c, LF(), date(2034, 1, 1))  # the full list for the Found early section
        assert [f["name"] for f in finds] == ["Day Band"] and finds[0]["debut"] == "2031-08-01" and pending == 0, finds
        assert ins.early_finds(c, LF(), date(2034, 1, 1), since=date(2032, 2, 1))[0] == []  # first played before the period
        assert ins.found_early(c, LF(), date(2032, 6, 1)) is None  # played under a year before this period ends
        debuts["Day Band"] = "2010-01-01"  # long established when you found them
        assert ins.found_early(c, LF(), date(2034, 1, 1)) is None
        debuts["Day Band"] = "2031-08-01"

        class Small:
            def info(self, a): return {"listeners": 400_000}  # early, but never got big
        assert ins.found_early(c, Small(), date(2034, 1, 1)) is None
    finally:
        mb.cached_debut, ex.plays = orig
    assert mb._date("2015-02") == "2015-02-01" and mb._date("2015") == "2015-01-01" and mb._date("x") is None


def feature_reimport_fills_new_fields():
    # Feature: plays imported before albums/devices were stored get them from a re-import, without duplicating
    store = deep_cuts.store
    base = {"ts": "2031-01-01T10:00:00Z", "master_metadata_album_artist_name": "Upgrade", "master_metadata_track_name": "Song",
            "ms_played": 200_000}
    assert store.add_plays([base]) == 1
    assert store.add_plays([{**base, "master_metadata_album_album_name": "Album", "shuffle": True}]) == 0
    with store.db() as c:
        assert c.execute("SELECT album, shuffle FROM plays WHERE artist='Upgrade'").fetchall() == [("Album", 1)]


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


def feature_smarter_similar():
    # Feature: Similar sound seeds lean on what you play now (backtested in backtest.py), no scene takes over the
    # list, and thumbs feed back in: "More like this" becomes a seed, both votes keep the artist out of picks.
    from datetime import date
    an, rec, store = deep_cuts.analyze, deep_cuts.recommend, deep_cuts.store
    hour = lambda day, artist: {"ts": f"{day}T12:00:00Z", "master_metadata_album_artist_name": artist,
                                "master_metadata_track_name": "x", "ms_played": 3_600_000}
    store.add_plays([hour("2040-01-01", "Now Old"), hour("2040-12-01", "Now New")])
    m = an.taste_now(until=date(2041, 1, 1))
    assert m["Now New"] > 5 * m["Now Old"], (m["Now New"], m["Now Old"])  # 11 months fresher, and new to you
    recs = [{"name": f"H{i}", "tags": ["deep house" if i % 2 else "lo-fi house"]} for i in range(12)] + \
           [{"name": "R", "tags": ["seen live", "hip hop"]}, {"name": "T", "tags": ["trap"]}]
    out = rec.diversify(recs, k=8)
    assert [r["name"] for r in out][4:6] == ["R", "T"] and len(out) == 8, [r["name"] for r in out]  # 4 house, then the rest
    assert {r["scene"] for r in out} == {"house", "hip-hop"}
    assert client.post("/api/feedback", json={"artist": "Liked One", "vote": 2}).status_code == 400
    client.post("/api/feedback", json={"artist": "Liked One", "vote": 1})
    client.post("/api/feedback", json={"artist": "Nope", "vote": -1})
    seeds, known = an.inputs(now=True)
    assert "liked one" in seeds and {"liked one", "nope"} <= known
    client.post("/api/feedback", json={"artist": "Nope", "vote": 0})
    assert "nope" not in an.inputs(now=True)[1]


def issue_lastfm_unreadable_reply():
    # Regression: a Last.fm HTML error page (not JSON) turned /api/found-early into a 500. Now retried, then a clear error.
    lf = deep_cuts.lastfm

    class Reply:
        def __init__(self, body, status=200): self.content, self.status_code = body.encode(), status
        def json(self):
            import json
            return json.loads(self.content)
    replies = [Reply("<html>502 Bad Gateway</html>", 502), Reply('{"artist": {"name": "X"}}')]
    orig = lf.requests.get, lf.time.sleep, os.environ.get("LASTFM_API_KEY")
    lf.requests.get, lf.time.sleep, os.environ["LASTFM_API_KEY"] = (lambda *a, **k: replies.pop(0)), (lambda s: None), "test"
    try:
        assert lf._raw("artist.getInfo", artist="X") == {"artist": {"name": "X"}}
        replies[:] = [Reply("<html>down</html>", 503)] * 3
        try:
            lf._raw("artist.getInfo", artist="X")
            raise AssertionError("should have raised")
        except RuntimeError as e:
            assert "HTTP 503" in str(e), e
    finally:
        lf.requests.get, lf.time.sleep = orig[:2]
        os.environ.pop("LASTFM_API_KEY") if orig[2] is None else os.environ.update(LASTFM_API_KEY=orig[2])


def issue_public_release_hardening():
    # Regression: pre-publish security review. (1) the image relay's allow-list was skipped by redirects,
    # (2) no anti-framing / CSP headers, (3) a newline in a saved key could write extra lines into .env,
    # (4) spotify._get would attach the bearer token to any URL it was handed.
    r = client.get("/api/status")
    assert r.headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"

    seen = []

    class Redirect:
        ok, status_code, headers, content = True, 302, {"Location": "http://127.0.0.1:9/internal"}, b""
    orig = deep_cuts.requests.get
    deep_cuts.requests.get = lambda url, **kw: (seen.append(kw), Redirect())[1]
    try:
        r = client.get("/api/img", query_string={"u": "https://i.scdn.co/image/abc"})
    finally:
        deep_cuts.requests.get = orig
    assert seen and seen[0].get("allow_redirects") is False and r.status_code == 502, (seen, r.status_code)

    r = client.post("/api/settings", json={"spotify_client_id": "abc\nSPOTIFY_REDIRECT_URI=http://evil.example/cb"})
    assert r.get_json()["saved"] == [] and "spotify_client_id" in r.get_json()["errors"], r.get_json()

    try:
        deep_cuts.spotify._get("https://evil.example/collect")
        raise AssertionError("token was sent to a foreign host")
    except ValueError as e:
        assert "evil.example" in str(e)


if __name__ == "__main__":
    for name, check in list(globals().items()):
        if name.startswith(("issue_", "feature_")):
            check()
    print("ok")
