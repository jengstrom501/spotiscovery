# Status

**State (2026-09-25, renamed Spotiscovery):** Running on real data (151k plays, 2016-2026, extended export re-imported so albums,
devices, countries, shuffle and start/end reasons are filled). Tabs: Trends, Explore (any period, daily averages,
calendar, habits, genres by period, artist + day drill-downs), Similar sound, Blends, Recap (shareable card).

## Next
1. Save a playlist for real (endpoints moved to post-Feb-2026 /me/playlists + /items; only tested with mocks)
2. Tune `JUNK_TAGS` and the obscurity curve with backtest.py
3. Create GitHub repo, push

Built 2026-09-25 (round 3): compare two periods, song pages, albums front-to-back vs cherry-picked,
travel map with trips, music by the clock ("your 3am music").

Recap is insight-driven (insights.py): ~18 kinds of finding, scored and ranked, one per kind.

Built 2026-09-25 (round 4): CVD-safe blue/orange theme; Explore sub-tabs + Customize (hide sections, per browser);
Found early list (/api/found-early, MusicBrainz warms in the background); recap stats gutters + compact insight picker;
Similar sound backtested (backtest.py) and rebuilt: taste-now seeds, 300 deep, consensus, scene shelves, thumbs.

## Next
- Recommendations: the final top 40 barely moves in the backtest (0-1 hits a year either way); try a taste-fit
  re-rank or Claude re-rank of real candidates, judged by backtest.py. Tried a tag-profile fit: no gain.
- Sleep-sound "artists" (Robbins Island Music Group, Sleepy Cow, Nature Sounds Artists) slip past flag_not_music
  (short plays) and show up in "3am music"; widen the artist-name check.

## Ideas not built yet
- Album pages (tracklist with which songs you play, full listens over time)
- Genre drill-down: click a tag for its artists and share over time
- Hide artists in bulk from Explore (focus-playlist and meditation "artists")

## Known limits
- "Focus/study playlist" artists (Tejal Yann, Dreamscaper...) count as music; hide by hand with the x on Top artists
- Aliases with no shared name or tag (AFX / Polygon Window for Aphex Twin) can still be recommended
- Spotify's later exports drop the iPhone model, so "Your phones" stops around 2022
- Saved-tracks walk capped at 2000 artists; similarity is Last.fm co-listening + tags only
