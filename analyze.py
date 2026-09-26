"""Trends from your plays + the inputs recommend.py needs (seeds, known artists)."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import explore
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


def taste_now(half_life=180, fresh_days=365, fresh_boost=2, until=None):
    """{artist: minutes} weighted toward what you play now: each play decays by its age (half-life in days,
    counted back from your last play, so an old export still works) and artists you started in the last year count
    double. Backtested on this app's own data (backtest.py): versus all-time minutes, it puts ~3x more of the
    artists you went on to adopt near the top of the Similar sound candidates."""
    c = explore.plays()
    rows = [p for p in c["rows"] if until is None or p.day < until]  # until: as of that day (backtest.py)
    if not rows:
        return {}
    end, m = until or rows[-1].day, Counter()
    for p in rows:
        m[p.artist] += 0.5 ** ((end - p.day).days / half_life) * p.ms / 60000
    return {a: v * (fresh_boost if (end - c["first_artist"][a]).days <= fresh_days else 1) for a, v in m.items()}


def feedback():
    """{artist: 1 | -1} from the thumbs on Similar sound cards."""
    return store.get("feedback", {})


def inputs(now=False):
    """(seeds, known) from the export plays and the Spotify snapshot, whichever you have. now: seeds lean on
    recent listening (Similar sound); otherwise all-time minutes (blends, deep cuts, Claude)."""
    snap = store.get("spotify", {})
    minutes = taste_now() if now else minutes_by_artist()
    skip = hidden()  # Spotify's top-artist lists can include the same sleep-sounds "artist"
    top = {rng: [a for a in names if store.norm(a) not in skip] for rng, names in snap.get("top", {}).items()}
    seeds = recommend.seed_weights(minutes, top)
    fb = feedback()
    if seeds and any(v > 0 for v in fb.values()):  # "More like this" makes an artist a seed, at a top-50 weight
        w = sorted((x for _, x in seeds.values()), reverse=True)[min(49, len(seeds) - 1)]
        for a, v in fb.items():
            if v > 0:
                seeds[store.norm(a)] = (a, max(w, seeds.get(store.norm(a), (a, 0))[1]))
    known = recommend.known_set([*seeds, *fb, *snap.get("followed", []), *snap.get("saved_artists", []),
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


def recap(lf, year=None, n_tags=40, n_deep=60, min_deep_hours=2):
    """A shareable year in review (year=None: all time). Beyond the usual top lists: your sound (the tags
    that carried the most of your hours) and your deepest cut (the least-known artist you really played)."""
    with store.db() as c:
        rows = c.execute(f"SELECT ts, artist, track, ms FROM plays WHERE {store.MUSIC} ORDER BY ts").fetchall()
    if not rows:
        return {"years": []}
    per_year = Counter(ts[:4] for ts, *_ in rows)
    years = sorted(y for y, n in per_year.items() if n >= 50)  # skip stub years with a handful of plays
    first_year = {}
    for ts, a, _, _ in rows:
        first_year.setdefault(a, ts[:4])
    if year is None:  # default: the latest finished year, like Wrapped, rather than a few months of this one
        year = years[-2] if len(years) > 1 and years[-1] == str(datetime.now().year) else years[-1]
    elif year == "all":
        year = None
    sel = [r for r in rows if year is None or r[0].startswith(year)]
    if not sel:
        return {"years": years, "error": f"No plays in {year}."}
    hours, tracks = Counter(), Counter()
    for _, a, t, ms in sel:
        hours[a] += ms / 3.6e6
        if t:
            tracks[(t, a)] += 1
    top = hours.most_common()
    sound = Counter()
    for a, h in store.track("recap", top[:n_tags], "Reading your sound"):
        for t, n in lf.top_tags(a)[:5]:
            if recommend.good_tag(t, a):
                sound[t] += h * n / 100
    deep = None
    for a, h in store.track("recap", [x for x in top[:n_deep] if x[1] >= min_deep_hours], "Finding your deepest cut"):
        inf = lf.info(a)
        if inf and inf["listeners"] and (deep is None or inf["listeners"] < deep["listeners"]):
            deep = {"name": a, "listeners": inf["listeners"], "hours": round(h, 1)}
    (track, artist), plays = tracks.most_common(1)[0] if tracks else ((None, None), 0)
    months = sorted({ts[:7] for ts, *_ in sel})
    # "About you", always all-time whatever year is picked: what the card says about the listener, and a monthly
    # history to chart with the picked year highlighted
    every = {}
    for ts, a, t, ms in rows:
        every[ts[:7]] = every.get(ts[:7], 0) + ms / 3.6e6
    first_d, last_d = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00")), datetime.fromisoformat(rows[-1][0].replace("Z", "+00:00"))
    all_months, m = [], datetime(first_d.year, first_d.month, 1)
    while (m.year, m.month) <= (last_d.year, last_d.month):
        all_months.append(f"{m.year}-{m.month:02d}")
        m = datetime(m.year + (m.month == 12), m.month % 12 + 1, 1)
    profile = {"plays": len(rows), "artists": len({a for _, a, _, _ in rows}), "songs": len({(a, t) for _, a, t, _ in rows if t}),
               "hours": round(sum(every.values())), "first_day": first_d.date().isoformat(), "last_day": last_d.date().isoformat(),
               "years_on": round((last_d - first_d).days / 365.25, 1),
               "months": all_months, "hours_by_month": [round(every.get(k, 0), 1) for k in all_months]}
    return {
        "profile": profile,
        "years": years, "year": year, "first_month": months[0], "last_month": months[-1],
        "hours": round(sum(hours.values())), "plays": len(sel), "artists": len(hours),
        "new_artists": sum(1 for a in hours if year is None or first_year[a] == year),
        "top": [{"name": a, "hours": round(h, 1)} for a, h in top[:5]],
        "sound": [t for t, _ in sound.most_common(2)],
        "deepest": deep,
        "on_repeat": track and {"track": track, "artist": artist, "plays": plays},
    }


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


def mainstream(by_quarter, listeners, min_hours=5, window=4):
    """Per quarter, the Last.fm listener count of your 'typical' artist over the trailing 12 months: the
    hours-weighted median over the artists we have listener counts for. Lower = further from the mainstream.
    A single quarter swung 5x on one binge (and the latest quarter is often partial), hence the window."""
    quarters, values = [], []
    qs = sorted(by_quarter)
    for i, q in enumerate(qs):
        cnt = sum((by_quarter[k] for k in qs[max(0, i - window + 1):i + 1]), Counter())
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
