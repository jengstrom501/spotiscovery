"""How good is Similar sound, measured on your own history: hide a year, recommend from the plays before it, and
see where the artists you went on to adopt (30+ minutes of someone new to you that year) land. Repeated for your
last three years. Run: python backtest.py  (needs imported history and a Last.fm key; the first run fetches a few
hundred Last.fm lists, then it's cached).

Two numbers per method: how many adopted artists reach the top 300 of the candidate ranking (dense enough to
compare methods), and how many make the final top 40 you'd see (the real test, but a handful at most). Both are a
floor: you never saw these lists, so a miss can still be a good pick."""
import os
import sys
from collections import Counter
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app  # noqa: E402,F401  (loads .env so lastfm sees the key)
import analyze  # noqa: E402
import explore  # noqa: E402
import lastfm  # noqa: E402
import recommend  # noqa: E402
import store  # noqa: E402


def split(back_days, min_minutes=30):
    """(plays before the hidden year, squash keys of artists adopted in it, first day of it)."""
    rows = explore.plays()["rows"]
    end = rows[-1].day - timedelta(days=back_days)
    cut = end - timedelta(days=365)
    before = [p for p in rows if p.day < cut]
    old = {store.squash(p.artist) for p in before}
    later = Counter()
    for p in rows:
        if cut <= p.day < end and store.squash(p.artist) not in old:
            later[p.artist] += p.ms / 60000
    return before, {store.squash(a) for a, m in later.items() if m >= min_minutes}, cut


def measure(before, truth, seeds, n_seeds, pool):
    known = recommend.known_set({p.artist for p in before})
    ranked = list(recommend.candidates(seeds, known, lastfm, n_seeds)[0])
    top300 = sum(store.squash(c) in truth for c in ranked[:300])
    recs = recommend.similar_recs(seeds, known, lastfm, n_seeds=n_seeds, pool=pool)
    return top300, [r["name"] for r in recs if store.squash(r["name"]) in truth]


def main():
    totals = Counter()
    for back in (0, 365, 730):
        before, truth, cut = split(back)
        if not before or not truth:
            continue
        alltime = Counter()
        for p in before:
            alltime[p.artist] += p.ms / 60000
        methods = {"all-time minutes, top 50 seeds (old)": (recommend.seed_weights(alltime, {}), 50, 150),
                   "taste now, top 300 seeds (current)": (recommend.seed_weights(analyze.taste_now(until=cut), {}), 300, 300)}
        print(f"Hidden year from {cut}: {len(truth)} artists adopted")
        for name, (seeds, n, pool) in methods.items():
            top300, hits = measure(before, truth, seeds, n, pool)
            totals[name, "top300"] += top300
            totals[name, "hits"] += len(hits)
            print(f"  {name:38s} top 300: {top300:2d}   top 40: {len(hits)}  {', '.join(hits)}")
    print("\nTotals:")
    for name in dict.fromkeys(k for k, _ in totals):
        print(f"  {name:38s} top 300: {totals[name, 'top300']:2d}   top 40: {totals[name, 'hits']}")


if __name__ == "__main__":
    main()
