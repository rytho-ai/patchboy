---
name: applemusic
description: Search Apple Music's catalog and manage the user's own library (playlists, saved songs, recently played, heavy rotation) via applemusic-cli. Catalog works with NO login; the library half lifts a media-user-token from the user's logged-in browser (and needs an active Apple Music subscription). Use when the user asks to search Apple Music, look at/edit their Apple Music library or playlists, or see what they've been playing. NOT for playback control or audio download. NOT the same as /spotify.
---

# Apple Music — operating manual

`packages/applemusic-cli` is a thin CLI over **Apple Music's
`api.music.apple.com`**. Sibling of `/spotify` (same thin-shell shape) and
`/splice` (same reverse-engineered "borrowed token" auth). This skill is the
decision manual; full command reference + architecture:
`packages/applemusic-cli/README.md` and its `CLAUDE.md`.

## The one thing that's different from Spotify: catalog needs NO login

There are two token layers, and they gate different halves:

- **Catalog** (search, song/album/artist/playlist lookup) — works with **zero
  setup**. The developer token is scraped automatically from the web player. Just
  run the command.
- **Library** (`/me/*`: your playlists, saved songs, recent, heavy-rotation, and
  all writes) — needs a **media-user-token**. Without it these commands **exit 2**
  and print a panel — the human-in-the-loop handoff: relay it and stop.

## Login — you CAN drive this one (it's not OAuth)

Unlike `/spotify`, Apple's login is a browser cookie lift, so there's no
loopback-namespace trap. When logging in on the user's behalf:

1. Ask them to sign in to <https://music.apple.com> in their normal browser
   (Apple ID, as usual) and play something so MusicKit sets the cookie.
2. Run `applemusic login` (auto-detects the browser) or
   `applemusic login --browser firefox|chrome|chromium|brave|edge`. It fetches
   the developer token, **lifts the `media-user-token` cookie from that browser**,
   validates it with a real `/me/storefront` call, adopts the storefront, saves
   0600. (`--token '<value>'` or `APPLE_MUSIC_USER_TOKEN` still allow a direct
   paste.)
3. Confirm with `applemusic status`.

Can't do: the browser sign-in itself (their Apple creds), or a cookie-lift when
the browser is on a *different* machine (cookie store is local — for remote/SSH,
lift locally then ship the token). Chrome/Chromium/Brave/Edge need
`applemusic-cli[chrome]`; Firefox needs no extra dependency.

## Library needs an active Apple Music *subscription* (not just login)

`/me/*` (library, recent, heavy-rotation, all writes) returns **400 Insufficient
Privileges** if the signed-in account has no active Apple Music subscription —
the CLI maps this to a clear message and **exit 1** (NOT the exit-2 login
handoff; re-login can't fix it). Auth is still fine (login/`status`/catalog all
work). If a user hits this, the answer is "subscribe at music.apple.com," not
"log in again." Catalog commands never need a subscription.

## Exit codes (key off these)

- `0` ok · `1` ordinary failure (no results, not found, rate limited, API error)
  · `2` a human login step is required (library only) — relay the panel, stop.

## Commands

```
applemusic login [--browser B] [--token V]  # lift media-user-token from your browser
applemusic status                            # session / token / storefront state

# catalog (read) — NO login required
applemusic search "<query>" --type song,album,artist,playlist --limit 25
applemusic song   <catalog_song_id>
applemusic album  <catalog_album_id>
applemusic artist <catalog_artist_id>
applemusic playlist-get <catalog_playlist_id> [--since D] [--until D] [--release-year Y] [--artist NAME]

# the user's own library — needs login
applemusic my-playlists [--limit N]
applemusic library [--limit N] [--since D] [--until D] [--release-year Y] [--artist NAME]
applemusic library-add    <catalog_song_id> ...
applemusic library-remove <library_song_id> ...     # i.xxxx ids from `library`
applemusic playlist-create "<name>" [--description "..."] [--song <catalog_id> ...]
applemusic playlist-add   <library_playlist_id> <catalog_song_id> ...
applemusic recent [--limit N]                        # recently played songs
applemusic heavy-rotation [--limit N]                # most-played mix
```

## The two id spaces — the #1 thing to get right

Apple has **catalog** ids (numeric, from `search`/`song`) and **library** ids
(`i.xxxx`/`p.xxxx`, from `library`/`my-playlists`). Writes are strict:

- **catalog** song ids → `library-add`, `playlist-add`, `playlist-create --song`.
- **library** song ids (`i.xxxx`) → `library-remove`.
- **library** playlist id (`p.xxxx`) → `playlist-add <playlist_id>`.

Pass the wrong space and Apple rejects it. When adding a song the user found via
`search`, use that search row's catalog id.

## Personalization vs curation — Apple actually has both, live

Unlike Spotify's dev-mode (where recommendations/audio-features are dead), Apple
keeps the **behavioral** signal working with a media-user-token:

- `library` / `my-playlists` = what the user **curated**.
- `recent` (recently played) + `heavy-rotation` (most-played mix) = what they
  **actually play**. For "what would this user enjoy" questions, the behavioral
  signal usually beats the curated one.

## Filtering

`library`/`playlist-get` take *client-side* filters over the fetched page:
`--since/--until` (when the user added it), `--release-year` (the song's era),
`--artist` (artist-name substring). Raise the page with `--limit` first.
**Acoustic filters — BPM / key / energy — are NOT available** (Apple's API
doesn't expose them); that needs a separate MIR analysis over the audio.

## Scope & caveats

- **Storefront** is regional (default `us`; `APPLE_MUSIC_STOREFRONT=tw`, or
  auto-adopted on login). Catalog results differ by storefront.
- **Borrowed-token fragility**: this is the unofficial route. If catalog suddenly
  401s or the scrape fails, Apple reshaped the web player — it's a code fix in
  `auth.py`, not a user error. Library *write* shapes are marked `verify-live`
  (not yet exercised against a real account) — treat first-time writes as
  experiments and check the result.
- **No playback control, no audio download** — Apple's API offers neither;
  downloads are what `/youtube` is for.
