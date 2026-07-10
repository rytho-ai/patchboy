---
name: youtube
description: Search, get info, transcript, analyze, download, and read comments from YouTube via the unofficial youtube-cli (wraps youtube-toolkit). Use when the user asks to find YouTube videos, get a video's metadata/transcript/comments, or download audio/video. No login required for most operations.
---

# YouTube toolkit — operating manual

`packages/youtube-cli` is a thin CLI over `packages/youtube-toolkit` (a
mature library: 5 sub-APIs, layered services/handlers, multi-backend
fallback). This skill is the decision manual; full command reference and
architecture: `packages/youtube-cli/README.md`.

## No login, no budget — much simpler than the Splice skill

Unlike `/splice`, there is **no session-lifting, no login bridge, no
purchase/budget system.** Every command here is public, unauthenticated
access. The only credential involved is an optional Data API key
(`YOUTUBE_API_KEY`, from Google Cloud Console) — a config value, not a login.
Nothing here spends money or requires the user to pass any human-only gate.

## The one thing worth knowing: two commands need an API key, the rest don't

| Needs `YOUTUBE_API_KEY` | Works with zero setup |
|---|---|
| `search --api` (a bare keyword search against the Data API — quota-costing, but not filtered or trending; `--sort` doesn't apply to it) | `search` (default, no-API path) |
| `comments` (youtube-toolkit has no no-API fallback for it — verified) | `info`, `transcript`, `analyze`, `download` |

If a command that needs the key is run without one, it fails with a clear
message telling the user to set `YOUTUBE_API_KEY` — relay that message, don't
guess a workaround or ask the user to log into anything (there's no login
here at all).

## Commands

```
youtube search "<query>" [-n N] [--api] [--sort relevance|date|views|rating]
                         [--duration short|medium|long] [--upload-date hour|today|week|month|year]
                         [--features hd,4k,live,cc,creative_commons,hdr] [--env FILE]
youtube info <url> [--include chapters|heatmap|key_moments|transcript|lyrics ...] [--chapters]
youtube transcript <url> [--lang en]
youtube analyze <url>              # 50+ metadata fields + engagement heatmap
youtube download <url> [--audio|--video] [--bitrate best|320k|256k|192k|128k] [--prefer-yt-dlp]
                       [--sponsorblock] [--sponsorblock-action remove|mark]
                       [--sponsorblock-categories sponsor,intro,...]
                       [--quality/--format] [--dest/-o DIR] [--env FILE]
youtube comments <url> [-n N] [--order relevance|time] [--search TERM] [--env FILE]
youtube channel <handle> [--videos|--shorts|--streams] [-n N] [--sort newest|oldest|popular]
youtube playlist <url> [--videos [-n N]] [--download [--type audio|video] [--format FMT] [--dest/-o DIR]] [--env FILE]
youtube captions <url> [--lang en] [--format srt|vtt|txt|json] [--dest/-o PATH]
youtube formats <url>               # available resolutions/codecs, pre-download planning
youtube sponsorblock <url>          # preview sponsor/intro/outro segments, no download
```

`--env FILE` points at a specific `.env` instead of the default lookup.
`--dest`/`-o` (`download`, `playlist --download`, `captions`) sets the
output directory/path. `--sort` on `search` only applies to the default
no-API path — combined with `--api` it's ignored with a warning, not
silently dropped; `--duration`/`--upload-date`/`--features` route `search`
through the no-API advanced-filter path instead (also ignoring `--api` with
a warning). `info --include` (repeatable/comma-separated) replaces the old
`--chapters`-only wiring; `--chapters` still works as shorthand. Combining
`--sponsorblock` with `--audio`/`--quality`/`--format`/`--bitrate`/
`--prefer-yt-dlp` on `download` warns and ignores those flags (sponsorblock
mode always produces a video); `--sponsorblock-action`/`-categories` only
apply in that mode. `channel --sort` only applies to `--videos` (the
sub-API has no sort order for `--shorts`/`--streams`). `comments --search`
searches within one video's comments — different from the `search`
command, which finds videos.

These twelve cover the common cases. `youtube-toolkit` still has methods not
exposed as CLI commands (live-stream download/status, thumbnails, filtered/
archived/cookie-authenticated downloads, trending, categories/regions/
languages, comment replies, ...) — for anything beyond these, note the gap
to the user rather than assuming it's impossible; the underlying library
likely already supports it.

## Known limitation: transcript can hit YouTube's own IP rate-limiting

`transcript`'s code path is correct (verified against real captions), but the
actual caption-fetch request can be blocked by YouTube if the machine's IP
has made a lot of recent requests (shared/cloud IPs are more exposed to
this). If `transcript` fails with a YouTube "blocking requests from your IP"
message, that's not a bug to route around silently — tell the user directly;
retrying immediately won't help. This is a different endpoint than the one
Phase 2 (PO-token work, planned in youtube-toolkit) targets, so that fix may
not resolve this specific failure mode.

## Spending nothing — but note the real ToS caveat

This automates public YouTube access at conversational/personal-use rates.
Don't batch-scrape (e.g., looping `download` over hundreds of videos in one
session) — that crosses into the same ToS-risk territory splice-cli's
automation does, even though no money or login is involved here.
