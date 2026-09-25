# Status

**State (2026-09-25):** Running on real data (151k plays, 2016-2026, extended export re-imported so albums,
devices, countries, shuffle and start/end reasons are filled). Tabs: Trends, Explore (any period, daily averages,
calendar, habits, genres by period, artist + day drill-downs), Similar sound, Blends, Recap (shareable card).

## Next
1. Save a playlist for real (endpoints moved to post-Feb-2026 /me/playlists + /items; only tested with mocks)
2. Tune `JUNK_TAGS` and the obscurity curve against real recs
3. Create GitHub repo, push

## Ideas not built yet
- Compare any two periods side by side (e.g. 2019 vs 2025), not just vs the previous one
- Song pages like artist pages (play curve, first/last play, skip rate over time)
- Album completion: which albums you play front to back vs cherry-pick
- Listening map from conn_country with dates (trips show up as clusters)
- "Your 3am music": what you play at unusual hours vs your baseline

## Known limits
- "Focus/study playlist" artists (Tejal Yann, Dreamscaper...) count as music; hide by hand with the x on Top artists
- Aliases with no shared name or tag (AFX / Polygon Window for Aphex Twin) can still be recommended
- Spotify's later exports drop the iPhone model, so "Your phones" stops around 2022
- Saved-tracks walk capped at 2000 artists; similarity is Last.fm co-listening + tags only
