"""A made-up listening history, plus the Last.fm answers that go with it, so anyone can see what Spotiscovery
does before spending a minute on an API key or five days waiting for Spotify to send their export.

Everything is generated from one fixed seed, so the demo looks the same for everyone, and the Last.fm and
MusicBrainz replies are written into the cache table, so the demo never touches the network. It lives in its own
database file (store.DEMO_PATH) and is thrown away and rebuilt whenever this file changes."""
import json
import os
import random
import time
from datetime import date, datetime, timedelta

import store

BUILT_WITH = 4  # bump to rebuild demo.db after changing anything below

# (listeners today, tags, first release year). The listener counts drive the obscurity slider and the
# mainstream meter, so they run from arena-sized down to a few thousand.
ARTISTS = {
    # the shoegaze / indie stretch
    "Paper Harbour": (412_000, ["shoegaze", "dream pop"], 2014),
    "Violet Static": (198_000, ["shoegaze", "noise pop"], 2016),
    "The Long Wade": (1_240_000, ["indie rock", "dream pop"], 2009),
    "Cassette Sunday": (76_000, ["dream pop", "bedroom pop"], 2018),
    "Nine Mile Radio": (523_000, ["indie rock", "alternative"], 2011),
    "Halcyon Ferry": (41_000, ["shoegaze", "ambient"], 2019),
    # the hip-hop stretch
    "Kid Meridian": (3_100_000, ["hip-hop", "rap"], 2015),
    "OSO GRANDE": (2_450_000, ["trap", "hip-hop"], 2017),
    "Lucky Sevens": (890_000, ["hip-hop", "jazz rap"], 2013),
    "Marbles": (5_600_000, ["hip-hop", "pop rap"], 2012),
    "Duval Court": (167_000, ["hip-hop", "underground hip-hop"], 2018),
    "Sable Youngblood": (2_900_000, ["trap", "rap"], 2022),  # found early: blew up after the demo user got there
    # the house / electronic stretch
    "Nightbus": (231_000, ["house", "deep house"], 2016),
    "Orchid Systems": (88_000, ["deep house", "electronic"], 2019),
    "Tessellate": (1_050_000, ["electronic", "idm"], 2010),
    "Coastal Freight": (54_000, ["deep house", "lo-fi house"], 2020),
    "Mira Fold": (1_900_000, ["electronic", "house"], 2023),  # found early
    "Sunday Service Club": (119_000, ["house", "disco"], 2017),
    "Perimeter": (36_000, ["ambient", "electronic"], 2015),
    # played all the way through, whatever the era
    "Green Lantern Radio": (760_000, ["indie rock", "alternative"], 2008),
    "Aurelia Vance": (4_200_000, ["pop", "alternative"], 2014),
    "Bitter Almond": (145_000, ["alternative", "indie rock"], 2013),
}
# Never played, but they show up in the similar lists, so the recommendations have somewhere to go
UNPLAYED = {
    "Wax Harbour": (62_000, ["shoegaze", "dream pop"], 2017),
    "Slow Ferris": (29_000, ["dream pop", "bedroom pop"], 2020),
    "Tin Chapel": (94_000, ["shoegaze", "noise pop"], 2015),
    "Ninth & Vine": (210_000, ["hip-hop", "jazz rap"], 2016),
    "Prince Oakland": (640_000, ["trap", "hip-hop"], 2018),
    "Big Ferry": (118_000, ["underground hip-hop", "hip-hop"], 2019),
    "Cormorant Club": (73_000, ["deep house", "house"], 2018),
    "Nights In Ojai": (44_000, ["lo-fi house", "deep house"], 2021),
    "Second Harbour": (156_000, ["house", "disco"], 2016),
    "Glasswing": (21_000, ["ambient", "electronic"], 2019),
    "Ocean Parkway": (305_000, ["indie rock", "alternative"], 2012),
    "Ruth Amber": (880_000, ["pop", "alternative"], 2017),
}
ALL = {**ARTISTS, **UNPLAYED}

# Eras: (first day, last day, the artists that stretch belongs to). They overlap a little at the seams,
# which is what gives Trends its era boundaries to find.
SCENES = [
    ("2022-01-01", "2022-12-31", ["Paper Harbour", "Violet Static", "The Long Wade", "Cassette Sunday",
                                  "Nine Mile Radio", "Halcyon Ferry"]),
    ("2023-01-01", "2023-11-30", ["Kid Meridian", "OSO GRANDE", "Lucky Sevens", "Marbles", "Duval Court",
                                  "Sable Youngblood"]),
    ("2023-12-01", "2025-12-31", ["Nightbus", "Orchid Systems", "Tessellate", "Coastal Freight", "Mira Fold",
                                  "Sunday Service Club", "Perimeter"]),
]
ALWAYS = ["Green Lantern Radio", "Aurelia Vance", "Bitter Almond"]

WORDS = ("Harbour Lights|Slow Exit|Paper Thin|Cassette|Blue Hour|Ferry Song|Static|Long Wade|Nightswim|Marbles|"
         "Overpass|Second Wind|Tin Roof|Glass Arcade|Winter Count|Coastline|Low Tide|Meridian|Sable|Orchid|"
         "Perimeter|Afterparty|Dial Tone|Sunday Service|Velvet Freight|Half Light|Runner|Tessellate|Undertow|"
         "Little Fires|Gold Standard|Every Exit|Nine Mile|Bitter Almond|Aurelia|Green Lantern").split("|")
ALBUMS = ["Nightswim", "Long Wade", "Paper Thin", "Coastline", "Second Wind", "Meridian", "Afterparty",
          "Low Tide", "Glass Arcade", "Winter Count"]
DEVICES = ["iOS 16.1 (iPhone14,5)", "Windows 10 (Chrome)", "Android OS 13 (Pixel 7)", "web_player (chrome)"]


def _tracks(rng, artist, n=20):
    """A stable made-up tracklist. The user only ever plays part of it, so "songs you've missed" has something."""
    pool = rng.sample(WORDS, min(n, len(WORDS)))
    return [f"{w}" if i % 3 else f"{w} (feat. {rng.choice(list(ALL))})" for i, w in enumerate(pool)][:n]


def _plays(rng):
    """Every play, as store.add_plays() wants them. Weekends and evenings are busier, some days are spent playing
    one album start to finish, and there is a stretch abroad so the travel map has something to draw."""
    tracklists = {a: _tracks(rng, a) for a in ALL}
    heard = {a: tracklists[a][:rng.randint(7, 14)] for a in ARTISTS}  # the rest stay unheard on purpose
    # Each album owns a slice of the artist's heard tracks, so playing one through is a real run of songs
    albums, album_of = {}, {}
    for a in ARTISTS:
        picks, ts = rng.sample(ALBUMS, 3), list(heard[a])
        albums[a] = picks
        for i, al in enumerate(picks):
            for t in ts[i::3]:
                album_of[(a, t)] = al
    trip = (date(2024, 7, 6), date(2024, 7, 20))  # two weeks in Portugal
    rows, day, last = [], date(2022, 1, 1), date(2025, 12, 31)
    while day <= last:
        scene = next((s for s in SCENES if date.fromisoformat(s[0]) <= day <= date.fromisoformat(s[1])), None)
        pool = (scene[2] if scene else []) + ALWAYS
        n = rng.choices([0, 4, 9, 16, 26], weights=[9, 26, 34, 22, 9])[0]
        if day.weekday() >= 5:
            n = int(n * 1.35)
        country = "PT" if trip[0] <= day <= trip[1] else "US"
        device = rng.choices(DEVICES, weights=[6, 3, 2, 1])[0]
        # Roughly one day in six is an album day: most of one record, in order, back to back
        run = []
        if n and rng.random() < 0.17:
            a0 = rng.choice(pool)
            al0 = rng.choice(albums[a0])
            side = [t for t in heard[a0] if album_of[(a0, t)] == al0]
            run = [(a0, t) for t in side[:rng.randint(min(3, len(side)), len(side))]] if side else []
        for i in range(n):
            artist, track = run[i] if i < len(run) else (None, None)
            if artist is None:
                artist = rng.choice(pool)
                track = rng.choice(heard[artist])
            # Evenings carry most of it; a thin tail after midnight gives Explore its 3am artist.
            hour = (19 + i // 4) % 24 if i < len(run) else rng.choices(  # an album run sits in one evening
                range(24), weights=[3, 2, 1, 1, 1, 1, 2, 5, 8, 7, 6, 6, 7, 7, 7, 8, 9, 11, 12, 12, 11, 9, 7, 5])[0]
            ms = rng.choice([182_000, 205_000, 240_000, 168_000, 312_000])
            skipped = rng.random() < (0.03 if i < len(run) else 0.13)  # you don't skip through an album you chose
            rows.append({
                "ts": f"{day}T{hour:02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z",
                "master_metadata_album_artist_name": artist, "master_metadata_track_name": track,
                "master_metadata_album_album_name": album_of[(artist, track)],
                "ms_played": 30_000 + rng.randint(0, 20_000) if skipped else ms,
                "skipped": skipped, "platform": device, "conn_country": country,
                "reason_start": rng.choice(["trackdone", "clickrow", "fwdbtn", "playbtn"]),
                "reason_end": "endplay" if skipped else "trackdone",
                "shuffle": rng.random() < 0.45, "offline": rng.random() < 0.05, "incognito_mode": False,
            })
        day += timedelta(days=1)
    # One song the demo user drops for two years and comes back to, so the recap has a comeback to find
    rows.append({"ts": "2025-11-14T21:12:00Z", "master_metadata_album_artist_name": "Paper Harbour",
                 "master_metadata_track_name": heard["Paper Harbour"][0],
                 "master_metadata_album_album_name": album_of[("Paper Harbour", heard["Paper Harbour"][0])],
                 "ms_played": 224_000,
                 "skipped": False, "platform": DEVICES[0], "conn_country": "US",
                 "reason_start": "clickrow", "reason_end": "trackdone", "shuffle": False, "offline": False})
    sizes = {a: {al: sum(1 for t in heard[a] if album_of[(a, t)] == al) for al in albums[a]} for a in ARTISTS}
    return rows, tracklists, sizes


def _cache(rng, tracklists, sizes):
    """The Last.fm, MusicBrainz and artist-photo answers, written straight into the cache table. With these in
    place the demo never makes a network call, so it works with no keys and no internet."""
    now, rows = time.time(), []
    put = lambda k, v: rows.append((k, json.dumps(v), now))
    by_tag = {}
    for name, (_, tags, _) in ALL.items():
        for t in tags:
            by_tag.setdefault(t, []).append(name)
    for name, (listeners, tags, debut) in ALL.items():
        n = store.norm(name)
        put(f"lastfm2:info:{n}", {"name": name, "listeners": listeners, "tags": tags,
                                  "url": "https://www.last.fm/music/" + name.replace(" ", "+")})
        put(f"lastfm2:tags:{n}", [[t, 100 - 20 * i] for i, t in enumerate(tags)])
        put(f"lastfm2:toptracks:{n}:20", tracklists[name])
        # Similar: others sharing a tag, closest first, always including a few nobody has played
        peers = [p for t in tags for p in by_tag.get(t, []) if p != name]
        seen, sim = set(), []
        for p in sorted(set(peers), key=lambda p: (p not in UNPLAYED, -len(set(ALL[p][1]) & set(tags)), p)):
            if p not in seen:
                seen.add(p)
                sim.append([p, round(max(0.08, 1.0 - 0.11 * len(sim)), 2)])
        put(f"lastfm2:similar:{n}", sim)
        put(f"mb:debut:{n}", {"date": f"{debut}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}"})
        # No photo: the cards fall back to their initials tile, so the demo needs no image host
        put(f"card2:{n}", {"image": None, "url": "https://open.spotify.com/search/" + name.replace(" ", "%20"),
                           "miss": True})
        for al, size in sizes.get(name, {}).items():
            put(f"lastfm2:albumtracks:{n}:{store.norm(al)}", {"n": size})
    for tag, names in by_tag.items():
        put(f"lastfm2:tagtop:{store.norm(tag)}:300", sorted(names, key=lambda x: -ALL[x][0]))
    return rows


def build():
    """(Re)fill demo.db from scratch. Cheap enough (well under a second) to redo whenever the recipe changes."""
    rng = random.Random(20260926)
    rows, tracklists, sizes = _plays(rng)
    cache = _cache(rng, tracklists, sizes)
    was, store.DEMO[0] = store.DEMO[0], True  # write into demo.db, not the real one
    try:
        # Empty the tables rather than the file: a still-open connection keeps the file locked on Windows,
        # and this way a rebuild works whether or not anything is mid-read.
        with store.db() as c:
            c.executescript("DELETE FROM plays; DELETE FROM cache; DELETE FROM kv; DELETE FROM not_music;")
        store.add_plays(rows)
        with store.db() as c:
            c.executemany("INSERT OR REPLACE INTO cache VALUES (?,?,?)", cache)
            c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", ("demo_built", json.dumps(BUILT_WITH)))
        store.flag_not_music()
    finally:
        store.DEMO[0] = was
    return len(rows)


def ready():
    """True when demo.db exists and was built by this version of the recipe."""
    if not os.path.exists(store.DEMO_PATH):
        return False
    was, store.DEMO[0] = store.DEMO[0], True
    try:
        return store.get("demo_built") == BUILT_WITH
    except Exception:
        return False
    finally:
        store.DEMO[0] = was
