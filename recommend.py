"""Scoring. `lf` is anything with lastfm.py's interface (similar, top_tags, info, tag_artists)."""
import math
import re
from collections import Counter, defaultdict

from store import norm, squash, track, track_key

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


def is_known(name, known, tags=()):
    """Known by name (spelling-proof, see store.squash), a collab with anyone you already play, or tagged on
    Last.fm with the name of an artist you play (how aliases and side projects show up: "Scotts" -> "travis scott").
    ponytail: separator split, so "Earth, Wind & Fire" only drops if you play an artist called "Fire"; and a
    genre-named artist you play (the band "Electronic") would hide everything tagged with it. Rare, left as is."""
    n = norm(name)
    # Spelling-proofing only for the whole name: on parts and tags it over-matches ("the queen" -> Queen,
    # the tag "florida" -> Flo Rida), so those need an exact match.
    return "~" + squash(n) in known or any(p in known for p in (n, *COLLAB.split(n), *map(norm, tags)) if p)


def known_set(names):
    """Known artists as norm() keys plus "~"-prefixed squash() keys (kept apart so exact checks can't hit them),
    the form is_known() expects."""
    names = {norm(n) for n in names}
    return names | {"~" + squash(n) for n in names}


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
    for name, w in track("profile", top_seeds(seeds, n), "Reading the tags of your top artists"):
        for tag, count in lf.top_tags(name)[:10]:
            if good_tag(tag, name):
                prof[tag] += w * count / 100
    return dict(sorted(prof.items(), key=lambda kv: -kv[1]))


def candidates(seeds, known, lf, n_seeds=300):
    """({candidate: score} best first, {candidate: [(weight, seed)]}) from your seeds' Last.fm similar lists."""
    score, because = defaultdict(float), defaultdict(list)
    for seed, w in track("similar", top_seeds(seeds, n_seeds), "Finding artists similar to yours"):
        for cand, match in lf.similar(seed):
            if is_known(cand, known):
                continue
            score[cand] += w * match
            because[cand].append((w * match, seed))
    for cand in score:
        score[cand] *= 1 + math.log(len(because[cand]))  # consensus: several of your artists point here
    return dict(sorted(score.items(), key=lambda kv: -kv[1])), because


def similar_recs(seeds, known, lf, obscurity=0.5, n_seeds=300, pool=300, k=40):
    """Artists similar to what you play that you've never played.
    obscurity 0..1 penalizes big Last.fm listener counts; that's what surfaces the overlooked.
    300 seeds, not just your top 50: new finds tend to hang off your long tail (the small artists of a scene
    you're getting into), and a candidate several of them point to gets a bonus (consensus)."""
    score, because = candidates(seeds, known, lf, n_seeds)
    out = []
    for cand in track("similar", list(score)[:pool], "Checking how well-known each one is"):
        inf = lf.info(cand)
        if not inf or is_known(inf["name"], known, inf["tags"]):  # autocorrect can map to an artist you know
            continue
        penalty = (1 + inf["listeners"] / 100_000) ** obscurity
        out.append({"name": inf["name"], "score": round(score[cand] / penalty, 3),
                    "listeners": inf["listeners"], "tags": [t for t in inf["tags"] if good_tag(t)][:4],
                    "because": [s for _, s in sorted(because[cand], reverse=True)[:3]]})
    out.sort(key=lambda r: -r["score"])
    return diversify(out, k)


# Rough scene of a tag: its last word ("deep house", "lo-fi house" -> house), with hip-hop's spellings merged.
SCENE_ALIAS = {"hop": "hip-hop", "rap": "hip-hop", "trap": "hip-hop", "chillout": "downtempo", "lounge": "downtempo"}


def scene(tags):
    t = next((t for t in tags if good_tag(t)), "")
    last = t.replace("hip hop", "hip-hop").split()[-1] if t else "other"
    return SCENE_ALIAS.get(last, last)


def diversify(recs, k, share=0.25):
    """Best first, but no scene takes more than `share` of the list, so the sound you're deepest in right now
    doesn't crowd out the others you still play. Each rec gets its "scene" for the page's shelves.
    ponytail: scene = last word of the top tag; tag clustering if that proves too rough."""
    cap, per, out, rest = max(4, int(k * share)), Counter(), [], []
    for r in recs:
        r["scene"] = scene(r["tags"])
        (out if per[r["scene"]] < cap else rest).append(r)
        per[r["scene"]] += 1
    return (out + rest)[:k]


def deep_cuts(seeds, played, lf, n_seeds=40, per_artist=6, min_unheard=3):
    """Songs you've never played by artists you already love: each top artist's Last.fm top 20 minus the tracks
    in your history. played: {norm(artist): {track_key}}. Artists with the most gaps come first."""
    out = []
    for name, w in track("deepcuts", top_seeds(seeds, n_seeds), "Finding songs you've missed"):
        tops = lf.top_tracks(name)
        heard = played.get(norm(name), set())
        unheard = [t for t in tops if track_key(t) not in heard]
        if tops and len(unheard) >= min_unheard:
            out.append({"name": name, "heard": len(tops) - len(unheard), "of": len(tops),
                        "tracks": unheard[:per_artist], "weight": w})
    out.sort(key=lambda r: (r["heard"] / r["of"], -r["weight"]))
    return out


def favorites_like(seeds, known, lf, n=10, per=6, obscurity=0.5):
    """For each of your top artists, similar artists you've never played, best match first (the obscurity slider
    demotes big names like it does elsewhere). An artist shows under one favorite only, the first that claims it,
    so the shelves don't repeat. Returns [{"seed", "artists": [{name, match, listeners, tags}]}]."""
    out, used = [], set()
    for seed, _ in track("favorites", top_seeds(seeds, n), "Finding artists that sound like your favorites"):
        found = []
        for cand, match in lf.similar(seed):
            if is_known(cand, known) or norm(cand) in used:
                continue
            inf = lf.info(cand)
            if not inf or is_known(inf["name"], known, inf["tags"]) or norm(inf["name"]) in used:
                continue
            found.append({"name": inf["name"], "match": round(match, 2), "listeners": inf["listeners"],
                          "tags": [t for t in inf["tags"] if good_tag(t)][:4],
                          "rank": match / (1 + inf["listeners"] / 100_000) ** obscurity})
            if len(found) >= per * 2:  # each lookup is a Last.fm call (cached after the first), so look at a dozen at most
                break
        picks = sorted(found, key=lambda a: -a["rank"])[:per]
        used.update(norm(a["name"]) for a in picks)
        if picks:
            out.append({"seed": seed, "artists": [{k: v for k, v in a.items() if k != "rank"} for a in picks]})
    return out
