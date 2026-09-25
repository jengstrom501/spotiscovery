"""What Deep Cuts figured out about you: candidate findings for a period, each scored by how unusual it is,
so the recap card leads with the most telling ones instead of a top-5 list.

Each insight: {id, kind, kicker, value, text, score 0..1, artist?}. `kind` keeps the card varied (one per kind);
`artist` lets the card show a photo. Scores are hand-set curves, each noted where it's defined: they only
need to rank findings against each other, not be calibrated probabilities."""
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, timedelta

import explore
import store


def clamp(x):
    return max(0.0, min(1.0, x))


def fmt_n(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1e3)}k" if n >= 1e3 else str(n)


def fmt_h(h):
    # non-breaking space: the card wraps lines, and "3.1" / "h" shouldn't split
    return f"{round(h)}\u00a0h" if h >= 10 else f"{h:.1f}".rstrip("0").rstrip(".") + "\u00a0h" if h >= 1 else f"{round(h * 60)}\u00a0min"


def short(d, year=False):
    """"Aug 27", or "Aug 27, 2019" when the period spans years (no zero padding; %-d isn't portable to Windows)"""
    d = date.fromisoformat(d)
    return f"{d:%b} {d.day}" + (f", {d.year}" if year else "")


def typical_listeners(rows, lf, n=60):
    """Hours-weighted median Last.fm listener count of the artists you played (top n). None without data."""
    h = Counter()
    for p in rows:
        h[p.artist] += p.ms / 3.6e6
    pairs = []
    for a, hrs in h.most_common(n):
        inf = lf.info(a)
        if inf and inf.get("listeners"):
            pairs.append((inf["listeners"], hrs))
    total = sum(x for _, x in pairs)
    acc = 0
    for listeners, hrs in sorted(pairs):
        acc += hrs
        if acc >= total / 2:
            return listeners
    return None


def song_pairs(c, lo, hi, gap_min=30):
    """(song A, song B) played back to back within one session, counted over the period."""
    rows, keys, pairs, names = c["rows"], c["keys"], Counter(), {}
    for i in range(max(lo, 1), hi):
        p, q = rows[i], rows[i - 1]
        if keys[i] == keys[i - 1] and p.artist == q.artist:
            continue  # a song on repeat isn't a pairing
        if (p.dt - timedelta(milliseconds=p.ms)) - q.dt > timedelta(minutes=gap_min) or not (p.track and q.track):
            continue
        if store.NOISE.search(p.track) or store.NOISE.search(q.track):
            continue  # "Distant Thunder Only" into "Distant Thunder Sounds Only" is a sleep playlist, not a habit
        k = ((q.artist, keys[i - 1]), (p.artist, keys[i]))
        pairs[k] += 1
        names.setdefault(k, ((q.track, q.artist), (p.track, p.artist)))
    return pairs, names


def comebacks(c, lo, hi, min_years=2):
    """Songs you returned to after a long silence: their first play in the period vs the last one before it."""
    rows, keys, last_before = c["rows"], c["keys"], {}
    for i in range(lo):
        last_before[(rows[i].artist, keys[i])] = rows[i].day
    best, seen = None, set()
    for i in range(lo, hi):
        k = (rows[i].artist, keys[i])
        if k in seen or k not in last_before or not rows[i].track:
            continue
        seen.add(k)
        gap = (rows[i].day - last_before[k]).days / 365.25
        if gap >= min_years and (best is None or gap > best[0]):
            best = (gap, rows[i], last_before[k])
    return best


def find(lf, start, end, label, prev_label=None, recap=None, eras=None):
    """All candidate insights for [start, end], best first. prev_label names the previous period in sentences."""
    E = explore.explore(start, end)
    if "error" in E or E.get("empty"):
        return []
    c = explore.plays()
    s, e = date.fromisoformat(E["start"]), date.fromisoformat(E["end"])
    lo, hi = bisect_left(c["days"], s), bisect_right(c["days"], e)
    rows = c["rows"][lo:hi]
    pv = E.get("previous") if prev_label else None
    all_time = s == c["rows"][0].day and e == c["rows"][-1].day
    multi = s.year != e.year  # dates in sentences need the year when the period spans several
    prev_rows = explore._slice(c, date.fromisoformat(pv["start"]), date.fromisoformat(pv["end"])) if pv else []
    out = []
    add = lambda **kw: out.append(kw)

    # Taste shift: the genre that moved most since the previous period (10 points of your music = a big shift)
    if lf and prev_rows:
        try:
            now, before = explore.sound(rows, lf, label="Reading this period's genres"), explore.sound(prev_rows, lf, label="Reading the period before")
            moves = sorted(((t, now.get(t, 0) - before.get(t, 0)) for t in set(now) | set(before)), key=lambda x: -abs(x[1]))
            if moves and abs(moves[0][1]) >= 3:
                t, d = moves[0]
                add(id="genre_shift", kind="taste", kicker="Your sound moved", value=f"{t} {'+' if d > 0 else '-'}{abs(round(d))} pts",
                    text=f"{t.capitalize()} {'rose' if d > 0 else 'fell'} from {before.get(t, 0):.0f}% to {now.get(t, 0):.0f}% of your music since {prev_label}.",
                    score=clamp(abs(d) / 10))
        except Exception:
            pass

    # Overlap with the previous period: low = you reinvented your listening, high = creature of habit
    if prev_rows:
        ha, hb = Counter(), Counter()
        for p in prev_rows:
            ha[p.artist] += p.ms
        for p in rows:
            hb[p.artist] += p.ms
        ta, tb = sum(ha.values()) or 1, sum(hb.values()) or 1
        o = round(100 * sum(min(ha[x] / ta, hb[x] / tb) for x in set(ha) & set(hb)))
        if o <= 35:
            add(id="overlap", kind="overlap", kicker="Reinvention", value=f"{o}% overlap",
                text=f"Only {o}% of your listening matched {prev_label}. Mostly new artists, mostly new habits.", score=clamp((40 - o) / 30))
        elif o >= 55:
            add(id="overlap", kind="overlap", kicker="Creature of habit", value=f"{o}% the same",
                text=f"{o}% of your listening matched {prev_label}. You know what you like.", score=clamp((o - 50) / 40))

    # Your 3am music: the late-night artist with the biggest lift (6x at night is striking, 2x is not)
    night = E["clock"]["parts"][0]
    if night["artists"]:
        a = night["artists"][0]
        add(id="three_am", kind="clock", kicker="Your 3am music", value=a["name"], artist=a["name"],
            text=f"You play {a['name']} {a['lift']:g}× more late at night than usual. {a['of_theirs']}% of their plays happen after midnight.",
            score=clamp((a["lift"] - 1.5) / 5) * clamp(a["hours"] / 3))

    # Deepest cut: the least-known artist you really played (1k listeners = very deep, 1M = not at all)
    deep = recap and recap.get("deepest")
    if deep:
        add(id="deepest", kind="deep", kicker="Deepest cut", value=deep["name"], artist=deep["name"],
            text=f"Only {fmt_n(deep['listeners'])} people listen to {deep['name']} on Last.fm. You gave them {fmt_h(deep['hours'])}.",
            score=clamp((6 - math.log10(max(deep["listeners"], 1))) / 3))

    # Mainstream drift: your typical artist's audience vs the previous period (60% change = max)
    if lf and prev_rows:
        try:
            now_l, then_l = typical_listeners(rows, lf), typical_listeners(prev_rows, lf)
            if now_l and then_l:
                pct = round(100 * (now_l - then_l) / then_l)
                if abs(pct) >= 15:
                    add(id="mainstream", kind="mainstream", kicker="Went underground" if pct < 0 else "Went mainstream",
                        value=f"{'' if pct < 0 else '+'}{pct}%",
                        text=f"Your typical artist has {fmt_n(now_l)} Last.fm listeners, {abs(pct)}% {'fewer' if pct < 0 else 'more'} than in {prev_label}.",
                        score=clamp(abs(pct) / 60))
        except Exception:
            pass

    # A discovery that took over: first played in this period and already in your top 10
    top10 = [x["name"] for x in E["top_artists"][:10]]
    found = [] if all_time else [d for d in E["discoveries"] if d["name"] in top10]
    if found:
        d = found[0]
        rank = top10.index(d["name"]) + 1
        add(id="discovery", kind="discovery", kicker="Found and kept", value=d["name"], artist=d["name"],
            text=f"You first played {d['name']} on {short(d['first'])}. By the end they were your #{rank} artist.",
            score=0.95 - 0.06 * rank)

    # Drifted away: someone who had 10+ hours before and lost most of them (20 hours lost = max)
    drops = [m for m in E["movers"]["down"] if m["was"] >= 10 and m["hours"] <= 0.45 * m["was"]]
    if drops and prev_label:
        m = max(drops, key=lambda m: m["was"] - m["hours"])
        add(id="drifted", kind="drift", kicker="Drifted away", value=m["name"], artist=m["name"],
            text=f"From {fmt_h(m['was'])} in {prev_label} to {fmt_h(m['hours'])} in {label}.", score=clamp((m["was"] - m["hours"]) / 20))

    # Streak of daily listening (120 days = max)
    st = E["streaks"]["longest"]
    if st and st["days"] >= 14:
        add(id="streak", kind="streak", kicker="Longest streak", value=f"{st['days']} days",
            text=f"Music every single day from {short(st['start'], multi)} to {short(st['end'], multi)}.", score=clamp(st["days"] / 120))

    # One-day obsession (13+ plays of one song in a day = max)
    ob = E["obsession"]
    if ob and ob["plays"] >= 4:
        add(id="obsession", kind="repeat", kicker="One-day obsession", value=f"{ob['plays']}× in a day", artist=ob["artist"],
            text=f"{ob['track']} by {ob['artist']}, over and over on {short(ob['day'], multi)}.", score=clamp((ob["plays"] - 3) / 10))

    # Songs you always play together (13 times back to back = max)
    pairs, names = song_pairs(c, lo, hi)
    if pairs:
        song_n = Counter(zip((p.artist for p in rows), c["keys"][lo:hi]))
        # The strongest pairing: often, and a big share of the first song's plays (7 of 8 beats 9 of 60)
        k = max((k for k, n in pairs.items() if n >= 4), default=None, key=lambda k: pairs[k] * pairs[k] / song_n[k[0]])
        if k:
            n, of = pairs[k], song_n[k[0]]
            (ta, aa), (tb, ab) = names[k]
            add(id="pair", kind="pair", kicker="Inseparable", value=f"{n} of {of}",
                text=f"{n} of the {of} times you played {ta}, {tb}{'' if aa == ab else ' by ' + ab} came next.",
                score=clamp((n - 3) / 10) * (0.5 + 0.5 * n / of), artist=aa)

    # A comeback: a song you returned to after years (5 years = max)
    cb = comebacks(c, lo, hi)
    if cb:
        gap, p, was = cb
        add(id="comeback", kind="comeback", kicker="The comeback", value=f"{int(gap)} years later", artist=p.artist,
            text=f"You played {p.track} by {p.artist} again on {short(p.day.isoformat(), multi)}, {int(gap)} years after the last time.",
            score=clamp(gap / 5) * 0.9)

    # A trip and its soundtrack (a 2-week trip = max)
    trips = [t for t in E["travel"]["trips"] if t["track"] and t["days"] >= 2]
    if trips:
        t = max(trips, key=lambda t: t["days"])
        name = COUNTRIES.get(t["code"], t["code"])
        add(id="trip", kind="travel", kicker=f"Soundtrack of {name}", value=t["track"], artist=t["track_artist"],
            text=f"{t['days']} days in {name}, and {t['track']} by {t['track_artist']} was the song.", score=0.45 + clamp(t["days"] / 14) * 0.4)

    # Your weekday (a 2x gap between your biggest and quietest day = max)
    wd = E["weekday_avg"]
    hi_d, lo_d = max(range(7), key=wd.__getitem__), min(range(7), key=wd.__getitem__)
    if wd[lo_d] and wd[hi_d] / wd[lo_d] >= 1.3:
        add(id="weekday", kind="weekday", kicker=f"{DAYS[hi_d]}s are yours", value=fmt_h(wd[hi_d]),
            text=f"You average {fmt_h(wd[hi_d])} of music on {DAYS[hi_d]}s and {fmt_h(wd[lo_d])} on {DAYS[lo_d]}s.",
            score=clamp((wd[hi_d] / wd[lo_d] - 1.3) / 0.9))

    # New music share (the typical listener sits around a quarter; far from that either way is telling)
    fresh = E["taste"]["fresh_share"]
    if not all_time and (fresh >= 40 or fresh <= 12):
        add(id="fresh", kind="fresh", kicker="Always digging" if fresh >= 40 else "Old favorites", value=f"{fresh}% new",
            text=f"{fresh}% of your listening was songs you'd never played before." if fresh >= 40
            else f"Only {fresh}% of your listening was songs you hadn't played before.", score=clamp(abs(fresh - 26) / 30))

    # Skipper (1 in 3 skipped is a lot)
    sk = E["habits"]["skip_rate"]
    if sk >= 28:
        add(id="skips", kind="skips", kicker="Hard to please", value=f"{sk}% skipped",
            text=f"You skipped about 1 in {round(100 / sk)} songs before it ended.", score=clamp((sk - 22) / 25))

    # Biggest single day (14 hours = max)
    if E["biggest_days"]:
        b = E["biggest_days"][0]
        if b["hours"] >= 6:
            add(id="big_day", kind="day", kicker="Biggest day", value=fmt_h(b["hours"]), artist=b["top"],
                text=f"{short(b['day'], multi)}: {fmt_h(b['hours'])} of music, mostly {b['top']}.", score=clamp((b["hours"] - 5) / 9) * 0.8)

    # A new era that began in this period
    for era in eras or []:
        q_start = date(int(era["start"][:4]), (int(era["start"][-1]) - 1) * 3 + 1, 1)
        if s <= q_start <= e and era is not eras[0]:
            add(id="era", kind="era", kicker="A new era began", value=" × ".join(era["tags"]),
                text=f"Around {q_start:%B %Y} your {' × '.join(era['tags'])} era started, led by {', '.join(era['artists'][:2])}.",
                score=0.75, artist=era["artists"][0] if era["artists"] else None)

    # Your #1, as context rather than headline (only surfaces when little else does)
    if E["top_artists"]:
        t = E["top_artists"][0]
        was = f", up from #{t['prev_rank']}" if t.get("prev_rank") and t["prev_rank"] > 1 else ""
        add(id="top", kind="top", kicker="Your #1", value=t["name"], artist=t["name"],
            text=f"{fmt_h(t['hours'])}, {t['share']}% of your music{was}.", score=0.2)

    out.sort(key=lambda i: (-i["score"], PRIORITY.index(i["kind"]) if i["kind"] in PRIORITY else 99))
    kinds, best = set(), []
    for i in out:  # one per kind, so the card isn't three flavors of the same finding
        if i["kind"] not in kinds:
            kinds.add(i["kind"])
            best.append({**i, "score": round(i["score"], 2)})
    return best


# Tie-break when scores are equal: the more personal and surprising kinds first
PRIORITY = ["clock", "comeback", "deep", "pair", "discovery", "drift", "taste", "era", "overlap", "mainstream", "travel",
            "repeat", "streak", "fresh", "day", "weekday", "skips", "top"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Names for the trip insight's text (the card is drawn server-agnostic, so no browser Intl here). Unknown codes
# fall back to the code itself.
COUNTRIES = {"US": "the US", "GB": "the UK", "CA": "Canada", "MX": "Mexico", "DK": "Denmark", "SE": "Sweden", "NO": "Norway",
             "FI": "Finland", "DE": "Germany", "FR": "France", "ES": "Spain", "PT": "Portugal", "IT": "Italy", "NL": "the Netherlands",
             "BE": "Belgium", "CH": "Switzerland", "AT": "Austria", "HU": "Hungary", "CZ": "Czechia", "PL": "Poland", "IE": "Ireland",
             "IS": "Iceland", "GR": "Greece", "HR": "Croatia", "TR": "Turkey", "JP": "Japan", "KR": "South Korea", "CN": "China",
             "IN": "India", "TH": "Thailand", "VN": "Vietnam", "ID": "Indonesia", "AU": "Australia", "NZ": "New Zealand",
             "BR": "Brazil", "AR": "Argentina", "CO": "Colombia", "PE": "Peru", "CL": "Chile", "ZA": "South Africa",
             "EG": "Egypt", "MA": "Morocco", "AE": "the UAE", "IL": "Israel", "SG": "Singapore", "PH": "the Philippines",
             "CR": "Costa Rica", "DO": "the Dominican Republic", "PR": "Puerto Rico", "JM": "Jamaica", "BS": "the Bahamas"}
