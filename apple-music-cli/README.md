# apple-music-cli

A thin CLI over the **Apple Music API** (`api.music.apple.com`) for coding
agents and humans — catalog search + your own library (playlists, saved songs,
recently played). Sibling of [`spotify-cli`](../spotify-cli) (same thin-shell
shape) and [`splice-cli`](../splice-cli) (same reverse-engineered "borrowed
token" auth lineage).

## Prerequisites — what you need before it works

There are **two halves**, and they have different requirements. Nothing here
needs a paid Apple Developer membership (that's the deliberately-unbuilt Path A).

### Catalog half — `search`, `song`, `album`, `artist`, `playlist-get`

**No prerequisites.** No login, no account, no subscription. Just:

- Python ≥ 3.10 and the install below.
- Outbound network to `music.apple.com` (to auto-fetch the developer token) and
  `api.music.apple.com`.

Works the moment you install it.

### Library half — `my-playlists`, `library`, `recent`, `heavy-rotation`, all writes

Needs **all three** of these, or it can't return your data:

1. **An Apple ID signed in to `music.apple.com` in a local browser.** You log in
   yourself (normal browser sign-in); the CLI lifts the `media-user-token` cookie.
   The browser must be on the **same machine** as the CLI (the cookie store is
   local). Firefox works with no extra dependency; Chrome/Chromium/Brave/Edge
   need the `[chrome]` extra (`pip install 'apple-music-cli[chrome]'`).
2. **An _active_ Apple Music subscription** on that account — the consumer
   ~monthly plan (a **free trial counts**). This is separate from the login and
   from any developer membership. Without it, `/me/*` returns
   *400 Insufficient Privileges* — a structural Apple gate (library/history are
   subscriber features), not something this CLI or any workaround can bypass.
3. **`apple-music login`** run once to lift and save the token (see below).

> Rule of thumb: **catalog = zero setup; library = your browser login + an
> active (or trial) Apple Music subscription.**

### Heads-up: this is the unofficial route (Path B)

Auth uses a developer token **scraped** from the `music.apple.com` web player and
your browser's `media-user-token` cookie — the `splice-cli` lineage, no paid
membership. It depends on `music.apple.com`'s current shape and Apple's ToS: fine
for personal use, but expect the occasional re-scrape/re-login when Apple changes
things. Catalog and the full library read/write cycle are **verified live**
(2026-07-21) against a real subscribed account; two quirks worth knowing: library
**writes are async** (an added song appears a few seconds later), and library
**removal** goes through Apple's internal `amp-api` host. The official, stable,
ToS-compliant route ($99/yr membership signing a JWT + MusicKit JS) is
deliberately **not** built here.

## What makes Apple different from spotify-cli

Apple Music has **no OAuth/PKCE flow**. Two tokens reach the API, and this CLI
gets them the no-paid-membership way (Path B):

- **Developer token** — required on *every* request, catalog included. This CLI
  does **not** sign one (that would need a paid Apple Developer membership + a
  MusicKit `.p8` key). Instead it **scrapes the public developer token** the
  `music.apple.com` web player already carries. Fully automatic — no login, no
  user action. Valid ~6 months; re-scraped when stale.
- **Media-user-token** — required only for your library (`/me/*`). This is the
  cookie Apple sets when you sign in to `music.apple.com`. You just **sign in in
  your normal browser**, and `apple-music login` **lifts the cookie from that
  browser** (Firefox/Chrome/…) — no DevTools, no copy-paste. This is the single
  human-in-the-loop step (mirrors `splice-cli`'s browser cookie lift).

So **catalog commands work with zero setup**; only library commands need login.

> 🎟️ **Library data also needs an active Apple Music _subscription_** (the
> consumer ~monthly one — separate from both the login and any developer
> membership). Without it, `/me/*` returns 400 *Insufficient Privileges* and the
> CLI says so plainly. Auth still works (login/storefront succeed); it's the
> account tier that gates the data.

> ⚠️ Path B is the unofficial, reverse-engineered route (like `splice-cli`). It
> depends on `music.apple.com`'s current shape and Apple's ToS — fine for
> personal use, but expect the occasional re-scrape/re-login when Apple changes
> things. The official, stable, ToS-compliant route (a $99/yr membership signing
> a JWT + MusicKit JS) is deliberately **not** built here.

## Install

```bash
# Daily use — install once, then a bare `apple-music` from anywhere:
uv tool install .

# Development — from the repo:
uv venv && uv pip install -e ".[dev]"
uv run apple-music --help
```

No credentials are required to start. Catalog commands work immediately.

## Login (only needed for your library)

1. Sign in to <https://music.apple.com> in your **normal browser** (your Apple
   ID, as usual). Play something so MusicKit sets the cookie.
2. Run:
   ```bash
   apple-music login                    # auto-detects your browser
   apple-music login --browser firefox  # or name it: firefox|chrome|chromium|brave|edge
   ```

It fetches the developer token for you, **lifts the `media-user-token` cookie
from that browser**, validates it against a real `/me/storefront` call, adopts
your account's storefront, and stores it `0600` in
`~/.apple-music-cli/session.json`. No DevTools, no copy-paste.

- **Firefox** needs no extra dependency (cookies are plain sqlite). **Chrome /
  Chromium / Brave / Edge** need `pip install 'apple-music-cli[chrome]'` (their
  cookie values are keyring-encrypted; `browser_cookie3` decrypts them).
- If auto-lift can't find a session, `login` falls back to a manual paste prompt.
- Non-interactive / agents: `apple-music login --token <value>`, or set
  `APPLE_MUSIC_USER_TOKEN` in the environment.
- The browser must be on the **same machine** as the CLI (the cookie store is
  local). For remote/SSH, log in locally then ship the session or inject
  `APPLE_MUSIC_USER_TOKEN` over the SSH channel.

## Commands

```
apple-music login [--browser B] [--token V]  # lift media-user-token from your logged-in browser
apple-music status                            # session / token / storefront state

# catalog (read) — NO login required
apple-music search "<query>" --type song,album,artist,playlist --limit 25
apple-music song   <catalog_song_id>
apple-music album  <catalog_album_id>
apple-music artist <catalog_artist_id>
apple-music playlist-get <catalog_playlist_id> [--since D] [--until D] [--release-year Y] [--artist NAME]

# your library — needs login
apple-music my-playlists [--limit N]
apple-music library [--limit N] [--since D] [--until D] [--release-year Y] [--artist NAME]
apple-music library-add    <catalog_song_id> ...        # add catalog songs to library
apple-music library-remove <library_song_id> ...        # remove by i.xxxx library id
apple-music playlist-create "<name>" [--description "..."] [--song <catalog_id> ...]
apple-music playlist-add   <library_playlist_id> <catalog_song_id> ...
apple-music recent [--limit N]                           # recently played songs
apple-music heavy-rotation [--limit N]                   # most-played mix
```

### ids: catalog vs library

Apple has **two** id spaces. `search`/`song`/… return **catalog** ids (numeric).
Your library items (`library`, `my-playlists`) return **library** ids (`i.xxxx`,
`p.xxxx`). The write commands are explicit about which they take:

- `library-add`, `playlist-add`, `playlist-create --song` → **catalog** song ids.
- `library-remove` → **library** song ids (`i.xxxx`).
- `playlist-add <playlist_id>` → a **library** playlist id (`p.xxxx`).

### Storefront

Catalog is region-scoped. Default is `us`; override with `APPLE_MUSIC_STOREFRONT`
(e.g. `tw`). Once you log in, your account's own storefront is adopted
automatically.

## Exit codes (agents key off these)

- `0` ok · `1` ordinary failure (no results, not found, rate limited, API error)
  · `2` a human step is required (login) — relay the panel verbatim and stop.

## Scope

Catalog search + your library (playlists, saved songs, recently played, heavy
rotation). **No playback control** and **no audio download** (Apple's API offers
neither; downloads are what [`youtube-cli`](../youtube-cli) is for).

## Development

```bash
uv run pytest -q          # unit tests — all mocked, never hit the network
uv run ruff check .
uv run mypy src
```

See `CLAUDE.md` for the architecture and the caged-auth rules.
