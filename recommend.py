"""Scoring. `lf` is anything with lastfm.py's interface (similar, top_tags, info, tag_artists)."""
import math
import re
from collections import defaultdict
from itertools import combinations

from store import norm

JUNK_TAGS = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorites", "love",
    "awesome", "beautiful", "cool", "spotify", "albums i own", "under 2000 listeners", "all",
    "male vocalists", "female vocalists", "male vocalist", "female vocalist", "american", "british",
    "usa", "uk", "english", "canadian", "australian", "german", "swedish", "french", "japanese",
    "check out", "good", "amazing", "best", "music", "songs", "chill", "fun", "sexy",
}
DECADE = re.compile(r"^(\d0s|\d{4}s?|\d{4})$")
# Last.fm lists collab credits ("Future & Lil Uzi Vert") as their own artists.
COLLAB = re.compile(r"\s+(?:&|\+|x|feat\.?|ft\.?|featuring|with|and|vs\.?)\s+|\s*,\s*")


def is_known(name, known):
    """Known by full name, or a collab with anyone you already play.
    ponytail: separator split, so "Earth, Wind & Fire" only drops if you play an artist called "Fire"."""
    n = norm(name)
    return n in known or any(p in known for p in COLLAB.split(n) if p)


def good_tag(tag, artist=""):
    return tag not in JUNK_TAGS and not DECADE.match(tag) and tag != norm(artist) and len(tag) > 1


def seed_weights(minutes, top_ranks):
    """minutes: {artist: minutes played}; top_ranks: {range: [artist,...]} best first.
    Returns {norm(name): (display name, weight)}."""
    out = {}
    for name, m in minutes.items():
        out[norm(name)] = (name, math.log1p(m))
    for names in top_ranks.values():
        for i, name in enumerate(names):
            disp, w = out.get(norm(name), (name, 0.0))
            out[norm(name)] = (disp, w + 2 * (1 - i / max(len(names), 1)))
    return out


def top_seeds(seeds, n):
    return sorted(seeds.values(), key=lambda s: -s[1])[:n]


def tag_profile(seeds, lf, n=100):
    """{tag: weight}: your listening weight spread over each artist's Last.fm tags."""
    prof = defaultdict(float)
    for name, w in top_seeds(seeds, n):
        for tag, count in lf.top_tags(name)[:10]:
            if good_tag(tag, name):
                prof[tag] += w * count / 100
    return dict(sorted(prof.items(), key=lambda kv: -kv[1]))


def similar_recs(seeds, known, lf, obscurity=0.5, n_seeds=50, pool=150, k=40):
    """Artists similar to what you play that you've never played.
    obscurity 0..1 penalizes big Last.fm listener counts; that's what surfaces the overlooked."""
    score, because = defaultdict(float), defaultdict(list)
    for seed, w in top_seeds(seeds, n_seeds):
        for cand, match in lf.similar(seed):
            if is_known(cand, known):
                continue
            score[cand] += w * match
            because[cand].append((w * match, seed))
    out = []
    for cand in sorted(score, key=lambda c: -score[c])[:pool]:
        inf = lf.info(cand)
        if not inf or is_known(inf["name"], known):  # autocorrect can map to an artist you know
            continue
        penalty = (1 + inf["listeners"] / 100_000) ** obscurity
        out.append({"name": inf["name"], "score": round(score[cand] / penalty, 3),
                    "listeners": inf["listeners"], "tags": [t for t in inf["tags"] if good_tag(t)][:4],
                    "because": [s for _, s in sorted(because[cand], reverse=True)[:3]]})
    out.sort(key=lambda r: -r["score"])
    return out[:k]


def blend(tags, known, lf, k=12):
    """Artists that sit in the top lists of every tag, excluding ones you know.
    Ranked by summed position, so the most representative of the mix come first."""
    lists = [lf.tag_artists(t) for t in tags]
    pos = [{norm(a): i for i, a in enumerate(lst)} for lst in lists]
    names = {norm(a): a for a in lists[0]} if lists else {}
    shared = [n for n in names if not is_known(n, known) and all(n in p for p in pos)]
    shared.sort(key=lambda n: sum(p[n] for p in pos))
    return [names[n] for n in shared[:k]]


def auto_blends(profile, known, lf, n_tags=12, k=6, min_hits=3):
    """Pairs of your top tags that make a good mix: both matter to you, neither contains
    the other ('metal' / 'black metal'), and at least min_hits artists you don't know live there."""
    tags = list(profile)[:n_tags]
    pairs = [(a, b) for a, b in combinations(tags, 2) if a not in b and b not in a]
    pairs.sort(key=lambda p: -profile[p[0]] * profile[p[1]])
    out, used = [], defaultdict(int)
    for a, b in pairs:
        if used[a] >= 2 or used[b] >= 2:  # spread blends across your tags
            continue
        artists = blend([a, b], known, lf)
        if len(artists) >= min_hits:
            out.append({"tags": [a, b], "artists": artists})
            used[a] += 1
            used[b] += 1
        if len(out) == k:
            break
    return out
