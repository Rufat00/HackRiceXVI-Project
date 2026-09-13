# Pulse — the room picks the music

An AI DJ for parties. The host puts a QR code on a screen; guests scan it and, with no account, request songs, vote 👍/👎 on what's playing, and pitch themes for the night. A **DJ brain** ranks every candidate with an explainable score and keeps the queue moving — and the big screen shows *why* each song was picked.

Plays through **Spotify** (host connects once; the app searches the full catalog and controls their device) or, with no keys at all, through a built-in catalog with a simulated player so it demos anywhere.

---

## Run it

```bash
pip install -r requirements.txt
MOCK_TIME_SCALE=20 python run.py      # songs fly by 20x for a demo; omit for real time
```

Open http://127.0.0.1:5000, name the party, and you're on the host screen. Scan the QR from a phone on the same network, or fake a crowd:

```bash
python simulate.py ABCD --guests 10     # ABCD = the code on the host screen
```

For phones not on your Wi-Fi, expose it and tell the QR where to point:

```bash
ngrok http 5000
PUBLIC_URL=https://xxxx.ngrok.app python run.py
```

### Spotify (real playback)

1. Create an app at developer.spotify.com. Add redirect URI **exactly** `http://127.0.0.1:5000/callback` (Spotify rejects `localhost`).
2. `export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=...` and restart.
3. On the host screen click **Connect Spotify**. Open Spotify on the laptop/speaker (Premium required for remote control) and press play once so it's the active device.

From then on the app searches Spotify and starts each next track via Spotify Connect. If the host changes the song manually on their phone, Pulse notices, logs it as a host pick, and carries on.

### Claude (optional)

`export ANTHROPIC_API_KEY=...` and the DJ gets two upgrades: themes like "songs my mom would dance to" get interpreted into genres/decade/energy (the regex parser handles obvious ones like "2000s throwback" on its own), and the host screen shows a one-line DJ shoutout per song.

---

## How the DJ decides

Every poll, the server scores every candidate (crowd requests first, then catalog/Spotify fallbacks when the queue is thin) and plays the top one when the current song ends. `app/services/dj.py`:

| component | what it measures | weight |
|---|---|---|
| demand | how many *different* guests requested it (log-scaled, so 10 requests ≠ 10× one) | 3.0 |
| momentum | net 👍/👎 on the queued request | 1.2 |
| theme | overlap with the active theme's genres/keywords, plus decade match | 2.5 |
| energy | closeness to the party's **target energy** | 2.0 |
| affinity | genre affinity **learned live** from how the room voted on past songs (shrunk so one song can't hijack the night) | 1.5 |
| freshness | small bonus for recent requests so the queue moves | 0.5 |
| popularity | tie-breaker toward safe picks | 0.3 |
| repeat | same artist as one of the last 4 plays | −2.0 |
| replay | already played in the last 2 hours (near-veto) | −8.0 |
| explicit | if the host turned on "hide explicit" | −3.0 |

**Target energy** follows an arc — warm-up 0.55 → peak 0.85 → wind-down 0.50 — over the party length the host set (or 4 h), blended 50/50 with the active theme's vibe ("chill" → 0.35, "bangers" → 0.9), then nudged ±0.15 by live feedback: if high-energy songs are getting buried, the target drops.

**Active theme** = host-pinned theme, else the top-voted one with net ≥ 1.

**Crowd skip**: once ≥ 4 votes are in and the balance is ≤ −50%, the song is skipped and logged as `skipped_by_crowd`. Both thresholds are env-tunable.

**Anti-abuse**: one vote per guest per song (toggle to undo), one request per guest per track, 8 requests per guest per hour, one theme per text per party. Guests are cookie-identified; there's nothing to sign up for.

Every pick stores its score components, and the host screen renders them ("demand +3.3, theme +2.5, repeat −2.0") plus a sentence: *"2 people asked for it; fits 'latin night'; despite a recent song by this artist."*

---

## Architecture

```
app/
  __init__.py        Flask factory; /, /host/<code>, /join/<code>, /callback (Spotify OAuth)
  config.py          env-driven; mock mode auto-selected without keys
  db.py              sqlite schema: parties, guests, tracks, suggestions(+votes), plays(+votes), themes(+votes)
  routes/api.py      JSON API (host + guest), QR PNG, Spotify login
  services/
    dj.py            pure scoring/parsing functions — unit tested, no I/O
    party.py         engine: context → candidates → playback; tick() per poll; mock & Spotify players
    catalog.py       48-track built-in catalog with energy/genre/year attributes
    spotify.py       OAuth, search + genre/feature enrichment, playback state/control
    ai.py            optional Claude for theme interpretation + shoutouts
  static/
    landing.html     create a party
    host.html        big screen: now playing, crowd pulse bar, why-this-song, QR, up next, themes, room reading
    guest.html       phone: Now / Request / Themes tabs
simulate.py          fake crowd
tests/test_pulse.py  8 tests: theme parsing, arc, affinity, scorer, skip rule, full party flow, mock clock, rate limits
```

**Realtime** is 3–4 s polling. `GET /state` runs one `tick()` (advance the player, apply crowd skips) and returns everything both pages need. No websockets to debug at 2 a.m.

### API

```
POST /api/parties                        {name, host_name, hours}  → {code, host_url, join_url}  (sets host cookie)
GET  /api/parties/:code/qr.png
POST /api/parties/:code/join             {nickname}                (sets guest cookie)
GET  /api/parties/:code/state            everything: now_playing, queue, themes, dj, stats, history(host), events
GET  /api/parties/:code/search?q=
POST /api/parties/:code/suggest          {track_id}
POST /api/parties/:code/vote             {value: 1|-1}             vote on the playing song (toggle)
POST /api/parties/:code/queue/vote       {track_id, value}
POST /api/parties/:code/themes           {text}   → parsed keywords/decade/energy
POST /api/parties/:code/themes/:id/vote  {value}
host: POST .../host/skip  .../host/play {track_id}  .../host/remove {track_id}
      POST .../host/settings {auto_dj?, pinned_theme_id?, block_explicit?}
      GET  .../spotify/login  .../spotify/devices
```

---

## Demo script (2 min)

1. Host screen up. Point at the QR, the pulse bar, and "Why this song."
2. Three phones request the same track → it jumps to the top of *Up next* with "3 people asked for it."
3. Someone pitches "latin night", two upvote → *Room reading* shows the theme steering; latin tracks climb.
4. Everyone 👎 the current song → the pulse bar goes red, "Crowd is skipping this…", it skips, and *Played* logs it. Affinity chips for its genres go negative — the DJ learned.
5. Show the score breakdown under the new song. Flip *Auto DJ* off and use ▶ on a queue item to show host override.

If you're alone: `python simulate.py <code>` and narrate.

---

## Known limits

- Spotify's `/audio-features` is restricted for apps created after Nov 2024, so live-mode energy may be `None`; the scorer then leans on votes, theme, genre affinity and popularity. Mock catalog has full attributes.
- Spotify remote control needs Premium and an active device.
- Guest identity is a cookie; clearing it lets someone vote twice. A real deployment would fingerprint or gate on the Wi-Fi.
- Dispute/abuse tooling is minimal: the host can remove any queued track.
