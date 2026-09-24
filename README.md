# Deep Cuts

A local web app that looks through your Spotify listening history, shows how your taste has changed, and finds **bands you've overlooked** that sound like what you already play. It can also mix sub-genres you like into new directions.

- **Trends:** hours per month, new artists per month, taste drift (how your genre mix shifts each quarter), when you listen, and your top artists by year.
- **Similar sound:** artists close to what you play that you've never played. The *Mainstream ↔ Deep cuts* slider pushes big names down so smaller acts come through.
- **Blends:** artists at the crossover of two or three of your tags, like *shoegaze × post-hardcore*. Some blends are picked automatically; you can also choose tags yourself.
- **Ask Claude (optional):** Claude reads your taste profile and suggests blends with reasons. Every artist it names is checked against Last.fm, so it can't hand you a band that doesn't exist.

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

### 4. Claude (optional)

Paste an Anthropic API key into the Setup panel (or `.env` as `ANTHROPIC_API_KEY`) to turn on **Ask Claude**. One click costs a few cents. To use a different model, set `CLAUDE_MODEL` in `.env` (no in-app field for this one).

## Run

```bash
python app.py
```

Open **http://127.0.0.1:8888**, save your keys, click **Connect Spotify**, and upload your history files if you have them.

The first time you open Similar or Blends it takes a minute or two, because Last.fm allows about 5 requests per second. Responses are cached for 30 days, so after that it's instant.

## How it works

| File | What it does |
|---|---|
| `app.py` | Flask routes, Spotify login callback, and export upload |
| `spotify.py` | PKCE login, then pulls top artists, recent plays, follows and saved-track artists |
| `lastfm.py` | Cached Last.fm client for similar artists, tags, listener counts and tag charts |
| `analyze.py` | Trend charts and taste drift |
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
