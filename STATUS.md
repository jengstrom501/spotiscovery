# Status

**State (2026-09-24):** V1 built. Unit test passes; all endpoints and all three tabs verified
against a fake-data DB (desktop + 390px mobile). Not yet run against real Spotify/Last.fm.

## Next
1. Create Spotify dev app (redirect `http://127.0.0.1:8888/callback`) → `SPOTIFY_CLIENT_ID` in `.env`
2. Get Last.fm API key → `LASTFM_API_KEY`
3. Request Extended Streaming History export (takes days)
4. First live run: connect, sync, check recs quality; tune `JUNK_TAGS` and obscurity curve
5. Create GitHub repo, push

## Known limits
- Heatmap hours are UTC (export has no per-play timezone)
- Saved-tracks walk capped at 2000 artists
- Similarity is Last.fm co-listening + tags only, no audio analysis
