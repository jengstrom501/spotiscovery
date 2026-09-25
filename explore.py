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
        first_artist, first_track = {}, {}
        for p in rows:
            first_artist.setdefault(p.artist, p.day)
            first_track.setdefault((p.artist, store.track_key(p.track or "")), p.day)
        _cache.update(v=store.VERSION[0], rows=rows, days=[p.day for p in rows],
                      first_artist=first_artist, first_track=first_track)
    return _cache


def _day(s, default):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


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
    fresh = sum(p.ms for p in rows if start <= c["first_track"].get((p.artist, store.track_key(p.track or "")), start) <= end) / 3.6e6

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
