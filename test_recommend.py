"""Run: python test_recommend.py  (no network, fake Last.fm graph)"""
import recommend as r


class FakeLastfm:
    SIM = {
        "Slowdive": [("Ride", 1.0), ("Hum", 0.8), ("Famous Band", 0.9), ("My Bloody Valentine", 1.0)],
        "My Bloody Valentine": [("Ride", 0.9), ("Hum", 0.5), ("Slowdive", 1.0)],
    }
    LISTENERS = {"Ride": 50_000, "Hum": 20_000, "Famous Band": 5_000_000}
    TAGS = {"Slowdive": [("shoegaze", 100), ("dream pop", 60), ("seen live", 90)],
            "My Bloody Valentine": [("shoegaze", 100), ("noise pop", 40)]}
    TAG_TOP = {"shoegaze": ["Slowdive", "Ride", "Hum", "Nothing"],
               "dream pop": ["Beach House", "Slowdive", "Nothing", "Ride"],
               "noise pop": ["Nothing", "My Bloody Valentine", "Ride"]}

    def similar(self, a): return self.SIM.get(a, [])
    def top_tags(self, a): return self.TAGS.get(a, [])
    def tag_artists(self, t): return self.TAG_TOP.get(t, [])
    def info(self, a):
        return {"name": a, "listeners": self.LISTENERS.get(a, 1000), "tags": ["shoegaze", "1990s"]}


lf = FakeLastfm()
seeds = r.seed_weights({"Slowdive": 3000, "My Bloody Valentine": 500}, {"short_term": ["Slowdive"]})
known = set(seeds)

recs = r.similar_recs(seeds, known, lf, obscurity=1.0)
names = [x["name"] for x in recs]
assert "Slowdive" not in names and "My Bloody Valentine" not in names, "known artists leaked"
assert names[0] == "Ride", names                       # strongest combined match
assert names.index("Hum") < names.index("Famous Band"), names  # obscurity demotes the giant
assert recs[0]["because"][0] == "Slowdive"
assert recs[0]["tags"] == ["shoegaze"]                  # decade tag filtered

recs0 = r.similar_recs(seeds, known, lf, obscurity=0.0)
famous = lambda rs: next(x["score"] for x in rs if x["name"] == "Famous Band")
assert famous(recs0) > 10 * famous(recs), "obscurity should crush a 5M-listener artist"

prof = r.tag_profile(seeds, lf)
assert list(prof)[0] == "shoegaze" and "seen live" not in prof, prof

# Favorites: one shelf per favorite, unknown artists only, big names demoted, nobody repeated on a second shelf
fav = r.favorites_like(seeds, known, lf, n=2, per=2, obscurity=1.0)
assert [x["seed"] for x in fav] == ["Slowdive"], fav  # Ride and Hum are taken, so My Bloody Valentine has nothing left
assert {a["name"] for a in fav[0]["artists"]} == {"Ride", "Hum"}, fav[0]  # Famous Band (5M listeners) loses to both
assert "rank" not in fav[0]["artists"][0] and fav[0]["artists"][0]["tags"] == ["shoegaze"]

print("ok")
