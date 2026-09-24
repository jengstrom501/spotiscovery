"""Trends from your plays + the inputs recommend.py needs (seeds, known artists)."""
from collections import Counter, defaultdict
from datetime import datetime

import recommend
import store


def minutes_by_artist():
    with store.db() as c:
        return {a: ms / 60000 for a, ms in c.execute("SELECT artist, SUM(ms) FROM plays GROUP BY artist")}


def inputs():
    """(seeds, known) from the export plays and the Spotify snapshot, whichever you have."""
    snap = store.get("spotify", {})
    minutes = minutes_by_artist()
    seeds = recommend.seed_weights(minutes, snap.get("top", {}))
    known = set(seeds) | {store.norm(a) for a in snap.get("followed", []) + snap.get("saved_artists", [])}
    known |= {store.norm(p["artist"]) for p in snap.get("recent", [])}
    return seeds, known


def trends(lf, drift_artists=150, drift_tags=8):
    with store.db() as c:
        plays = c.execute("SELECT ts, artist, ms FROM plays ORDER BY ts").fetchall()
    snap = store.get("spotify", {})
    out = {"plays": len(plays), "api_top": snap.get("top", {})}
    if not plays:
        return out

    monthly, first_seen, per_year = defaultdict(float), {}, defaultdict(Counter)
    heat = [[0.0] * 24 for _ in range(7)]
    by_quarter = defaultdict(Counter)
    for ts, artist, ms in plays:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        mo, hrs = ts[:7], ms / 3.6e6
        monthly[mo] += hrs
        first_seen.setdefault(artist, mo)
        per_year[ts[:4]][artist] += hrs
        heat[d.weekday()][d.hour] += hrs  # ponytail: UTC, the export has no timezone per play
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
    })

    # Tag drift: each quarter's hours spread over the tags of your most-played artists.
    top = [a for a, _ in Counter({a: sum(q[a] for q in by_quarter.values()) for a in first_seen}).most_common(drift_artists)]
    tags = {a: [(t, n) for t, n in lf.top_tags(a)[:5] if recommend.good_tag(t, a)] for a in top}
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
    return out
