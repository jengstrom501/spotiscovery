"""Trends from your plays + the inputs recommend.py needs (seeds, known artists)."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import recommend
import store


def minutes_by_artist():
    with store.db() as c:
        return {a: ms / 60000 for a, ms in c.execute(f"SELECT artist, SUM(ms) FROM plays WHERE {store.MUSIC} GROUP BY artist")}


def played_tracks():
    """{norm(artist): {track_key}} for every track in your history."""
    out = defaultdict(set)
    with store.db() as c:
        for a, t in c.execute(f"SELECT DISTINCT artist, track FROM plays WHERE {store.MUSIC} AND track IS NOT NULL"):
            out[store.norm(a)].add(store.track_key(t))
    return out


def hidden():
    with store.db() as c:
        return {store.norm(a) for (a,) in c.execute("SELECT artist FROM not_music WHERE hidden = 1")}


def inputs():
    """(seeds, known) from the export plays and the Spotify snapshot, whichever you have."""
    snap = store.get("spotify", {})
    minutes = minutes_by_artist()
    skip = hidden()  # Spotify's top-artist lists can include the same sleep-sounds "artist"
    top = {rng: [a for a in names if store.norm(a) not in skip] for rng, names in snap.get("top", {}).items()}
    seeds = recommend.seed_weights(minutes, top)
    known = recommend.known_set([*seeds, *snap.get("followed", []), *snap.get("saved_artists", []),
                                 *(p["artist"] for p in snap.get("recent", []))])
    return seeds, known


def trends(lf, drift_artists=150, drift_tags=8):
    with store.db() as c:
        plays = c.execute(f"SELECT ts, artist, ms FROM plays WHERE {store.MUSIC} ORDER BY ts").fetchall()
    snap = store.get("spotify", {})
    out = {"plays": len(plays), "api_top": snap.get("top", {})}
    if not plays:
        return out

    monthly, first_seen, per_year = defaultdict(float), {}, defaultdict(Counter)
    heat = [[0.0] * 24 for _ in range(7)]
    by_quarter = defaultdict(Counter)
    last = datetime.fromisoformat(plays[-1][0].replace("Z", "+00:00"))
    cutoff = (last - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S")  # "recent" = your latest year of data, not today
    recent, last_played = Counter(), {}
    for ts, artist, ms in plays:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        mo, hrs = ts[:7], ms / 3.6e6
        monthly[mo] += hrs
        first_seen.setdefault(artist, mo)
        per_year[ts[:4]][artist] += hrs
        last_played[artist] = ts
        if ts >= cutoff:
            recent[artist] += hrs
        local = d.astimezone()  # only the heatmap wants the viewer's clock; this app is one browser on one machine
        heat[local.weekday()][local.hour] += hrs
        by_quarter[f"{d.year}-Q{(d.month - 1) // 3 + 1}"][artist] += hrs

    months = sorted(monthly)
    discovered = Counter(first_seen.values())
    out.update({
        "months": months,
        "hours": [round(monthly[m], 1) for m in months],
        "discovered": [discovered[m] for m in months],
        "top_by_year": {y: [[a, round(h, 1)] for a, h in cnt.most_common(5)] for y, cnt in sorted(per_year.items())},
        "heatmap": [[round(h, 1) for h in row] for row in heat],
        "total_hours": round(sum(monthly.values())),
        "artists": len(first_seen),
        "lost": lost_loves(per_year, recent, last_played),
    })

    # Tag drift: each quarter's hours spread over the tags of your most-played artists.
    top = [a for a, _ in Counter({a: sum(q[a] for q in by_quarter.values()) for a in first_seen}).most_common(drift_artists)]
    try:
        tags = {a: [(t, n) for t, n in lf.top_tags(a)[:5] if recommend.good_tag(t, a)]
                for a in store.track("trends", top, "Getting tags for your top artists")}
        listeners = {a: (lf.info(a) or {}).get("listeners")
                     for a in store.track("trends", top, "Checking how mainstream each artist is")}
    except Exception as e:  # no or bad Last.fm key, network: only these charts need Last.fm, the rest still shows
        out["drift_note"] = f"Taste drift needs Last.fm tags. {e}"
        return out
    out["mainstream"] = mainstream(by_quarter, listeners)
    q_tags = {}
    for q, cnt in sorted(by_quarter.items()):
        tc = Counter()
        for a in top:
            for t, n in tags[a]:
                tc[t] += cnt[a] * n / 100
        q_tags[q] = tc
    overall = sum(q_tags.values(), Counter())
    keep = [t for t, _ in overall.most_common(drift_tags)]
    out["drift"] = {"quarters": list(q_tags), "tags": keep,
                    "share": {t: [round(100 * q_tags[q][t] / (sum(q_tags[q][k] for k in keep) or 1), 1)
                                  for q in q_tags] for t in keep}}
    out["eras"] = eras(q_tags, by_quarter)
    return out


def lost_loves(per_year, recent, last_played, k=12, min_peak=10):
    """Artists you once played a lot and have since dropped: a peak year of 10+ hours, before your latest year,
    and under 10% of that peak in your latest year."""
    peak = {}
    for y, cnt in per_year.items():
        for a, h in cnt.items():
            if h > peak.get(a, (None, 0))[1]:
                peak[a] = (y, h)
    years = sorted(per_year)
    last_year = years[-1]
    out = [{"name": a, "peak_year": y, "peak_hours": round(h, 1), "recent_hours": round(recent[a], 1),
            "last_played": last_played[a][:10], "yearly": [round(per_year[yy][a], 1) for yy in years]}
           for a, (y, h) in peak.items() if h >= min_peak and y != last_year and recent[a] < 0.1 * h]
    return {"years": years, "artists": sorted(out, key=lambda r: -r["peak_hours"])[:k]}


def mainstream(by_quarter, listeners, min_hours=5):
    """Per quarter, the Last.fm listener count of your 'typical' artist: the hours-weighted median over the
    artists we have listener counts for. Lower means you were listening further from the mainstream."""
    quarters, values = [], []
    for q, cnt in sorted(by_quarter.items()):
        rows = sorted((listeners[a], h) for a, h in cnt.items() if listeners.get(a))
        total = sum(h for _, h in rows)
        if total < min_hours:  # too little tagged listening that quarter for a stable number
            continue
        acc = 0
        for n, h in rows:
            acc += h
            if acc >= total / 2:
                quarters.append(q)
                values.append(n)
                break
    return {"quarters": quarters, "listeners": values}


def eras(q_tags, by_quarter, min_share=0.05, min_len=2):
    """Split your history into eras by what was *distinctive* each quarter: the tag furthest above its all-time share,
    in percentage points (the biggest tag is usually the same all along, so raw share would give one endless era;
    a ratio would crown tiny tags).
    Runs shorter than min_len quarters fold into the era before them."""
    overall = sum(q_tags.values(), Counter())
    total = sum(overall.values()) or 1
    runs = []  # [tag, [quarters]] for consecutive quarters with the same distinctive tag
    for q, tc in q_tags.items():
        qt = sum(tc.values())
        lift = {t: n / qt - overall[t] / total for t, n in tc.items() if qt and n / qt >= min_share}
        tag = max(lift, key=lift.get) if lift else None
        if runs and runs[-1][0] == tag:
            runs[-1][1].append(q)
        else:
            runs.append([tag, [q]])
    merged = []
    for tag, qs in runs:
        if merged and (len(qs) < min_len or merged[-1][0] in (tag, None)):
            merged[-1][1] += qs  # a blip joins the era it interrupts
            if merged[-1][0] is None:
                merged[-1][0] = tag
        else:
            merged.append([tag, qs])
    if len(merged) > 1 and len(merged[0][1]) < min_len:  # a blip at the very start joins the next era
        first = merged.pop(0)
        merged[0][1][:0] = first[1]
    runs = [r for r in merged if r[0]]
    all_artists = sum(by_quarter.values(), Counter())
    all_hours = sum(all_artists.values()) or 1
    out = []
    for tag, qs in runs:
        tc, ac = sum((q_tags[q] for q in qs), Counter()), sum((by_quarter[q] for q in qs), Counter())
        et, eh = sum(tc.values()) or 1, sum(ac.values()) or 1
        lifted = sorted(tc, key=lambda t: overall[t] / total - tc[t] / et)
        # Same idea for artists: who defined this era, not who you always play
        defining = sorted(ac, key=lambda a: all_artists[a] / all_hours - ac[a] / eh)
        out.append({"start": qs[0], "end": qs[-1], "tags": [tag] + [t for t in lifted if t != tag][:1],
                    "artists": defining[:3], "hours": round(eh)})
    return out
