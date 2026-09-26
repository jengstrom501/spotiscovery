# Spotiscovery
Spotify history → trends + overlooked-artist recs + songs you've missed, saved as playlists. Local Flask app,
public GitHub repo (nested git repo, ignored by the Claude OS repo).

## How to resume
Read STATUS.md, show state + next step, wait for direction.

## Working here
- Run: `python app.py` → http://127.0.0.1:8888 (needs `.env`, see README)
- Test: `python test_recommend.py && python test_regressions.py` (plain assert scripts)
- Offline UI check: turn on demo mode (`POST /api/demo {"on":true}`), which builds demo.py's seeded
  history plus its cached Last.fm rows into demo.db. Works with no keys and makes no network calls.
- Spotify only supplies history (similarity APIs are dead); similarity = Last.fm.

## Look
Own identity, NOT Claude OS DESIGN.md: Geist + Geist Mono (self-hosted in static/fonts, OFL),
warm near-black + one accent driven by CSS variables: Spotify green #1DB954 by default, blue #5CB0FF + orange errors in
the optional colorblind mode (`data-theme="cvd"`, header toggle). Never hardcode the accent in JS; use ACCENT/acc(), dark only. Radius: cards 14, controls 8, tags pill.

## Skills & agents
- design-taste-frontend — before visual changes (no em-dashes in UI copy)
- animate — before adding or changing motion (Emil Kowalski's rules: ease-out, under 300ms, transform/opacity, reduced motion)
- claude-api — before touching claude_mode.py
- dataviz — before changing charts (data-storytelling for insight-first titles/headlines)
- browse — screenshot the UI after frontend changes
