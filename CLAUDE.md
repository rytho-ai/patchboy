# CLAUDE.md — Development principles for apple-music-cli

Governance for Claude (or any AI assistant) working on this codebase. Same
discipline as siblings `spotify-cli` (thin shell) and `splice-cli` (caged,
reverse-engineered auth). This repo is a **thin shell over Apple Music's
`api.music.apple.com`**, authed the **borrowed-token** way (Path B — no paid
membership). The single most important rule is **stay thin** — wire knowledge
stays caged.

## Prerequisites at a glance (three independent axes)

Don't conflate these — they gate different things and fail differently (the
detailed "why" is under *Auth — the load-bearing facts* below):

| Axis | Catalog (`search`/`song`/…) | Library (`/me/*`) |
|---|---|---|
| Paid Apple Developer membership ($99) | ✗ never (Path B) | ✗ never (Path B) |
| Browser login (media-user-token) | ✗ not needed | ✓ required (lifted from a local browser) |
| **Active Apple Music subscription** | ✗ not needed | ✓ **required** (free trial counts) |

So: **catalog = zero setup**; **library = a local browser signed in to
music.apple.com + an active/trial subscription**. A subscription-less account
authenticates fine (login/storefront work) but every `/me/*` returns 400 →
`AppleSubscriptionError` (exit 1, not the exit-2 login handoff — re-login can't
fix a tier). Catalog reaches `music.apple.com` + `api.music.apple.com`; the
browser used for login must be on the **same machine** (local cookie store).

## Prime directive: thin shell, caged wire

The CLI owns four things and nothing else:

1. **Argument parsing** (Typer commands + flags).
2. **Calling the caged client** (`client.Client`).
3. **Rendering** the returned models (`render.py`).
4. **Presenting errors + exit codes** (0/1/2 — never a raw traceback).

Everything about Apple's wire — endpoints, the `{id,type,attributes}` JSON
shape, the token scrape, the `Origin` binding, rate-limit classification —
lives in the **caged** modules. If you import `httpx`, a token detail, or a raw
Apple JSON shape *outside* a caged module, stop: it belongs in one.

```
cli.py       thin shell an agent drives (parse → client → render → exit code)
render.py    presentation only (rich tables; markup.escape every upstream string)
models.py    stable hand-authored vocabulary (Song/Album/Artist/Playlist/...)
config.py    Settings: storefront, session path, env-injected token (caller-owned)
── caged ──   the only modules that know Apple's surface:
auth.py      borrowed-token flow (scrape developer token; hold media-user-token)
browsers.py  lift the media-user-token cookie from a logged-in browser (login)
session.py   atomic 0600 token persistence
client.py    api.music.apple.com/v1 calls + JSON→models maps
```

## Auth — the load-bearing facts (Path B, borrowed tokens)

There is **no OAuth/PKCE** here. Two tokens reach the API, obtained two ways:

- **Developer token** — required on *every* request, catalog included. We do
  **not** sign one (that's Path A: a paid Apple Developer membership + a MusicKit
  `.p8` key). We **scrape the public token** the `music.apple.com` web player
  carries (`auth.fetch_developer_token`). Automatic, no user action, valid
  ~6 months; `exp` is read from the JWT so a stale one is re-scraped.
- **`Origin: https://music.apple.com` is mandatory** — verified live
  2026-07-21: the borrowed token is bound to the web player's origin;
  `api.music.apple.com` returns **401 for any request (catalog too) that omits
  the `Origin` header**, and 200 with it. It travels with the token, so it lives
  on `AppleMusicSession.catalog_headers` / `user_headers`. An easy, silent-
  failing thing to regress.
- **Media-user-token** — required only for `/me/*` (the user's library). This is
  per-user: the cookie Apple sets on sign-in to `music.apple.com`. The user signs
  in in their normal browser and `browsers.py` **lifts the cookie** from the
  browser's cookie store (`apple-music login [--browser …]`) — splice-cli's
  firefox.py/chrome.py pattern; Firefox is a plain sqlite read, Chromium needs
  `browser_cookie3`. Verified live 2026-07-21: `media-user-token` is a real
  cookie on `.music.apple.com` (len ~246), and a lifted one authed `/me/storefront`.
  A `/me/*` call without a token raises `AppleMusicAuthError` *before* any network
  hit (via the `user_headers` property) → the exit-2 handoff.
- **Library DATA needs an active Apple Music _subscription_** — a different axis
  from auth. Verified live 2026-07-21: a validly-authed account with no active
  subscription gets 400 *Insufficient Privileges* (CloudLibrary / ListeningHistory)
  on every `/me/library/*` and `/me/recent/*`, while `/me/storefront` still 200s.
  `client._request` maps that 400 → `AppleSubscriptionError` (exit 1, NOT the
  exit-2 login handoff — re-login can't fix a subscription tier). So `login`
  validates against `/me/storefront` (subscription-free), not a library call.
- **Secrets never touch git or stdout.** Tokens live at 0600 in
  `~/.apple-music-cli/session.json`; print only freshness/presence, never a token.

### verify-live markers

Path B rests on Apple's *un*documented surface. Two risks carry `# verify-live`:

1. **The scrape** (`auth.fetch_developer_token`) targets the current
   `music.apple.com` bundle layout. If it starts returning nothing, Apple
   reshaped its JS — fix the regexes in `auth.py`; nothing above knows the token
   has a source.
2. **The library write shapes** (`client.add_to_library` /
   `remove_from_library` / `create_playlist` / `add_to_playlist`) are the
   under-documented, historically-shifting part of Apple's API (removal
   especially). They are coded to the best-known shapes but were **not** exercised
   against a real logged-in account here (catalog was — see the live results in
   the README). Confirm each against a real account before removing the marker.

## Agent-driven login — you CAN do this one (unlike spotify-cli)

Apple's login is not OAuth — it's a browser cookie. So if you're an AI assistant
logging in on the user's behalf, there's no loopback-namespace problem: the
developer token you fetch yourself, and the media-user-token is lifted from the
browser the user is already signed in on. The clean path:

1. Ask the user to sign in to `music.apple.com` in their normal browser (and
   play something so MusicKit sets the cookie).
2. Run `apple-music login` (auto-detects) or `--browser firefox|chrome|…`. It
   fetches the developer token, lifts the `media-user-token` cookie, validates it
   with one real `/me/storefront` call, adopts the account's storefront, saves
   0600. (`--token`/`APPLE_MUSIC_USER_TOKEN` still work for a direct paste.)
3. Verify with `apple-music status`.

Two things you can't do: the browser sign-in itself (their Apple credentials),
and the cookie-lift when the browser is on a *different* machine (the cookie
store is local — for remote/SSH, lift locally then ship the token). Everything
else is fair game.

## Human-in-the-loop — the exit-code convention

- **0** — ok.
- **2** — a human step is required (login). Print a panel with exact steps
  (`_needs_human`), relay verbatim, STOP. Only `/me/*` (library) commands can
  raise this; catalog commands never need a human.
- **1** — an ordinary failure (no results, not found, rate limit, API error,
  bad filter value).

## Catalog vs library ids — don't conflate them

Apple has two id spaces, and the write commands are strict about which each
takes. Catalog ids (numeric) come from `search`/`song`; library ids (`i.xxxx`,
`p.xxxx`) come from `library`/`my-playlists`. `add_to_library` /
`add_to_playlist` / `create_playlist(song_ids=…)` take **catalog** ids;
`remove_from_library` takes **library** ids; `add_to_playlist(playlist_id=…)`
takes a **library** playlist id. This is caged in the model `type` field and the
command help — keep it accurate.

## Adding a capability — which layer

1. **New Apple call / a changed JSON shape** → `client.py` (caged), mapping the
   raw `{id,type,attributes}` onto a `models.py` type. Never leak wire shapes up.
2. **New user vocabulary** → extend `models.py` (hand-authored, minimal — only
   fields the CLI renders).
3. **New command / flag / rendering** → `cli.py` (+ `render.py`). Surface a
   capability the client already supports; if it doesn't, do step 1 first.

## Environment & tooling

- **src layout (required)** — package under `src/apple_music_cli/`.
- **`uv` first, two entry paths** — daily use is `uv tool install .` then a bare
  `apple-music` from anywhere; development is `uv run …` + `uv pip install -e
  ".[dev]"` from the repo. Both are first-class. Unlike spotify-cli there is **no
  required credential**, so no home-dir `.env` is needed to start; the optional
  `~/.apple-music-cli/.env` fallback only carries an env-injected
  `APPLE_MUSIC_USER_TOKEN` / `APPLE_MUSIC_STOREFRONT` for global use.
- Tests mock at the client/auth boundary; do NOT hit the network in unit tests.
  Worth covering: JWT `exp` parsing, the scrape against a captured bundle,
  header shape (Origin present, user vs catalog), session atomic save/load +
  0600, JSON→model maps, the two id spaces, and the exit-code contract per
  command.

## Communication

Converse with the user in **Traditional Chinese (Taiwanese phrasing)** — plain,
direct, concrete analogies. Keep code, identifiers, commit messages, and this
file in English.
