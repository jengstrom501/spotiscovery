# Spotiscovery
Spotify history → trends + overlooked-artist recs + sub-genre blends. Local Flask app,
public GitHub repo (nested git repo, ignored by the Claude OS repo).

## How to resume
Read STATUS.md, show state + next step, wait for direction.

## Working here
- Run: `python app.py` → http://127.0.0.1:8888 (needs `.env`, see README)
- Test: `python test_recommend.py && python test_regressions.py` (plain assert scripts)
- Offline UI check: seed a throwaway DB with fake plays + pre-cached Last.fm rows
  (`DEEP_CUTS_DB=<path> LASTFM_API_KEY=dummy python app.py`)
- Spotify only supplies history (similarity APIs are dead); similarity = Last.fm.

## Look
Own identity, NOT Claude OS DESIGN.md: Geist + Geist Mono (self-hosted in static/fonts, OFL),
warm near-black + one blue accent #5CB0FF (orange #F0914A for down/errors: a CVD-safe pair), dark only. Radius: cards 14, controls 8, tags pill.

## Skills & agents
- design-taste-frontend — before visual changes (no em-dashes in UI copy)
- claude-api — before touching claude_mode.py
- dataviz — before changing charts (data-storytelling for insight-first titles/headlines)
- browse — screenshot the UI after frontend changes
