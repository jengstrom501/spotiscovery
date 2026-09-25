# Status

**State (2026-09-25):** Running on real data (148k plays, 2016-2026). Added: progress bars, not-music filter
(hid ~720 h of sleep/relax audio), eras, mainstream meter, lost loves, songs you've missed, playlist export,
insight-first Trends. Fixed: collab and misspelled-duplicate recs, low-res/missing/wrong artist photos.

## Next
1. Reconnect Spotify once (new playlist scope), then try Save as playlist for real (only tested with mocks)
2. Tune `JUNK_TAGS` and the obscurity curve against real recs
3. Create GitHub repo, push

## Known limits
- "Focus/study playlist" artists (Tejal Yann, Dreamscaper...) count as music: normal-length tracks, daytime
  plays, "ambient" tags. Hide by hand with the x on Top artists.
- Aliases with no shared name or tag (AFX / Polygon Window for Aphex Twin) can still be recommended
- Saved-tracks walk capped at 2000 artists
- Similarity is Last.fm co-listening + tags only, no audio analysis
