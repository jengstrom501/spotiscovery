"""Explore: every stat for any date range, plus artist and day drill-downs.
Days, weekdays and hours are local time (this app is one person on one machine), like the Trends heatmap."""
import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, namedtuple
from datetime import date, datetime, timedelta

import recommend
import store

P = namedtuple("P", "dt day artist track ms skipped album platform country start end shuffle offline")
_cache = {}


def plays():
    """All music plays oldest first, with local datetimes, plus first-play indexes. Rebuilt only when data changes
    (store.VERSION), so switching periods doesn't re-parse 150k timestamps each time."""
    if _cache.get("v") != store.VERSION[0]:
        with store.db() as c:
            raw = c.execute("SELECT ts, artist, track, ms, skipped, album, platform, country, reason_start, reason_end, "
                            f"shuffle, offline FROM plays WHERE {store.MUSIC} ORDER BY ts").fetchall()
        rows = []
        for ts, *rest in raw:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
            rows.append(P(dt, dt.date(), *rest))
        first_artist, first_track, song_plays, country_h = {}, {}, Counter(), Counter()
        keys = [store.track_key(p.track or "") for p in rows]  # song identity, versions merged ("(Remastered)")
        for p, k in zip(rows, keys):
            first_artist.setdefault(p.artist, p.day)
            first_track.setdefault((p.artist, k), p.day)
            song_plays[(p.artist, k)] += 1
            if p.country:
                country_h[p.country] += p.ms
        _cache.update(v=store.VERSION[0], rows=rows, days=[p.day for p in rows], keys=keys,
                      first_artist=first_artist, first_track=first_track, song_plays=song_plays,
                      home=country_h.most_common(1)[0][0] if country_h else None)
    return _cache


def _day(s, default):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


def bad_dates(*vals):
    """True if any of these was given but is not a real date. Empty means "not specified", which is fine and
    falls back to the data's own edges. A typo in the URL should say so, not quietly hand back all time."""
    return any(v and _day(v, None) is None for v in vals)


def _slice(c, start, end):
    return c["rows"][bisect_left(c["days"], start):bisect_right(c["days"], end)]


def hours(rows):
    return sum(p.ms for p in rows) / 3.6e6


def _is_skip(p):
    return p.end == "fwdbtn" if p.end else bool(p.skipped)


# How a song started, in plain words (Spotify's reason_start codes)
STARTS = {"trackdone": "Played on from the last song", "fwdbtn": "You skipped to it", "clickrow": "You picked it",
          "playbtn": "You pressed play", "backbtn": "You went back for it", "appload": "Resumed when the app opened",
          "remote": "Started from another device"}

# iPhone model identifiers in Spotify's platform strings -> names people know. Unknown ids show as-is.
IPHONES = {"9,1": "7", "9,3": "7", "9,2": "7 Plus", "9,4": "7 Plus", "10,1": "8", "10,4": "8", "10,2": "8 Plus",
           "10,5": "8 Plus", "10,3": "X", "10,6": "X", "11,2": "XS", "11,4": "XS Max", "11,6": "XS Max", "11,8": "XR",
           "12,1": "11", "12,3": "11 Pro", "12,5": "11 Pro Max", "12,8": "SE (2nd gen)", "13,1": "12 mini", "13,2": "12",
           "13,3": "12 Pro", "13,4": "12 Pro Max", "14,4": "13 mini", "14,5": "13", "14,2": "13 Pro", "14,3": "13 Pro Max",
           "14,6": "SE (3rd gen)", "14,7": "14", "14,8": "14 Plus", "15,2": "14 Pro", "15,3": "14 Pro Max", "15,4": "15",
           "15,5": "15 Plus", "16,1": "15 Pro", "16,2": "15 Pro Max", "17,1": "16 Pro", "17,2": "16 Pro Max",
           "17,3": "16", "17,4": "16 Plus", "17,5": "16e"}


def phone(platform):
    m = re.search(r"iPhone(\d+,\d+)", platform or "")
    return m and "iPhone " + IPHONES.get(m.group(1), m.group(1))


def _top(counter, n):
    return [k for k, _ in counter.most_common(n)]


def _series(rows, start, end, first_artist):
    """Hours and new artists per bucket; the bucket size follows the range so the chart stays readable."""
    n = (end - start).days + 1
    unit = "day" if n <= 62 else "week" if n <= 731 else "month"
    key = {"day": lambda d: d.isoformat(),
           "week": lambda d: (d - timedelta(days=d.weekday())).isoformat(),
           "month": lambda d: d.isoformat()[:7]}[unit]
    buckets, d = [], start
    while d <= end:  # every bucket in the range, so empty stretches show as gaps, not as missing bars
        k = key(d)
        if not buckets or buckets[-1] != k:
            buckets.append(k)
        d += timedelta(days=1)
    hrs, new = Counter(), Counter()
    for p in rows:
        hrs[key(p.day)] += p.ms / 3.6e6
    for a, d0 in first_artist.items():
        if start <= d0 <= end:
            new[key(d0)] += 1
    return {"unit": unit, "keys": buckets, "hours": [round(hrs[k], 2) for k in buckets], "new": [new[k] for k in buckets]}


def _sessions(rows, gap_min=30):
    """A session ends when more than gap_min passes between one song ending and the next starting."""
    out, cur = [], []
    for p in rows:
        begin = p.dt - timedelta(milliseconds=p.ms)
        if cur and (begin - cur[-1].dt) > timedelta(minutes=gap_min):
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def _streaks(days_with_music, start, end):
    """Longest run of days with music, longest run without, and the run still going at the end of the range."""
    best = gap = run = run_gap = 0
    best_end = gap_end = None
    d = start
    while d <= end:
        if d in days_with_music:
            run, run_gap = run + 1, 0
            if run > best:
                best, best_end = run, d
        else:
            run_gap, run = run_gap + 1, 0
            if run_gap > gap:
                gap, gap_end = run_gap, d
        d += timedelta(days=1)
    span = lambda n, e: e and {"days": n, "start": (e - timedelta(days=n - 1)).isoformat(), "end": e.isoformat()}
    return {"longest": span(best, best_end), "longest_gap": span(gap, gap_end), "current": run}


def _add_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def previous_period(start, end):
    """What a range is compared against. Whole months (and so whole years) compare with the same number of
    calendar months just before (2025 vs 2024, March vs February); anything else with the same number of days."""
    if start.day == 1 and (end + timedelta(days=1)).day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        return _add_months(start, -months), start - timedelta(days=1)
    n = (end - start).days + 1
    return start - timedelta(days=n), start - timedelta(days=1)


def explore(start=None, end=None, today=None):
    c = plays()
    rows_all = c["rows"]
    if not rows_all:
        return {"empty": True}
    if bad_dates(start, end):
        return {"error": "Dates must look like 2024-01-31."}
    data_start, data_end = rows_all[0].day, rows_all[-1].day
    start = max(_day(start, data_start), data_start)
    end = min(_day(end, data_end), data_end)
    if start > end:
        return {"error": "That range has no listening in it.", "data_start": data_start.isoformat(), "data_end": data_end.isoformat()}
    rows = _slice(c, start, end)
    n_days = (end - start).days + 1
    prev_start, prev_end = previous_period(start, end)
    prev_start = max(prev_start, data_start)
    prev = _slice(c, prev_start, prev_end) if prev_end >= data_start else []
    today = today or date.today()

    daily = Counter()
    for p in rows:
        daily[p.day] += p.ms / 3.6e6
    total = sum(daily.values())
    # Averages count every calendar day in the range, silent ones included: that's what "a day" means
    wd_days = Counter((start + timedelta(days=i)).weekday() for i in range(n_days))
    wd_hours, hour_h, heat = Counter(), Counter(), [[0.0] * 24 for _ in range(7)]
    for p in rows:
        h = p.ms / 3.6e6
        wd_hours[p.dt.weekday()] += h
        hour_h[p.dt.hour] += h
        heat[p.dt.weekday()][p.dt.hour] += h
    weekday_avg = [round(wd_hours[w] / wd_days[w], 2) if wd_days[w] else 0 for w in range(7)]
    bins = [(0, 0), (0, .5), (.5, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 99)]
    dist = [0] * len(bins)
    for i in range(n_days):
        h = daily.get(start + timedelta(days=i), 0)
        dist[0 if h == 0 else next(j for j, (lo, hi) in enumerate(bins) if j and lo < h <= hi)] += 1

    artist_h, artist_n, prev_h = Counter(), Counter(), Counter()
    track_n, track_h, track_skip, album_h = Counter(), Counter(), Counter(), Counter()
    for p in rows:
        artist_h[p.artist] += p.ms / 3.6e6
        artist_n[p.artist] += 1
        k = (p.track or "(unknown)", p.artist)
        track_n[k] += 1
        track_h[k] += p.ms / 3.6e6
        track_skip[k] += _is_skip(p)
        if p.album:
            album_h[(p.album, p.artist)] += p.ms / 3.6e6
    for p in prev:
        prev_h[p.artist] += p.ms / 3.6e6
    prev_rank = {a: i + 1 for i, a in enumerate(_top(prev_h, 200))}
    first = c["first_artist"]
    top_artists = [{"name": a, "hours": round(h, 1), "plays": artist_n[a], "share": round(100 * h / total, 1) if total else 0,
                    "prev_rank": prev_rank.get(a), "new": start <= first[a] <= end, "first": first[a].isoformat()}
                   for a, h in artist_h.most_common(25)]
    top_tracks = [{"track": t, "artist": a, "plays": track_n[(t, a)], "hours": round(track_h[(t, a)], 1),
                   "skip_rate": round(100 * track_skip[(t, a)] / track_n[(t, a)])} for (t, a), _ in track_n.most_common(25)]
    top_albums = [{"album": al, "artist": a, "hours": round(h, 1)} for (al, a), h in album_h.most_common(12)]

    movers = {"up": [], "down": []}
    if prev:
        delta = {a: artist_h[a] - prev_h[a] for a in set(_top(artist_h, 60)) | set(_top(prev_h, 60))}
        movers["up"] = [{"name": a, "hours": round(artist_h[a], 1), "was": round(prev_h[a], 1)}
                        for a in sorted(delta, key=lambda a: -delta[a])[:6] if delta[a] > 0]
        movers["down"] = [{"name": a, "hours": round(artist_h[a], 1), "was": round(prev_h[a], 1)}
                          for a in sorted(delta, key=lambda a: delta[a])[:6] if delta[a] < 0]

    new_here = [a for a in artist_h if start <= first[a] <= end]
    after = Counter()
    later = c["rows"][bisect_right(c["days"], end):]
    new_set = set(new_here)
    for p in later:
        if p.artist in new_set:
            after[p.artist] += p.ms / 3.6e6
    discoveries = [{"name": a, "hours": round(artist_h[a], 1), "after": round(after[a], 1), "first": first[a].isoformat()}
                   for a in sorted(new_here, key=lambda a: -artist_h[a])[:12]]
    keys = c["keys"][bisect_left(c["days"], start):bisect_right(c["days"], end)]  # aligned with rows, precomputed
    fresh = sum(p.ms for p, k in zip(rows, keys) if start <= c["first_track"][(p.artist, k)] <= end) / 3.6e6

    sessions = _sessions(rows)
    lengths = sorted((s[-1].dt - (s[0].dt - timedelta(milliseconds=s[0].ms))).total_seconds() / 60 for s in sessions)
    longest = max(sessions, key=lambda s: sum(p.ms for p in s)) if sessions else None
    run_start, best = 0, (0, 0)  # longest back-to-back run of one artist, as (start index, length)
    for i, p in enumerate(rows):
        if i and rows[i - 1].artist != p.artist:
            run_start = i
        if i - run_start + 1 > best[1]:
            best = (run_start, i - run_start + 1)
    best_run = rows[best[0]:best[0] + best[1]]
    day_track = Counter((p.day, p.track, p.artist) for p in rows if p.track)
    (od, ot, oa), on = day_track.most_common(1)[0] if day_track else ((None, None, None), 0)
    biggest = []
    for d, h in daily.most_common(5):
        dr = _slice(c, d, d)
        biggest.append({"day": d.isoformat(), "hours": round(h, 1), "plays": len(dr), "top": _top(Counter(p.artist for p in dr), 1)[0]})

    shares = [h / total for h in artist_h.values()] if total else []
    entropy = -sum(s * math.log(s) for s in shares if s > 0)
    known = [p for p in rows if p.shuffle is not None]
    starts = Counter(STARTS.get(p.start, "Other") for p in rows if p.start)
    devices, countries, country_days = Counter(), Counter(), defaultdict(set)
    phones = {}
    for p in rows:
        if store.device(p.platform):
            devices[store.device(p.platform)] += p.ms / 3.6e6
            ph = phone(p.platform)
            if ph:
                e = phones.setdefault(ph, {"name": ph, "first": p.day, "last": p.day, "hours": 0})
                e["last"], e["hours"] = p.day, e["hours"] + p.ms / 3.6e6
        if p.country:
            countries[p.country] += p.ms / 3.6e6
            country_days[p.country].add(p.day)
    parts = {"Night (0 to 5)": range(0, 5), "Morning (5 to 12)": range(5, 12),
             "Afternoon (12 to 17)": range(12, 17), "Evening (17 to 24)": range(17, 24)}

    def summary(rs, days):
        h = hours(rs)
        return {"hours": round(h, 1), "plays": len(rs), "artists": len({p.artist for p in rs}),
                "tracks": len({(p.track, p.artist) for p in rs}), "days": days,
                "listening_days": len({p.day for p in rs}), "per_day": round(h / days, 2) if days else 0}

    return {
        "start": start.isoformat(), "end": end.isoformat(), "days": n_days,
        "data_start": data_start.isoformat(), "data_end": data_end.isoformat(),
        "summary": {**summary(rows, n_days), "new_artists": len(new_here),
                    "per_listening_day": round(total / len(daily), 2) if daily else 0},
        # None, not []: an empty list is truthy in JavaScript and the page would compare against nothing
        "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat(),
                     **summary(prev, (prev_end - prev_start).days + 1)} if prev else None,
        "today": {"weekday": today.weekday(), "avg": weekday_avg[today.weekday()]},
        "weekday_avg": weekday_avg,
        "hour_avg_min": [round(60 * hour_h[h] / n_days, 1) for h in range(24)],
        "heat": [[round(60 * heat[w][h] / wd_days[w], 1) if wd_days[w] else 0 for h in range(24)] for w in range(7)],
        "distribution": {"labels": ["None", "Under 30 min", "30 to 60 min", "1 to 2 h", "2 to 3 h", "3 to 5 h", "5 to 8 h", "8 h+"],
                         "days": dist},
        "calendar": {d.isoformat(): round(h, 2) for d, h in daily.items()},
        "series": _series(rows, start, end, first),
        "streaks": _streaks(set(daily), start, end),
        "biggest_days": biggest,
        "sessions": {"count": len(sessions), "median_min": round(lengths[len(lengths) // 2]) if lengths else 0,
                     "longest": longest and {"start": (longest[0].dt - timedelta(milliseconds=longest[0].ms)).isoformat(timespec="minutes"),
                                             "minutes": round(sum(p.ms for p in longest) / 60000), "plays": len(longest),
                                             "top": _top(Counter(p.artist for p in longest), 1)[0]}},
        "binge": {"artist": best_run[0].artist, "plays": len(best_run), "day": best_run[0].day.isoformat(),
                  "minutes": round(sum(p.ms for p in best_run) / 60000)} if best_run else None,
        "obsession": ot and {"track": ot, "artist": oa, "day": od.isoformat(), "plays": on},
        "top_artists": top_artists, "top_tracks": top_tracks, "top_albums": top_albums,
        "movers": movers, "discoveries": discoveries,
        "taste": {"effective_artists": round(math.exp(entropy)) if shares else 0,
                  "top10_share": round(100 * sum(h for _, h in artist_h.most_common(10)) / total) if total else 0,
                  "fresh_share": round(100 * fresh / total) if total else 0},
        "habits": {"skip_rate": round(100 * sum(map(_is_skip, rows)) / len(rows)) if rows else 0,
                   "shuffle": round(100 * sum(p.shuffle for p in known) / len(known)) if known else None,
                   "offline": round(100 * sum(1 for p in rows if p.offline) / len(rows)) if rows else 0,
                   "starts": [[k, round(100 * n / sum(starts.values()))] for k, n in starts.most_common()],
                   "parts_of_day": [[k, round(100 * sum(hour_h[h] for h in r) / total) if total else 0] for k, r in parts.items()]},
        "devices": [[d, round(h, 1)] for d, h in devices.most_common(8)],
        "phones": sorted(({**e, "first": e["first"].isoformat(), "last": e["last"].isoformat(), "hours": round(e["hours"])}
                          for e in phones.values() if e["hours"] >= 1), key=lambda e: e["first"]),
        "countries": [{"code": k, "hours": round(h, 1), "days": len(country_days[k])} for k, h in countries.most_common(12)],
        "clock": clock(rows),
        "travel": travel(c, start, end),
    }


def sound(rows, lf, n_artists=40, label="Reading the genres of this period"):
    """Genre shares for a set of plays: each top artist's hours spread over their Last.fm tags."""
    h = Counter()
    for p in rows:
        h[p.artist] += p.ms / 3.6e6
    tags = Counter()
    for a, hrs in store.track("explore", h.most_common(n_artists), label):
        for t, n in lf.top_tags(a)[:5]:
            if recommend.good_tag(t, a):
                tags[t] += hrs * n / 100
    total = sum(tags.values()) or 1
    return {t: 100 * v / total for t, v in tags.items()}


def with_sound(r, lf):
    """Adds "sound" to an explore() result: top genres, and how each moved since the previous period."""
    c = plays()
    cur = sound(_slice(c, date.fromisoformat(r["start"]), date.fromisoformat(r["end"])), lf)
    prev = sound(_slice(c, date.fromisoformat(r["previous"]["start"]), date.fromisoformat(r["previous"]["end"])), lf,
                 label="Reading the genres of the period before") if r.get("previous") else {}
    top = sorted(cur, key=cur.get, reverse=True)[:10]
    r["sound"] = [{"tag": t, "share": round(cur[t], 1), "was": round(prev[t], 1) if t in prev else (0 if prev else None)} for t in top]
    return r


def on_this_day(today=None):
    """What you played on today's date in each earlier year."""
    c = plays()
    today = today or date.today()
    out = []
    for y in range(c["rows"][0].day.year, today.year + 1) if c["rows"] else []:
        try:
            d = today.replace(year=y)
        except ValueError:  # Feb 29 in a non-leap year
            continue
        rows = _slice(c, d, d)
        if rows and d < today:
            tracks = Counter((p.track, p.artist) for p in rows if p.track)
            (t, a), n = tracks.most_common(1)[0] if tracks else ((None, None), 0)
            out.append({"day": d.isoformat(), "hours": round(hours(rows), 1), "plays": len(rows),
                        "artist": _top(Counter(p.artist for p in rows), 1)[0], "track": t, "track_artist": a})
    return out[::-1]


def artist(name, lf=None, start=None, end=None):
    """Everything about one artist: all-time curve, totals, when you play them, their songs you play most,
    and (with Last.fm) who sounds like them that you haven't heard, plus their big songs you've missed."""
    c = plays()
    rows_all = c["rows"]
    mine = [p for p in rows_all if p.artist == name]
    if not mine:
        return {"error": f"No plays of {name}."}
    data_start, data_end = rows_all[0].day, rows_all[-1].day
    start, end = max(_day(start, data_start), data_start), min(_day(end, data_end), data_end)
    inr = [p for p in mine if start <= p.day <= end]

    months, m = [], date(data_start.year, data_start.month, 1)
    while m <= data_end:
        months.append(m.isoformat()[:7])
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    mine_m, all_m = Counter(), Counter()
    for p in rows_all:
        all_m[p.dt.strftime("%Y-%m")] += p.ms
    for p in mine:
        mine_m[p.dt.strftime("%Y-%m")] += p.ms
    totals = Counter()
    for p in rows_all:
        totals[p.artist] += p.ms
    rank = 1 + sum(1 for a, v in totals.items() if v > totals[name])
    days = Counter()
    for p in mine:
        days[p.day] += p.ms / 3.6e6
    streak = best = 0
    prev_day = None
    for d in sorted(days):
        streak = streak + 1 if prev_day and d - prev_day == timedelta(days=1) else 1
        best, prev_day = max(best, streak), d

    tn, th, ts, tfirst = Counter(), Counter(), Counter(), {}
    for p in inr:
        k = p.track or "(unknown)"
        tn[k] += 1
        th[k] += p.ms / 3.6e6
        ts[k] += _is_skip(p)
        tfirst.setdefault(k, p.day)
    heat = [[0.0] * 24 for _ in range(7)]
    albums = Counter()
    for p in inr:
        heat[p.dt.weekday()][p.dt.hour] += p.ms / 3.6e6
        if p.album:
            albums[p.album] += p.ms / 3.6e6
    in_total = hours(_slice(c, start, end))
    out = {
        "name": name, "start": start.isoformat(), "end": end.isoformat(),
        "months": months, "hours_by_month": [round(mine_m[k] / 3.6e6, 2) for k in months],
        "share_by_month": [round(100 * mine_m[k] / all_m[k], 1) if all_m[k] else 0 for k in months],
        "total_hours": round(hours(mine), 1), "total_plays": len(mine), "rank": rank,
        "first": {"day": mine[0].day.isoformat(), "track": mine[0].track},
        "last": {"day": mine[-1].day.isoformat(), "track": mine[-1].track},
        "peak_month": max(mine_m, key=mine_m.get), "peak_month_hours": round(max(mine_m.values()) / 3.6e6, 1),
        "biggest_day": {"day": max(days, key=days.get).isoformat(), "hours": round(max(days.values()), 1)},
        "days_played": len(days), "longest_streak": best,
        "range_hours": round(hours(inr), 1), "range_plays": len(inr),
        "range_share": round(100 * hours(inr) / in_total, 1) if in_total else 0,
        "tracks": [{"track": t, "plays": tn[t], "hours": round(th[t], 1), "skip_rate": round(100 * ts[t] / tn[t]),
                    "first": tfirst[t].isoformat()} for t, _ in tn.most_common(20)],
        "albums": [[a, round(h, 1)] for a, h in albums.most_common(8)],
        "heat": [[round(h, 2) for h in row] for row in heat],
        "skip_rate": round(100 * sum(map(_is_skip, inr)) / len(inr)) if inr else 0,
    }
    if lf:
        try:
            info = lf.info(name) or {}
            out["listeners"], out["tags"] = info.get("listeners"), [t for t in info.get("tags", []) if recommend.good_tag(t, name)][:6]
            played = {store.norm(p.artist) for p in rows_all}
            known = recommend.known_set(played)
            out["similar"] = [{"name": a, "match": round(m, 2)} for a, m in lf.similar(name)[:40]
                              if not recommend.is_known(a, known)][:8]
            heard = {store.track_key(p.track or "") for p in mine}
            out["missed"] = [t for t in lf.top_tracks(name) if store.track_key(t) not in heard][:8]
        except Exception as e:  # Last.fm is extra here; the listening stats stand on their own
            out["lastfm_note"] = str(e)
    return out


def day(d):
    """Play-by-play for one local day."""
    c = plays()
    when = _day(d, None)
    if not when:
        return {"error": "Bad date."}
    rows = _slice(c, when, when)
    return {"day": when.isoformat(), "hours": round(hours(rows), 2), "plays": [
        {"time": (p.dt - timedelta(milliseconds=p.ms)).strftime("%H:%M"), "artist": p.artist, "track": p.track,
         "album": p.album, "minutes": round(p.ms / 60000, 1), "skipped": _is_skip(p), "device": store.device(p.platform),
         "how": STARTS.get(p.start)} for p in rows],
        "sessions": len(_sessions(rows))}


# ---- music by the clock: what's distinctive about each part of your day
PARTS = [("Late night", 0, 5), ("Morning", 5, 12), ("Afternoon", 12, 17), ("Evening", 17, 24)]


def clock(rows, top=6):
    """For each part of the day, the artists and songs you play there far more than usual: their share of that
    part's listening over their share of all your listening (the "lift"). Ranked by hours x log(lift), so a big
    artist that leans nocturnal beats a one-off that happened to play once at 3am."""
    all_h, all_n = Counter(), Counter()
    for p in rows:
        all_h[p.artist] += p.ms / 3.6e6
        all_n[(p.track, p.artist)] += 1
    total_h, total_n = sum(all_h.values()) or 1, sum(all_n.values()) or 1
    out = []
    for name, a, b in PARTS:
        part = [p for p in rows if a <= p.dt.hour < b]
        ph, pn = Counter(), Counter()
        for p in part:
            ph[p.artist] += p.ms / 3.6e6
            pn[(p.track, p.artist)] += 1
        part_h, part_n = sum(ph.values()), sum(pn.values())
        entry = {"name": name, "from": a, "to": b, "hours": round(part_h, 1),
                 "share": round(100 * part_h / total_h) if rows else 0, "artists": [], "tracks": []}
        if part_h >= 0.5:
            # A slot holding 37% of your music can't give anyone much more than 2.7x, so the bar for "distinctive"
            # scales with the slot: 1.4x for small slots, down to 1.2x for the big ones.
            bar = max(1.2, min(1.4, 1 + 0.4 * (1 - 2 * part_h / total_h)))
            lift = {x: (h / part_h) / (all_h[x] / total_h) for x, h in ph.items() if h >= max(0.25, 0.004 * part_h)}
            best = sorted((x for x in lift if lift[x] >= bar), key=lambda x: -ph[x] * math.log(lift[x]))[:top]
            entry["artists"] = [{"name": x, "hours": round(ph[x], 1), "lift": round(lift[x], 1),
                                 "of_theirs": round(100 * ph[x] / all_h[x])} for x in best]
            tl = {k: (n / part_n) / (all_n[k] / total_n) for k, n in pn.items() if n >= 3 and k[0]}
            tbest = sorted((k for k in tl if tl[k] >= bar), key=lambda k: -pn[k] * math.log(tl[k]))[:5]
            entry["tracks"] = [{"track": t, "artist": x, "plays": pn[(t, x)], "of_theirs": round(100 * pn[(t, x)] / all_n[(t, x)])}
                               for t, x in tbest]
        out.append(entry)
    night = [p for p in rows if p.dt.hour < 5]
    latest = max(night, key=lambda p: (p.dt.hour, p.dt.minute), default=None)
    small = Counter(p.artist for p in rows if 2 <= p.dt.hour < 4)  # the literal 3am slot
    return {"parts": out, "latest": latest and {"time": latest.dt.strftime("%H:%M"), "day": latest.day.isoformat(),
                                                "artist": latest.artist, "track": latest.track},
            "three_am": _top(small, 1)[0] if small else None}


# ---- travel: trips abroad, found from the country each play came from
def travel(c, start, end, gap=3):
    """Days whose music mostly came from a country other than home, merged into trips (up to `gap` quiet days
    inside one trip). Home is where you've listened most, all time."""
    home, rows = c["home"], _slice(c, start, end)
    by_day, per_country, cdays, cfirst, clast = defaultdict(Counter), Counter(), defaultdict(set), {}, {}
    for p in rows:
        if p.country:
            by_day[p.day][p.country] += p.ms / 3.6e6
            per_country[p.country] += p.ms / 3.6e6
            cdays[p.country].add(p.day)
            cfirst.setdefault(p.country, p.day)
            clast[p.country] = p.day
    trips = []
    for d in sorted(by_day):
        where = by_day[d].most_common(1)[0][0]
        if where == home:
            continue
        if trips and trips[-1]["code"] == where and (d - trips[-1]["_end"]).days <= gap + 1:
            trips[-1]["_end"] = d
        else:
            trips.append({"code": where, "_start": d, "_end": d})
    out = []
    for t in trips:
        tr = [p for p in _slice(c, t["_start"], t["_end"]) if p.country == t["code"]]
        songs = Counter((p.track, p.artist) for p in tr if p.track)
        (track, artist), _ = songs.most_common(1)[0] if songs else ((None, None), 0)
        out.append({"code": t["code"], "start": t["_start"].isoformat(), "end": t["_end"].isoformat(),
                    "days": (t["_end"] - t["_start"]).days + 1, "hours": round(hours(tr), 1),
                    "top_artist": _top(Counter(p.artist for p in tr), 1)[0], "track": track, "track_artist": artist})
    return {"home": home, "trips": out[::-1],
            "countries": [{"code": k, "hours": round(h, 1), "days": len(cdays[k]), "first": cfirst[k].isoformat(),
                           "last": clast[k].isoformat(), "trips": sum(1 for t in out if t["code"] == k)}
                          for k, h in per_country.most_common()]}


# ---- albums: played front to back, or cherry-picked
def album_runs(rows, gap_min=10):
    """Back-to-back plays from one album, each starting within gap_min of the last one ending."""
    runs = []
    for p in rows:
        k = (p.album, p.artist) if p.album else None
        if k and runs and runs[-1][0] == k and (p.dt - timedelta(milliseconds=p.ms)) - runs[-1][1][-1].dt <= timedelta(minutes=gap_min):
            runs[-1][1].append(p)
        elif k:
            runs.append((k, [p]))
    return runs


def albums(rows, lf=None, n=60):
    """Per album: songs heard of its tracklist, how concentrated your plays are, and "full listens" (runs that
    covered most of the album). Tracklist sizes come from Last.fm; without them a full listen is 5+ songs in a row."""
    h, songs, names = Counter(), defaultdict(Counter), {}
    for p in rows:
        if p.album:
            k, tk = (p.album, p.artist), store.track_key(p.track or "")
            h[k] += p.ms / 3.6e6
            songs[k][tk] += 1
            names.setdefault((k, tk), p.track)
    keys = [k for k, v in h.most_common(n) if v >= 0.5]
    sizes = {}
    for k in store.track("explore", keys, "Looking up album tracklists") if lf else []:
        try:
            sizes[k] = lf.album_tracks(k[1], k[0])
        except Exception:  # one odd album name shouldn't sink the section; it falls back to the 5-in-a-row rule
            sizes[k] = None
    full, run_hours = Counter(), 0.0
    for k, run in album_runs(rows):
        size = sizes.get(k)
        if size is not None and size < 3:  # singles and 2-song releases aren't albums
            continue
        need = max(3, round(0.6 * size)) if size else 5
        if len({store.track_key(p.track or "") for p in run}) >= need:
            full[k] += 1
            run_hours += hours(run)
    front, cherry = [], []
    for k in keys:
        heard, size = len(songs[k]), sizes.get(k)
        tk, tn = songs[k].most_common(1)[0]
        top_share = tn / sum(songs[k].values())
        row = {"album": k[0], "artist": k[1], "hours": round(h[k], 1), "heard": heard, "total": size,
               "full": full[k], "top_track": names[(k, tk)], "top_share": round(100 * top_share)}
        if full[k] >= 2:
            front.append(row)
        elif (size and size >= 5 and (heard / size <= 0.4 or top_share >= 0.6)) or (not size and heard <= 2 and top_share >= 0.7):
            cherry.append(row)  # few of its songs, or one song carrying most of the plays
    total = hours(rows)
    return {"front_to_back": sorted(front, key=lambda r: -r["full"])[:8], "cherry_picked": cherry[:8],
            "album_share": round(100 * run_hours / total) if total else 0, "tracklists": bool(sizes)}


def with_albums(r, lf):
    c = plays()
    r["albums"] = albums(_slice(c, date.fromisoformat(r["start"]), date.fromisoformat(r["end"])), lf)
    return r


# ---- song pages
def song(artist_name, track, lf=None, start=None, end=None):
    """One song's life in your history. Versions with the same core title are merged ("Nights (Remastered)")."""
    c = plays()
    rows_all, key = c["rows"], store.track_key(track or "")
    idx = [i for i, p in enumerate(rows_all) if p.artist == artist_name and c["keys"][i] == key]
    if not idx:
        return {"error": f"No plays of {track} by {artist_name}."}
    mine = [rows_all[i] for i in idx]
    data_start, data_end = rows_all[0].day, rows_all[-1].day
    start, end = max(_day(start, data_start), data_start), min(_day(end, data_end), data_end)
    months, m = [], date(data_start.year, data_start.month, 1)
    while m <= data_end:
        months.append(m.isoformat()[:7])
        m = _add_months(m, 1)
    per_month, per_day, per_year, skips = Counter(), Counter(), Counter(), Counter()
    heat = [[0] * 24 for _ in range(7)]
    for p in mine:
        per_month[p.dt.strftime("%Y-%m")] += 1
        per_day[p.day] += 1
        per_year[p.dt.year] += 1
        skips[p.dt.year] += _is_skip(p)
        heat[p.dt.weekday()][p.dt.hour] += 1
    streak = best = 0
    prev = None
    for d in sorted(per_day):
        streak = streak + 1 if prev and (d - prev).days == 1 else 1
        best, prev = max(best, streak), d
    # What you play around it: the song right before and after, when they're in the same listening session
    before, after = Counter(), Counter()
    for i in idx:
        p = rows_all[i]
        if i > 0:
            q = rows_all[i - 1]
            if (p.dt - timedelta(milliseconds=p.ms)) - q.dt <= timedelta(minutes=30) and c["keys"][i - 1] != key:
                before[(q.track, q.artist)] += 1
        if i + 1 < len(rows_all):
            q = rows_all[i + 1]
            if (q.dt - timedelta(milliseconds=q.ms)) - p.dt <= timedelta(minutes=30) and c["keys"][i + 1] != key:
                after[(q.track, q.artist)] += 1
    rank = 1 + sum(1 for v in c["song_plays"].values() if v > len(mine))
    starts = Counter(STARTS.get(p.start, "Other") for p in mine if p.start)
    titles = Counter(p.track for p in mine)
    out = {
        "artist": artist_name, "track": titles.most_common(1)[0][0], "versions": [t for t, _ in titles.most_common()],
        "albums": [a for a, _ in Counter(p.album for p in mine if p.album).most_common(3)],
        "start": start.isoformat(), "end": end.isoformat(),
        "plays": len(mine), "hours": round(hours(mine), 1), "rank": rank, "days_played": len(per_day),
        "range_plays": sum(1 for p in mine if start <= p.day <= end),
        "first": mine[0].day.isoformat(), "last": mine[-1].day.isoformat(),
        "most_in_a_day": {"day": max(per_day, key=per_day.get).isoformat(), "plays": max(per_day.values())},
        "longest_streak": best,
        "months": months, "plays_by_month": [per_month[k] for k in months],
        "by_year": [{"year": y, "plays": per_year[y], "skip_rate": round(100 * skips[y] / per_year[y])} for y in sorted(per_year)],
        "skip_rate": round(100 * sum(skips.values()) / len(mine)),
        "heat": heat,
        "starts": [[k, round(100 * v / sum(starts.values()))] for k, v in starts.most_common()],
        "before": [{"track": t, "artist": a, "times": n} for (t, a), n in before.most_common(5) if n >= 2],
        "after": [{"track": t, "artist": a, "times": n} for (t, a), n in after.most_common(5) if n >= 2],
    }
    if lf:
        try:
            info = lf.track_info(artist_name, out["track"]) or {}
            out["listeners"] = info.get("listeners")
            out["tags"] = [t for t in info.get("tags", []) if recommend.good_tag(t, artist_name)][:5]
        except Exception as e:  # Last.fm is extra here
            out["lastfm_note"] = str(e)
    return out


# ---- compare two periods side by side
def compare(a_start, a_end, b_start, b_end, lf=None):
    A, B = explore(a_start, a_end), explore(b_start, b_end)
    for r in (A, B):
        if "error" in r or r.get("empty"):
            return {"error": r.get("error", "No listening data yet.")}
    c = plays()
    ra = _slice(c, date.fromisoformat(A["start"]), date.fromisoformat(A["end"]))
    rb = _slice(c, date.fromisoformat(B["start"]), date.fromisoformat(B["end"]))
    ha, hb = Counter(), Counter()
    for p in ra:
        ha[p.artist] += p.ms / 3.6e6
    for p in rb:
        hb[p.artist] += p.ms / 3.6e6
    ta, tb = sum(ha.values()) or 1, sum(hb.values()) or 1
    # Overlap of the two mixes: each shared artist counts at the smaller of its two shares.
    # 100% = the same mix in both periods, 0% = nothing in common.
    overlap = sum(min(ha[x] / ta, hb[x] / tb) for x in set(ha) & set(hb))
    both = sorted((x for x in set(ha) & set(hb) if ha[x] >= 0.5 and hb[x] >= 0.5), key=lambda x: -min(ha[x], hb[x]))[:10]
    out = {"a": A, "b": B, "overlap": round(100 * overlap),
           "both": [{"name": x, "a": round(ha[x], 1), "b": round(hb[x], 1)} for x in both],
           "only_a": [{"name": x, "hours": round(h, 1)} for x, h in ha.most_common(200) if hb[x] < 0.1][:10],
           "only_b": [{"name": x, "hours": round(h, 1)} for x, h in hb.most_common(200) if ha[x] < 0.1][:10]}
    if lf:
        try:
            sa, sb = sound(ra, lf), sound(rb, lf, label="Reading the genres of the second period")
            tags = sorted(set(sa) | set(sb), key=lambda t: -(sa.get(t, 0) + sb.get(t, 0)))[:10]
            out["sound"] = [{"tag": t, "a": round(sa.get(t, 0), 1), "b": round(sb.get(t, 0), 1)} for t in tags]
        except Exception as e:  # genres are extra
            out["sound_note"] = f"Genres need Last.fm. {e}"
    return out
