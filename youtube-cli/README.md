# youtube-cli

A thin CLI + Claude Code skill over `youtube-toolkit` — search, info,
transcript, analyze, download, comments, channel, playlist, captions,
formats, sponsorblock, plus reading **your own** channel/playlists/subscriptions.

Three access tiers:

- **Zero setup** (most commands): search's default path, info, transcript,
  analyze, download, channel, playlist, captions, formats, sponsorblock — no
  key, no login.
- **Data API key** (`YOUTUBE_API_KEY`, not a login): `search --api` (a bare
  keyword search against the Data API — not filtered or trending; `--sort`
  doesn't apply, see below) and `comments` (youtube-toolkit has no no-API
  fallback for comments — it always calls the Data API).
- **OAuth login** (`youtube login`): reading **your own** account —
  `my-channel`, `my-playlists`, `subscriptions`. An API key identifies the
  *app*, not *you*, so it structurally can't read "mine". This is the one
  place the CLI has a login and a human-step (exit 2) handoff. See
  **[Reading your own account](#reading-your-own-account-oauth)** below.

## Why this exists

`youtube-toolkit` is a mature library (5 sub-APIs: get/download/search/
analyze/stream, layered services → handlers, robust multi-backend fallback)
but has no CLI. This package is the thin shell splice-cli's `cli.py` would
be if the "toolkit" and "cli" halves were separate packages instead of one —
`youtube-cli` depends on `youtube-toolkit` (a local path dependency; there's
an unrelated same-named package on PyPI, don't confuse the two) and adds
nothing but argument parsing and rendering.

## Setup

For daily use, install the CLI once with uv's tool installer:

```bash
cd packages/youtube-cli
uv tool install .
youtube --help
```

Only needed for `search --api` and `comments` (every other command works
with zero setup):

```bash
cp .env.example .env
# then edit .env and fill in YOUTUBE_API_KEY
# (Google Cloud Console → APIs & Services → Credentials → API key,
#  with "YouTube Data API v3" enabled for that project)
```

For local development from this repository:

```bash
uv pip install -e ".[dev]"
uv run youtube --help
```

## Reading your own account (OAuth)

`my-channel` / `my-playlists` / `subscriptions` read data that belongs to
*you*, which an API key can't — the API key says which *app* is asking, never
*who you are*. That needs an OAuth login. It's a two-part, one-time setup:

**1. Register an OAuth client (once, ever).** Run `youtube setup` for the
exact click-by-click steps. In short: in Google Cloud Console, enable
"YouTube Data API v3", configure the OAuth consent screen as **External /
Testing** with **your own account added as a test user**, create an OAuth
client ID of type **Desktop app**, and download its `client_secrets.json`.
Point the CLI at it: `YOUTUBE_OAUTH_CLIENT_SECRETS=/path/to/that.json` (in
`.env` or the environment). This is the same one-time app registration
spotify-cli's `SPOTIFY_CLIENT_ID` needs — you do it once and never again.

**2. Log in.**

```bash
youtube login       # opens a browser; approve once. Token saved 0600 at ~/.youtube-cli/token.json
youtube status      # confirm
youtube my-channel
```

Notes:
- **Read-only.** The requested scope is `youtube.readonly` — this CLI never
  posts, uploads, edits, or deletes on your behalf.
- **Testing-mode tokens expire ~weekly.** Keeping the consent screen in
  "Testing" avoids Google's app-verification review; the trade-off is that
  you'll re-run `youtube login` roughly once a week. (Going through
  verification removes that, but it's a real review process — not worth it for
  a personal tool.)
- **The login browser must be on the same machine as the CLI** (the redirect
  is a `localhost` callback). For a headless/remote box, log in locally and
  copy `~/.youtube-cli/token.json` over.
- **Once logged in you can drop `YOUTUBE_API_KEY` entirely** — OAuth is a
  superset, so `search --api` and `comments` work through it too.
- A `my-*` command with no login exits **2** with a "run `youtube login`"
  panel (the one human-step handoff); an agent relays that and stops.

## Commands

```bash
youtube search "<query>" [-n N] [--api] [--sort relevance|date|views|rating]
                         [--duration short|medium|long] [--upload-date hour|today|week|month|year]
                         [--features hd,4k,live,cc,creative_commons,hdr] [--env FILE]
youtube info <url> [--include chapters|heatmap|key_moments|transcript|lyrics ...] [--chapters]
youtube transcript <url> [--lang en]
youtube analyze <url>                 # 50+ metadata fields + engagement heatmap
youtube download <url> [--audio|--video] [--bitrate best|320k|256k|192k|128k] [--prefer-yt-dlp]
                       [--sponsorblock] [--sponsorblock-action remove|mark]
                       [--sponsorblock-categories sponsor,intro,...]
                       [--quality/--format] [--dest/-o DIR] [--env FILE]
youtube comments <url> [-n N] [--order relevance|time] [--search TERM] [--env FILE]
youtube channel <handle> [--videos|--shorts|--streams] [-n N] [--sort newest|oldest|popular]
youtube playlist <url> [--videos [-n N]] [--download [--type audio|video] [--format FMT] [--dest/-o DIR]] [--env FILE]
youtube captions <url> [--lang en] [--format srt|vtt|txt|json] [--dest/-o PATH]
youtube formats <url>                 # available resolutions/codecs, pre-download planning
youtube sponsorblock <url>            # preview sponsor/intro/outro segments, no download

# your own account (OAuth — see "Reading your own account" below)
youtube setup                         # print the one-time Google Cloud steps
youtube login [--env FILE]            # authorize your account (opens a browser)
youtube status [--env FILE]           # are you logged in? where's the token?
youtube my-channel                    # your channel's stats
youtube my-playlists [-n N]           # your playlists
youtube subscriptions [-n N] [--order alphabetical|relevance|unread]
```

`--env FILE` (on `search`, `download`, `comments`, `playlist`) points at a
specific `.env` file instead of the default lookup. `--dest`/`-o` (on
`download`, `playlist --download`, `captions`) sets the output
directory/path, overriding `YOUTUBE_DOWNLOAD_DIR`.

`--sort` only affects the default no-API `search` path — it's ignored (with
a warning) when combined with `--api`. `--duration`/`--upload-date`/
`--features` route `search` through `search.with_filters` (also no-API,
no-quota) instead of `search.videos`; combined with `--api` they win and
`--api` is ignored with a warning.

`info --include` (repeatable or comma-separated) replaces the old
`--chapters`-only wiring; `--chapters` still works as shorthand for
`--include chapters`.

Combining `--sponsorblock` with `--audio`, `--quality`, `--format`,
`--bitrate`, or `--prefer-yt-dlp` on `download` prints a warning and ignores
those flags — sponsorblock mode always cuts a video download.
`--sponsorblock-action`/`--sponsorblock-categories` only apply in
`--sponsorblock` mode.

`channel --sort` (newest|oldest|popular) applies to `--videos`, `--shorts`,
and `--streams`. `playlist --videos` returns full video detail (title, views,
…) via the toolkit's scrapetube path, degrading to `{url, video_id}` when
scrapetube isn't installed. `playlist --download`'s
`--type`/`--format`/`--dest` only apply with `--download`; `comments
--search` searches within a single video's comments (separate from
`search`, which finds videos).

Twelve commands cover the common cases; `youtube-toolkit` still has methods
across its five sub-APIs (live-stream download/status, thumbnails, filtered/
archived/cookie-authenticated downloads, trending, categories/regions/
languages, replies-to-a-comment, ...) not exposed here — import it directly
for anything not covered rather than growing this CLI to mirror every method
1:1.

## Layout

```
src/youtube_cli/
├── config.py   # API key + download dir (caller-owned, no session file)
├── render.py   # rich tables/panels for search/info/comments
└── cli.py      # thin `youtube` shell — no extraction logic lives here
```

The Claude Code skill lives at `.claude/skills/youtube/SKILL.md`.

## Status

- ✅ `search`, `info` (+ `--chapters`/`--include`), `analyze`, `download` —
  verified end-to-end against real videos.
- ✅ `channel`, `playlist`, `captions`, `formats`, `sponsorblock` — wired
  against confirmed sub-API signatures (`get.channel(.videos/.shorts/.streams)`,
  `get.playlist(.videos)`, `download.playlist`, `download.captions`,
  `get.formats`, `analyze.sponsorblock`); covered by mocked CliRunner tests,
  not yet re-verified against real videos in this pass.
- ✅ `comments` — verified it needs `YOUTUBE_API_KEY` (no no-API fallback
  exists in youtube-toolkit) and fails with a clean message without one.
- ✅ `transcript` — fixed a real bug in youtube-toolkit along the way (see
  below); code path verified correct, but *fetching the actual caption text*
  can hit YouTube's own IP-based rate limiting in some environments (a
  shared/cloud IP with a lot of recent requests) — the same class of problem
  Phase 2's PO-token work targets, just a different endpoint (caption fetch,
  not yt-dlp's player API) that PO tokens may or may not help with.
- 🔧 **Bug fixed in youtube-toolkit**: `handlers/yt_dlp_handler.py`'s
  `get_transcript()` called the pre-1.0 `youtube-transcript-api` surface
  (`YouTubeTranscriptApi.list_transcripts()` classmethod), which no longer
  round-trips real captions against current YouTube. Patched to the current
  instance-based API (`YouTubeTranscriptApi().list()` + attribute-access
  snippets); the dependency is now properly declared (was a lazy, undeclared
  soft-import before).
- 🔜 Phase 2 (planned, in `youtube-toolkit`, not here): a PO-token fallback
  for when yt-dlp gets throttled.
