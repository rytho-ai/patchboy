---
name: spotify
description: Search Spotify's catalog and manage the user's own account (playlists, saved/liked tracks) via spotify-cli — the official Web API with Authorization Code + PKCE. Use when the user asks to search Spotify, look at or edit their playlists, read/add/remove liked songs, or pull their library. Works for free-tier accounts. NOT for playback control or audio download.
---

# Spotify — operating manual

`packages/spotify-cli` is a thin CLI over the **official Spotify Web API**
(Authorization Code + PKCE). Sibling of `/youtube` (thin shell) and `/splice`
(the OAuth flow it borrows). This skill is the decision manual; full command
reference + architecture: `packages/spotify-cli/README.md` and its `CLAUDE.md`.

## Login — the one gate, and how it works when YOU drive it

Every command needs a saved session. If there's none (or it can't refresh),
the command **exits 2** and prints a panel — that's the human-in-the-loop
handoff: relay it and stop, don't guess a workaround.

The actual login needs a browser click with the user's own Spotify
credentials — **you can never do that click**. But you also **cannot** just
run `spotify login` yourself: its loopback-server mode may bind a network
namespace the user's browser can't reach, and `--no-server` blocks on an
interactive paste-prompt you can't feed across tool calls. **When logging in
on the user's behalf, use the two-step `begin_login`/`complete_login` recipe
in `packages/spotify-cli/CLAUDE.md` → "Agent-driven login"** (build+persist a
challenge and print the URL; the user approves in their browser and pastes the
redirect URL back; you exchange it). Don't reinvent PKCE.

If the user is running commands themselves at a terminal, plain `spotify
login` (or `--no-server` over SSH) is fine — that guidance is in the README.

## Exit codes (key off these)

- `0` ok · `1` ordinary failure (no results, not found, rate limited, API
  error) · `2` a human login step is required — relay the panel verbatim, stop.

## Commands

```
spotify login [--no-server] [--port N]     # browser consent (see login note above)
spotify status                              # is there a fresh session?

# catalog (read) — a query for search, an id for the single-item gets
spotify search "<query>" --type track,album,artist,playlist --limit 10
                         [--year YYYY|YYYY-YYYY] [--genre jazz]   # server-side filters
spotify track   <track_id>
spotify album   <album_id>
spotify artist  <artist_id>
spotify playlist-get <playlist_id> [--since D] [--until D] [--release-year Y] [--artist NAME]

# the user's own account
spotify my-playlists [--limit N]
spotify saved [--limit N] [--since D] [--until D] [--release-year Y] [--artist NAME]
spotify saved-add    spotify:track:<id> ...
spotify saved-remove spotify:track:<id> ...
spotify playlist-create "<name>" [--public] [--description "..."]
spotify playlist-add <playlist_id> spotify:track:<id> ...
spotify top --type artists|tracks --range short|medium|long --limit N   # most-played
```

**id vs URI:** reads take a bare id or a free-text query; **write** commands
(`saved-add/remove`, `playlist-add`) take full Spotify **URIs**
(`spotify:track:...`), which you get from a prior `search`'s output.

**`top` (most-played):** unlike saved/playlists (what the user *curated*), `top`
is *behavioral* — what they actually play most, across short/medium/long_term.
For "what would this user enjoy" questions, the behavioral signal usually beats
the curated one. Needs the `user-top-read` scope (added; a re-login grants it).

**Filtering:** two mechanisms. `search --year/--genre` are Spotify's own
*server-side* query filters (you can also type `year:2015-2020`, `tag:new`,
`tag:hipster`, `artist:`, `album:` right in the query). `saved`/`playlist-get`
`--since/--until` (when the user liked/added it), `--release-year`, `--artist`
are *client-side* filters over the fetched page (raise the page with `--limit`,
max 50). **Acoustic filters — BPM / key / energy — are NOT possible via Spotify**
(the audio-features endpoint is permanently dead for this app); that needs a
separate MIR analysis over the audio, not this CLI.

**Playlist items visibility:** `playlist-get` returns full items only for
playlists the user owns or collaborates on; for others it shows metadata only
and says so — that's an API limit, not a failure.

## Development Mode — endpoints that DON'T exist for this app (never propose them)

A personal app is stuck in Development Mode. These are **gone / 403** and no
command wraps them — don't suggest them as a next step or assume the CLI is
just missing a flag:

- **recommendations / "make me a playlist like X"** — the recommendations
  endpoint is dead. Discovery has to be done by your own knowledge + `search`
  (see how `docs/taste.md` drove a discovery playlist), not an API call.
- **audio-features / audio-analysis** (tempo, key, energy, danceability) — dead.
- **related-artists, artist top-tracks, new-releases, browse categories** — dead.
- **batch "get several"** — only single-item gets; loop one id at a time.
- **30-second preview URLs** — gone.
- `search` **limit is capped at 10** (the CLI clamps and warns).

## Scope of this CLI

Catalog search + the user's account (playlists, saved tracks). **No playback
control** (that's Premium + device-bound, deliberately out of scope) and **no
audio download** (Spotify's API doesn't offer it; that's what `/youtube` is
for). Free-tier accounts can use everything here.
