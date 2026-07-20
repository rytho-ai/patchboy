# spotify-cli

A thin, agent-drivable CLI over the **official Spotify Web API** — catalog
search plus your own account (playlists, saved tracks). Authenticates with
**Authorization Code + PKCE** (no client secret, no cookie-lifting).

Sibling of `youtube-cli` (thin-shell shape) and `splice-cli` (the OAuth flow
this borrows). Unlike splice-cli there is no reverse-engineered backend — Spotify
offers a sanctioned API, so this only ever talks to it.

## Setup

1. Register an app at <https://developer.spotify.com/dashboard>.
2. Add the redirect URI **`http://127.0.0.1/callback`** (portless — the CLI picks
   a free port at runtime). Spotify **rejects `localhost`**, so it must be the
   `127.0.0.1` literal.
3. Copy `.env.example` → `.env` and set `SPOTIFY_CLIENT_ID` to the app's Client ID
   (public, not a secret).
4. Install: `uv venv && uv pip install -e .`

## Usage

```bash
spotify login                       # browser consent, saves session (0600)
spotify status                      # is the session fresh?

# catalog
spotify search "aphex twin" --type track,album --limit 10
spotify search "lofi" --year 2015-2020 --genre "hip-hop"   # server-side filters
spotify track   <track_id>
spotify album   <album_id>
spotify artist  <artist_id>
spotify playlist-get <playlist_id> [--since 2026-01 --release-year 2020-2024 --artist nujabes]

# your account
spotify my-playlists
spotify saved [--limit 50] [--since 2026-07-01] [--until 2026-07-31] [--release-year 2024] [--artist "soft lipa"]
spotify saved-add    spotify:track:<id> ...
spotify saved-remove spotify:track:<id> ...
spotify playlist-create "My Mix" --description "..."
spotify playlist-add <playlist_id> spotify:track:<id> ...
```

## Logging in over SSH / headless

Login uses an OAuth loopback callback, so the browser and the CLI must agree on
`127.0.0.1:<port>`. Two ways when the CLI runs on a remote box:

- **Port-forward (recommended, nothing to configure):** tunnel the callback port
  when you connect, then log in normally —
  ```bash
  ssh -L 8787:localhost:8787 user@remote
  spotify login            # open the printed URL in your LOCAL browser
  ```
  If 8787 is taken locally, use `spotify login --port 9000` and forward `9000`.
- **Manual paste (no forwarding):** `spotify login --no-server` prints the URL;
  approve it in any browser, then paste the **full** redirect URL from the
  address bar back into the CLI. The browser will show a connection-refused page
  after approving — that's expected, the code is in the URL.

Either way the session is saved on the remote box; every later call is just
outbound HTTPS, and refresh is headless — login is the only step touching a browser.

**If an AI agent is doing this on your behalf** (not you typing the commands
yourself): neither mode above works unmodified — the agent's own sandbox may
not share a loopback with your browser (default mode), and it can't feed input
into an already-running interactive prompt across separate tool calls
(`--no-server`). The agent should instead call `auth.begin_login`/
`auth.complete_login` directly, split across two steps with the challenge
persisted to disk between them — the exact recipe is in `CLAUDE.md` under
"Agent-driven login." Point the agent there rather than asking it to just run
`spotify login`.

## Exit codes (agents key off these)

- `0` — ok
- `1` — ordinary failure (no results, not found, rate limited, API error)
- `2` — a human step is required (login / re-login). A panel prints exact steps;
  relay it verbatim and stop.

## Development Mode reality (important)

A personal app stays in Spotify **Development Mode** (Extended Quota needs a
registered business + 250k MAU). That means, as of 2026: a small authenticated-
user cap, the owner must hold Premium, and a swathe of endpoints are unavailable
to dev-mode apps — **recommendations, audio-features/analysis, related-artists,
artist top-tracks, browse/new-releases, batch "get several" endpoints, 30s
preview URLs** — plus `search` limit is capped at 10. This CLI is built only on
the surface that survives (single-item GETs, search, `/me/*`). Several write-side
endpoint shapes reflect Spotify's fast-moving Feb 2026 restructuring and are
marked `# verify-live` in `client.py` — confirm them against your real account
before trusting them. See `docs/spotify-cli-plan.md`.
