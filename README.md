# Deep Cuts

A local web app that looks through your Spotify listening history, shows how your taste has changed, and finds **bands you've overlooked** that sound like what you already play. It can also mix sub-genres you like into new directions.

- **Trends:** leads with what matters (your current era, how mainstream you are, your peak month), then:
  - **Your eras:** your history split into stretches named for what was distinctive then, like *synthwave × chillwave, 2018 to 2021*.
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
- **Recap:** a shareable year-in-music card (Story or Square) to download, share or copy.
- **Similar sound:** artists close to what you play that you've never played. The *Mainstream ↔ Deep cuts* slider pushes big names down so smaller acts come through.
- **Songs you've missed:** the best-known songs by artists you already love that aren't in your history yet.
- **Blends:** artists at the crossover of two or three of your tags, like *shoegaze × post-hardcore*. Some blends are picked automatically; you can also choose tags yourself.
- **Ask Claude (optional):** Claude reads your taste profile and suggests blends with reasons. Every artist it names is checked against Last.fm, so it can't hand you a band that doesn't exist.
- **Save as playlist:** any list of picks becomes a private Spotify playlist in one click.
- **Only music:** podcasts, audiobooks and sleep/rain sounds are found and left out of every chart. You can review them in Setup and put any back.

Artists you already know are never recommended, including Last.fm's misspelled duplicates ("Travi$ Scott"), collabs with artists you play, and side projects.

Everything runs on your own machine. Your data stays in a local SQLite file.

> **Why Last.fm?** In November 2024 Spotify shut off its recommendations, related-artists and audio-features APIs for new apps. Deep Cuts gets your *history* from Spotify and *similarity and genre tags* from Last.fm, which is free.

## Setup (about 10 minutes)

You need Python 3.10+.

```bash
git clone https://github.com/<you>/deep-cuts.git
cd deep-cuts
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

The extended history also carries albums, devices, countries, shuffle and how each song started, which Explore uses. If you imported before Deep Cuts stored those, upload the same zip again: existing plays get the extra fields and nothing is duplicated. (Your IP address, also in the export, is never stored.)

### 4. Claude (optional)

Paste an Anthropic API key into the Setup panel (or `.env` as `ANTHROPIC_API_KEY`) to turn on **Ask Claude**. One click costs a few cents. To use a different model, set `CLAUDE_MODEL` in `.env` (no in-app field for this one).

## Run

```bash
python app.py
```

Open **http://127.0.0.1:8888**, save your keys, click **Connect Spotify**, and upload your history files if you have them.

The first time you open Trends, Similar or Blends it takes a minute or two, because Last.fm allows about 5 requests per second. A progress bar shows how far along it is. Responses are cached for 30 days, so after that it's instant.

If you connected Spotify before playlist export existed, click **Reconnect to save playlists** in Setup once.

Artist photos come from Spotify, or from Deezer's public search when Spotify has none or isn't connected.

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask routes, Spotify login callback, and export upload |
| `spotify.py` | PKCE login, then pulls top artists, recent plays, follows and saved-track artists |
| `lastfm.py` | Cached Last.fm client for similar artists, tags, listener counts and tag charts |
| `analyze.py` | Trend charts, taste drift, eras, the recap card's numbers |
| `explore.py` | Any-period analytics, artist pages, day play-by-play |
| `recommend.py` | The scoring (see below) |
| `claude_mode.py` | Optional Claude blends, each artist checked against Last.fm |
| `static/index.html` | The whole UI, with no build step |

**Scoring.** Each artist you play gets a weight based on log minutes played, plus a bonus if they're in your Spotify top artists. For your top 50 artists, Deep Cuts collects Last.fm's similar artists. Each candidate's score is the sum of *(your weight for the seed artist × how similar the candidate is to it)*. Anyone you've played, follow or have saved is dropped. The obscurity slider then divides the score by `(1 + listeners/100k) ^ obscurity`.

**Blends.** Your tag profile is your listening weight spread over each artist's Last.fm tags, with junk tags like "seen live" and decades removed. A blend is the set of artists in *every* chosen tag's top 300 that you don't already know.

## Tests

```bash
python test_recommend.py
python test_regressions.py
```

## License

MIT
