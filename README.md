# Spotiscovery

A local web app that looks through your Spotify listening history, shows how your taste has changed, and finds **bands you've overlooked** that sound like what you already play. It can also mix sub-genres you like into new directions.

- **Trends:** leads with what matters (your current era, how mainstream you are, your peak month), then:
  - **Your eras:** your history split into stretches named for what was distinctive then, like *synthwave × chillwave, 2018 to 2021*. Click one and it opens in place: what you played then, who defined it, what it sounded like, and the artists you found.
  - **Mainstream meter:** the Last.fm listener count of your typical artist over time. Watch yourself go underground.
  - **Lost loves:** artists you once played for hours and quietly dropped.
  - Hours and new artists per month, taste drift per tag, when you listen, and top artists by year.
- **Explore:** pick any period (last 30/90/365 days, a year, a month, one of your eras, or any dates) and everything recomputes against the period before it:
  - **Daily averages:** what you average on today's weekday, on any day, and on days you listen, plus hour-by-hour and day-size charts.
  - **Every day at a glance:** a calendar heatmap; click a day for a play-by-play.
  - **Charts that zoom:** hours per day, week or month; click a bar to zoom in.
  - **Top lists and change:** artists, songs and albums with rank changes, what's rising and fading, and which discoveries stuck.
  - **Habits:** streaks, sessions, binges, one-day obsessions, time of day, skips, shuffle, how songs start.
  - **Taste:** genres by period and which ones rose, how varied your listening is.
  - **Where and how:** your devices, iPhones and countries.
  - **On this day:** what you played on today's date in every earlier year.
  - **Compare:** put any two periods side by side (2019 vs 2025): every stat with its change, genres, when you listened, who stayed, who's only in one, and how much the two listening mixes overlap.
  - **Your 3am music:** what each part of the day (late night, morning, afternoon, evening) plays far more than usual.
  - **Albums:** the ones you play front to back versus the ones you cherry-pick a song or two from.
  - **Travel:** a world map of where you listened, with each trip's dates and soundtrack.
  - **Artist pages:** click any artist, anywhere, for their full history with you (all-time curve, rank, streaks, first play, songs, when you play them), their big songs you've missed, and similar artists you haven't heard.
  - **Song pages:** click any song for its life in your history: plays over the years, most in one day, skips by year, how it usually started, and what you play before and after it.
- **Recap:** a card that summarizes *you* (total plays, artists, songs, hours, years on Spotify, number of music eras, and a chart of every month with your eras marked), not another top-5 list. Spotiscovery looks for the most telling things in your year (a song you came back to after 7 years, an artist you drifted from, the big artist you found within two years of their first release, your 3am artist, your deepest cut, where your taste moved) and ranks them by how unusual they are. Pick which go on the card (Story or Square), then download, share or copy it.
- **Similar sound:** artists close to what you play *now* that you've never played, on shelves by scene (house, hip-hop, ...) so one sound can't crowd out the rest. The *Mainstream ↔ Deep cuts* slider pushes big names down so smaller acts come through. *More like this* / *Not for me* on each pick shape the next run.
- **Found early:** the artists you were playing within two years of their first release (MusicBrainz) who now have over a million listeners.
- **Songs you've missed** (top of Similar sound): the best-known songs by artists you already love that aren't in your history yet. Tick the artists you want, or save one artist's songs with the button on its card.
- **Sounds like your favorites:** one shelf per artist you play most, of artists you've never played that sit in the same sound. Save a shelf, or tick individual artists, as a playlist.
- **Ask Claude (optional):** Claude reads your taste profile and suggests sound directions to explore, with reasons. Every artist it names is checked against Last.fm, so it can't hand you a band that doesn't exist.
- **Save as playlist:** everything on Similar sound saves as a private Spotify playlist: a whole section, the artists you tick (a bar at the bottom appears), or one artist. Artists without listed songs save their top 1, 3 or 5.
- **Only music:** podcasts, audiobooks and sleep/rain sounds are found and left out of every chart. You can review them in Setup and put any back.

Artists you already know are never recommended, including Last.fm's misspelled duplicates ("Travi$ Scott"), collabs with artists you play, and side projects.

Everything runs on your own machine. Your data stays in a local SQLite file.

> **Why Last.fm?** In November 2024 Spotify shut off its recommendations, related-artists and audio-features APIs for new apps. Spotiscovery gets your *history* from Spotify and *similarity and genre tags* from Last.fm, which is free.

## Setup (about 10 minutes)

You need Python 3.10+.

```bash
git clone https://github.com/<you>/spotiscovery.git
cd spotiscovery
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:8888**. The **Setup** panel opens on first run: paste your keys there and click **Save keys**. No file editing needed. (Prefer the file? Run `cp .env.example .env` and fill it in by hand. Both write the same `.env`, and Setup shows a saved key as already set.)

### 1. Spotify Client ID

1. Open [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and choose **Create app**.
2. Enter any name and description. Set **Redirect URI** to exactly `http://127.0.0.1:8888/callback`. Spotify doesn't accept `localhost`.
3. Under "Which API/SDKs", tick **Web API**, then save.
4. Copy the **Client ID** into the Setup panel (or `.env` as `SPOTIFY_CLIENT_ID`). You don't need a client secret.

The app runs in Spotify's development mode, which is fine for personal use. Spotify may require the account that owns the app to have Premium. If someone else wants to use *your* app, add them under **User Management**. Otherwise they can simply create their own app.

### 2. Last.fm API key

Create one at [last.fm/api/account/create](https://www.last.fm/api/account/create). It takes a minute and you can leave the callback URL blank. Paste it into the Setup panel (or `.env` as `LASTFM_API_KEY`).

### 3. Your full history (optional, strongly recommended)

The Spotify API only shows your top artists and your last 50 plays. For real trends, go to your [Spotify privacy page](https://www.spotify.com/account/privacy/) and request either export:

- **Account data**: your last year of plays, usually within 5 days. Upload the `StreamingHistory_music_*.json` files.
- **Extended streaming history**: your whole history with more detail, up to 30 days. Upload the `Streaming_History_Audio_*.json` files.

Upload the zip Spotify sends you directly, or unzip it yourself and upload the JSON files. Plays under 30 seconds are ignored.

The extended history also carries albums, devices, countries, shuffle and how each song started, which Explore uses. If you imported before Spotiscovery stored those, upload the same zip again: existing plays get the extra fields and nothing is duplicated. (Your IP address, also in the export, is never stored.)

### 4. Claude (optional)

Paste an Anthropic API key into the Setup panel (or `.env` as `ANTHROPIC_API_KEY`) to turn on **Ask Claude**. One click costs a few cents. To use a different model, set `CLAUDE_MODEL` in `.env` (no in-app field for this one).

## Run

```bash
python app.py
```

Open **http://127.0.0.1:8888**, save your keys, click **Connect Spotify**, and upload your history files if you have them.

The first time you open Trends or Similar sound it takes a minute or two, because Last.fm allows about 5 requests per second. A progress bar shows how far along it is. Responses are cached for 30 days, so after that it's instant.

If you connected Spotify before playlist export existed, click **Reconnect to save playlists** in Setup once.

Artist photos come from Spotify, or from Deezer's public search when Spotify has none or isn't connected.

Release dates for "you found them early" come from MusicBrainz (free, no key). It allows one request per second, so the first recap checks a few artists and finishes the rest in the background; run it again a few minutes later.

The default look is Spotify green. **Colorblind mode** (a switch in the header, remembered in your browser) swaps it for blue with orange for errors, a pair that stays distinct with red/green color blindness (deuteranopia). In both modes nothing relies on color alone: changes carry arrows and signs, and the time-of-day chart uses a single-hue ramp.

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask routes, Spotify login callback, and export upload |
| `spotify.py` | PKCE login, then pulls top artists, recent plays, follows and saved-track artists |
| `lastfm.py` | Cached Last.fm client for similar artists, tags, listener counts and tag charts |
| `analyze.py` | Trend charts, taste drift, eras, the recap card's numbers |
| `explore.py` | Any-period analytics, artist pages, day play-by-play |
| `recommend.py` | The scoring (see below) |
| `backtest.py` | Measures Similar sound on your own history (see below) |
| `claude_mode.py` | Optional Claude sound directions, each artist checked against Last.fm |
| `static/index.html` | The whole UI, with no build step |

**Scoring.** Each artist you play gets a weight from their minutes, with every play decayed by its age (half-life 180 days) and artists you started in the last year counting double, plus a bonus if they're in your Spotify top artists. For your top 300 artists, Spotiscovery collects Last.fm's similar artists. A candidate's score is the sum of *(your weight for the seed × how similar the candidate is to it)*, times `1 + ln(number of your artists pointing to it)`. Anyone you've played, follow, saved or rated is dropped; *More like this* adds an artist as a seed. The obscurity slider divides the score by `(1 + listeners/100k) ^ obscurity`, and no scene (the last word of an artist's top tag) gets more than a quarter of the 40 picks.

**Backtest.** `python backtest.py` hides a year of your history, recommends from what came before, and checks where the artists you went on to adopt (30+ minutes, new to you) land, for each of your last three years. On the author's history, moving from all-time minutes and 50 seeds to the current method took adopted artists in the top 300 candidates from 4 to 11. The final top 40 held about one each either way. That's too few to rank methods on, so the dense number drove the choice. Most new finds hang off the long tail of a scene you're getting into, not your all-time favorites, which is why the seeds go 300 deep and lean recent.

**Songs you've missed.** For your top 40 artists, Last.fm's top 20 songs minus every song in your history (versions like "Remastered" count as the same song). Artists with the most gaps come first.

**Sounds like your favorites.** For each of your top 10 artists, their Last.fm similar artists that you don't know, ranked by match with the obscurity penalty applied. An artist appears under one favorite only.

## Privacy and security

- **Your data stays on your machine.** The app listens on `127.0.0.1` only. Your history, your Spotify tokens and your ratings live in `deep_cuts.db`, and your API keys in `.env`. Both are git-ignored. Treat them like passwords: the database holds a Spotify refresh token, so never commit, share or upload it. If you fork this repo, keep those two entries in `.gitignore`.
- **What leaves your machine:** artist and song names go to Last.fm, MusicBrainz, Deezer and Spotify to look up similar artists, release dates and photos. Nothing is sent to Anthropic unless you click *Ask Claude*, which sends a summary of your top artists and tags using your own key. Spotify's `ip_addr` field from the extended export is never stored.
- **Don't expose it to a network.** There is no login, so anything that can reach the port can read your history and use your Last.fm, Spotify and Anthropic keys. Don't bind it to `0.0.0.0`, port-forward it, or put it behind a tunnel.
- **Built-in protections:** requests with a foreign `Host` (DNS rebinding) or a cross-site `Origin` on writes are refused; the page can't be framed; a Content-Security-Policy limits where scripts and requests can go; third-party scripts are pinned to exact versions with integrity hashes; the photo relay only fetches from Spotify's and Deezer's image hosts, and doesn't follow redirects.
- **Screenshots:** if you share screenshots or a recap card, they show your real listening.

Found a security problem? Please open a GitHub issue (without personal data in it).

## Tests

```bash
python test_recommend.py
python test_regressions.py
```

## License

MIT
