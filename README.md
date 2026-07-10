# youtube-cli

A thin CLI + Claude Code skill over `youtube-toolkit` — search, info,
transcript, analyze, download, comments. Unlike `splice-cli`, there's no
login/session/budget layer here: nothing needs a logged-in session. Most
commands need no API key either — `search`/`info`/`transcript`/`analyze`/
`download` all work with zero setup. Two things need a Data API key from
Google Cloud Console (`YOUTUBE_API_KEY`), not a login: `search --api`
(filtered/trending search) and `comments` (youtube-toolkit has no no-API
fallback for comments, verified — it always calls the Data API).

## Why this exists

`youtube-toolkit` is a mature library (5 sub-APIs: get/download/search/
analyze/stream, layered services → handlers, robust multi-backend fallback)
but has no CLI. This package is the thin shell splice-cli's `cli.py` would
be if the "toolkit" and "cli" halves were separate packages instead of one —
`youtube-cli` depends on `youtube-toolkit` (a local path dependency; there's
an unrelated same-named package on PyPI, don't confuse the two) and adds
nothing but argument parsing and rendering.

## Setup

```bash
cd packages/youtube-cli
uv venv && uv pip install -e .
```

Only needed for `search --api` and `comments` (every other command works
with zero setup):

```bash
cp .env.example .env
# then edit .env and fill in YOUTUBE_API_KEY
# (Google Cloud Console → APIs & Services → Credentials → API key,
#  with "YouTube Data API v3" enabled for that project)
```

## Commands

```bash
youtube search "<query>" [-n N] [--api] [--sort relevance|date|views|rating]
youtube info <url> [--chapters]
youtube transcript <url> [--lang en]
youtube analyze <url>                 # 50+ metadata fields + engagement heatmap
youtube download <url> [--audio|--video] [--sponsorblock] [--quality/--format]
youtube comments <url> [-n N] [--order relevance|time]
```

Six commands cover the common cases; `youtube-toolkit` has 40+ methods across
its five sub-APIs (playlists, channels, live streams, key moments, lyrics,
captions export, thumbnails, ...) — import it directly for anything not
covered here rather than growing this CLI to mirror every method 1:1.

## Layout

```
src/youtube_cli/
├── config.py   # API key + download dir (caller-owned, no session file)
├── render.py   # rich tables/panels for search/info/comments
└── cli.py      # thin `youtube` shell — no extraction logic lives here
```

The Claude Code skill lives at `.claude/skills/youtube/SKILL.md`.

## Status

- ✅ `search`, `info` (+ `--chapters`), `analyze`, `download` — verified
  end-to-end against real videos.
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
