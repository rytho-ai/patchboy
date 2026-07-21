# spotify-cli — design & landing plan

Status: **v0.1 live** (2026-07-20) — logged in against a real Dashboard app, all
catalog + account commands exercised end-to-end, all endpoint shapes live-verified
(create/add/save-remove/track-count/top-items), a real discovery playlist created,
client-side + server-side filters and a `top` (most-played) command shipped. 51
unit tests green. Registered as the `spotify` skill. This doc is the durable record
of the research and decisions; `CLAUDE.md` is the enforced governance.

## Why this shape

Two siblings set the pattern:
- **youtube-cli** — the thin-shell shape (cli → client → render → exit code).
- **splice-cli** — the OAuth + PKCE + loopback-callback + atomic-0600 session
  machinery (`mcp_auth.py`/`mcp_session.py`), reused here nearly verbatim.

Spotify offers a **sanctioned Web API**, so — unlike splice-cli — there is **no
cookie-lifting, no reverse-engineered backend, no budget/money layer**. One
official auth path (Authorization Code + PKCE) covers both catalog and account.

### Scope & audience (locked 2026-07-20)

- **In scope:** catalog search, playlist read/create/modify, saved/liked
  read/add/remove. **Out of scope: playback control** — deliberately not built.
- **Audience:** free-tier ("一般用戶") users must be able to use it, not just
  Premium. This is **already satisfied** — none of the in-scope endpoints
  (search / playlists / library) require Premium; only playback control does,
  and that's out of scope. So Premium is a non-issue for this CLI.
- **Two different walls, don't conflate them:** (A) *account tier* — free vs
  Premium — is a non-issue here (see above). (B) *user-count cap* — Development
  Mode caps distinct authorized users (~5 as of 2026, manual email allowlist in
  the Dashboard) and lifting it needs Extended Quota (business + 250k MAU,
  effectively closed for a personal app). Wall B is independent of tier. It does
  NOT bite the intended use (yourself + a few allowlisted people); it only bites
  public distribution to unbounded users — which is not a goal.

### Options considered (and rejected)

- **Client Credentials (no user login).** Simplest, but app-level only — cannot
  touch the user's playlists/saved tracks. Rejected: we need the account tier.
- **`sp_dc` cookie-lifting (unofficial).** No app registration, reaches internal
  endpoints, but ToS-risky and fragile. Rejected: sanctioned API exists.
- **Spotify MCP.** No official first-party MCP exists (2026-07). Community
  servers just wrap the same Web API and still require your own Dashboard app +
  OAuth — a dependency without removing any work. Rejected.
- **spotipy SDK.** Its surface is far wider than Development Mode can reach, and
  its auth manager doesn't match this family's exit-code-2 handoff. Using raw
  `httpx` in a caged `client.py` instead. (Note as a fallback if hand-written
  HTTP later becomes a burden.)
- **`sp_dc` cookie path — probed and rejected as a foundation (2026-07-20).**
  Would skip Development Mode entirely, but the `get_access_token` exchange has
  been a hardening treadmill (TOTP → HMAC-OTP; broke ~Mar/Jun/Dec 2025, Feb
  2026) and the shared secret-extraction infra (`spotify-secrets`) was taken
  down under legal threat Oct 2025 (+ DMCA sweep of ~520 repos). It also does
  NOT resurrect the dead `/v1/audio-features`/`recommendations` endpoints (that
  403 is an app-approval gate, not a token-flavor gate); its unique payoff is
  the internal Pathfinder GraphQL (lyrics, internal metadata) — a different
  asset, with its own hash-rotation maintenance. Enforcement to date targets
  tool authors/repos, not individual accounts, but the whole path is a poor
  foundation. If internal-only data is ever needed, it belongs as a caged
  break-glass sub-path, never the core.
- **MPRIS / D-Bus local playback (pwittchen/spotify-cli-linux).** A clean,
  zero-auth, Premium-free FOURTH mechanism — but for *playback control only*
  (it drives the running desktop app; it's catalog/account-blind). Out of scope
  here (no playback). If ever wanted, it's a separate Linux-desktop-only,
  clean-room MPRIS `playback` command group — orthogonal to this Web-API core,
  not a change to it.

## Spotify API reality (verified 2026-07, web-sourced — recheck live)

> These reflect research at scaffold time. The Feb 2026 restructuring is recent
> and fast-moving; treat the write-side endpoint shapes as provisional until
> confirmed against the real account (they carry `# verify-live` in `client.py`).

> **Live-verified 2026-07-20** (against Paul's real account, first `spotify login`):
> read/search/`/me/playlists`/`/me/tracks`/`/playlists/{id}` all work. Two write
> shapes confirmed by direct probe (the Feb 2026 change is REAL). All four
> write/quirk shapes are now live-verified and their `# verify-live` markers
> dropped:
> - **create** → `POST /me/playlists` (classic `POST /users/{id}/playlists` 403s).
> - **add** → `POST /playlists/{id}/items` (old `/tracks` 403s).
> - **save/remove liked** → `PUT`/`DELETE /me/library` with `uris` as a
>   **comma-separated query param** (a JSON body 400s "Missing required field:
>   uris"); classic `PUT/DELETE /me/tracks` and `GET /me/tracks/contains` all 403.
> - **playlist track-count** → lives in `items.total`, not `tracks.total` (the
>   whole `tracks` field was renamed `items`); `_playlist()` reads the new key.
>
> All verified against Paul's real account, restoring state (the save-liked probe
> added then removed a track, leaving liked-songs untouched).

- **Auth**: authorize `https://accounts.spotify.com/authorize`, token
  `https://accounts.spotify.com/api/token`. PKCE → **no client_secret**. Access
  token ~1h. Refresh **may not return a new refresh token** — keep the old one.
- **Redirect URI**: **`localhost` is NOT allowed** — must be the `127.0.0.1`
  literal. Register the **portless** `http://127.0.0.1/callback`; supply the port
  only in the runtime authorize request (the one dynamic-port case still allowed).
- **Scopes**: `playlist-read-private playlist-read-collaborative
  playlist-modify-private playlist-modify-public user-library-read
  user-library-modify`.
- **Development Mode is permanent** for a personal app (Extended Quota needs a
  registered business + 250k MAU). Small authenticated-user cap; owner must hold
  Premium.
- **Unavailable to dev-mode apps** (do not build on these): recommendations,
  audio-features/analysis, related-artists, artist top-tracks, browse/
  new-releases & categories, all batch "get several", 30s preview URLs. `search`
  limit capped at **10**. Other-user profile/playlist reads removed — only
  `/me/*` for account data.
- **Rate limit**: rolling ~30s window, `429` + `Retry-After` seconds.

## Directory

```
spotify-cli/
├── CLAUDE.md · README.md · pyproject.toml · .env.example · .gitignore
├── docs/spotify-cli-plan.md            # this file
├── src/spotify_cli/
│   ├── config.py     Settings: client_id, session_path, redirect_port
│   ├── models.py     Track/Album/Artist/Playlist/SavedTrack/SearchResults/PlaylistDetail
│   ├── auth.py       ← caged: OAuth Auth-Code + PKCE (accounts.spotify.com)
│   ├── session.py    atomic 0600 token persistence
│   ├── client.py     ← caged: api.spotify.com/v1 calls + JSON→models maps
│   ├── filters.py    client-side library filters (added_at / release-year / artist) — one apply()
│   ├── render.py     rich tables (markup.escape on every upstream string)
│   └── cli.py        thin `spotify` shell (parse → client → render → exit code)
└── tests/            test_auth · test_session · test_cli · test_client · test_filters (51 green, no network)
```

## Deltas from splice-cli's OAuth (the risky bits)

1. `register_client()` **removed** — Spotify has no dynamic registration;
   `client_id` is a required config value from the Dashboard.
2. Redirect host `localhost` → **`127.0.0.1`** in both the `HTTPServer` bind and
   the `redirect_uri` string. Silent-failing if regressed (opaque
   `INVALID_CLIENT`).
3. Two hosts: `accounts.spotify.com` (auth) vs `api.spotify.com/v1` (client).
4. Scopes are the Spotify set above, not Splice's `openid profile ...`.
5. No `client_secret` (already true of splice's `token_endpoint_auth_method:none`).
6. Session payload drops `client_id` (static config, not per-registration).

## Command surface

Catalog (need a valid token): `search` (limit ≤10), `track`, `album`, `artist`,
`playlist-get` (items only if you own/collaborate — else metadata-only, surfaced).
Account: `my-playlists`, `saved`, `saved-add`, `saved-remove`, `playlist-create`,
`playlist-add`. Flat Typer app — no nested sub-app needed yet (a future
`playback` group would justify one; deferred, Premium-only).

## Build checklist

- [x] Scaffold package (auth/session/config/models/client/render/cli + metadata).
- [x] Auth spine with the 6 deltas; `spotify --help` lists all commands.
- [x] Unit tests green (PKCE, session 0600 round-trip, refresh fallback,
      exit-code contract) — no network. (20 tests as of 2026-07-20.)
- [x] **Register the Dashboard app**; redirect registered as
      `http://127.0.0.1:8878/callback` (port 8878 — 8787 collided with an
      unrelated local uvicorn); `SPOTIFY_CLIENT_ID` + `SPOTIFY_REDIRECT_PORT=8878`
      in `.env`. (Premium-owner requirement not separately confirmed; login worked.)
- [x] **Live token round-trip** (2026-07-20): logged in via the manual
      `begin_login`/`complete_login` two-step (agent-driven; see CLAUDE.md), session
      saved at 0600, `spotify status` → fresh. Also fixed a bug where `login`'s own
      `SpotifyAuthError` (port-in-use) escaped as a raw traceback → now clean exit 1.
- [x] **Verify all `# verify-live` endpoint shapes** (2026-07-20): create, add,
      save/remove-liked, and the track-count field all confirmed live and markers
      dropped (see reality note above). No `# verify-live` markers remain.
- [x] **Tracks-count fix**: `_playlist()` now reads `items.total`; `my-playlists`
      shows real counts.
- [x] `.claude/skills/spotify/SKILL.md` written + symlinked at
      `rytho-ai/.claude/skills/spotify` (registers like `youtube`/`splice`/`gamut`).
- [ ] Capture real JSON fixtures → add `test_client.py` mapping tests.
- [ ] (optional) filter flags — `saved`/playlist listing by `added_at` time range,
      by release era, by artist. See "Filtering" below.
```

## Filtering — what's possible (2026-07-20, verified live)

Good filtering IS possible, in three doable buckets — plus one dead one that
matters for production work.

**Doable, server-side (works today, no code — just query syntax):** Spotify
`search` accepts field filters in the query string. Verified `jazz year:2024`
returns 2024 tracks. Usable: `year:2020`, `year:2015-2020` (ranges — the "time
range" ask), `genre:jazz`, `artist:`, `album:`, `tag:new` (released last ~2
weeks), `tag:hipster` (low-popularity deep cuts).

**Doable, client-side (needs small filter flags):** `/me/tracks` and playlist
items each carry `added_at` (ISO timestamp of when the user saved/added it) and
each track carries `album.release_date` + `duration_ms`. So filters over a
fetched list are straightforward: `--since/--until` on `added_at` (when you
saved it — the real "time range" over the library), `--release-year` (era of
the music), `--artist`, `--min/--max-duration`. Not built yet; candidate flags
on `saved` / `playlist-get`.

**DEAD — a hard wall, verified against Spotify official 2026-07-20:** BPM / key
/ energy / danceability filtering via Spotify is impossible for this app, and
this is worse than "gated behind business quota." The `audio-features` and
`audio-analysis` endpoints were withdrawn from **all new use cases** on
2024-11-27 (blog: 2024-11-27-changes-to-the-web-api) — **grandfathered-only**:
solely apps that *already had* Extended Quota access *and* were *already
calling* these endpoints before that date keep them. That means:
- Not a dev-mode-vs-business thing — **any app registered after 2024-11-27 is
  403, business or not.** No appeal path documented.
- **Extended Quota does NOT restore them** (official Feb-2026 migration guide
  confirms Extended Quota apps are simply "unaffected" = grandfathered; a *new*
  Extended Quota approval doesn't reopen these two). Extended Quota is also
  organizations-only + 250k MAU (2025-04-15 criteria post).
- **No official replacement** — no successor endpoint, no tempo/key/energy field
  on Get Track. Dead end, not a relocation.

So don't spend any time looking for a Spotify door here — there isn't one, now
or later. The acoustic data isn't *locked*, just not Spotify's to give anymore
(it's computable from audio via Essentia/librosa — reportedly Spotify's own
original method). Correct architecture: spotify-cli pulls the track list +
metadata → Paul's own MIR capability layer (openmirlab / audion / pytimbral)
analyzes the audio → filter on acoustic features. This makes the capability
layer the *only* path to this data, not a re-implementation of a Spotify
feature. Spotify owns "which tracks exist"; the capability layer owns "what
they sound like."

## Podcasts / shows / audiobooks — capability boundary (2026-07-20)

The API gives a **catalog + subscription layer only — no content layer.**

- **Available:** `GET /shows/{id}`, `/shows/{id}/episodes`, `/episodes/{id}`
  (single-item metadata — batch "get several" is dead); `GET /me/shows` and
  `/me/episodes` (your subscribed/saved, `user-library-read`); save/remove via the
  generic `PUT/DELETE /me/library` with show/episode URIs; `search type=show,episode`.
  Audiobooks/chapters mirror this shape.
- **NOT available (the valuable part):** episode **transcripts** (exist in the app,
  not the API), **audio / download**, **in-episode full-text search** (catalog-level
  only), chapter markers, play position / resume points. Also: `recently-played`
  does **not** cover podcast episodes, only music.

**Decision: don't wire podcasts into spotify-cli.** Paul's line is music/production;
podcast metadata adds little, and the one thing worth having — transcript/content —
is exactly what Spotify withholds. For a "listen → notes/analysis" pipeline the
right source is one that exposes content: `/youtube` (has transcripts), a podcast
RSS feed, or local Whisper transcription — not the Spotify API. Revisit only if a
concrete metadata-only use-case appears.
