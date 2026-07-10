"""youtube — thin CLI shell over youtube-toolkit, meant for a coding agent to drive.

Every command is a few lines: parse args, call youtube_toolkit, render. No
extraction/fallback logic lives here — that's youtube-toolkit's job. Unlike
splice-cli, there's no login/session/budget layer: everything here is public,
unauthenticated access (search's Data API path needs a key, not a login).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from youtube_toolkit import RateLimitedError, YouTubeToolkit

from . import render
from .config import Settings

app = typer.Typer(add_completion=False, help="Thin CLI over youtube-toolkit.")
console = Console()

# User-facing --sort values -> what the no-API (scrapetube) search path
# actually expects. --api ignores sort_by entirely (see search() below).
_SORT_MAP = {
    "relevance": "relevance",
    "date": "upload_date",
    "views": "view_count",
    "rating": "rating",
}


def _toolkit() -> YouTubeToolkit:
    return YouTubeToolkit()


@contextmanager
def _handle():
    """Run a toolkit call and turn any failure into a clean message + exit(1).

    Presenting errors is the CLI's real job (see CLAUDE.md) — never let a
    raw traceback reach the user. ``RateLimitedError`` (YouTube IP-blocking)
    gets a distinct message from an ordinary toolkit failure, since retrying
    a rate-limit immediately won't help but retrying a transient error might.
    Only wrap the toolkit call itself in ``with _handle():`` — don't raise
    typer.Exit from inside the block, or nest command logic that shouldn't
    be swallowed as a generic failure.
    """
    try:
        yield
    except typer.Exit:
        raise
    except RateLimitedError:
        console.print(
            "[red]YouTube is rate-limiting requests from this IP — "
            "retrying now won't help; try later or from a different "
            "network.[/]"
        )
        raise typer.Exit(1)
    except Exception as exc:
        # The exception message is redacted upstream (no API keys), so it's
        # safe to show directly — but still escape it, since it can contain
        # arbitrary text (e.g. an echoed URL or title) that would otherwise
        # be parsed as Rich markup.
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1)


@app.command()
def search(
    query: str,
    limit: int = typer.Option(20, "--limit", "-n"),
    use_api: bool = typer.Option(
        False, "--api", help="Use the YouTube Data API (needs YOUTUBE_API_KEY, costs quota)."
    ),
    sort_by: str = typer.Option("relevance", "--sort", help="relevance|date|views|rating"),
    duration: str = typer.Option(
        None, "--duration", help="short (<4min)|medium (4-20min)|long (>20min) — no-API filter."
    ),
    upload_date: str = typer.Option(
        None, "--upload-date", help="hour|today|week|month|year — no-API filter."
    ),
    features: str = typer.Option(
        None,
        "--features",
        help="Comma-separated: hd,4k,live,cc,creative_commons,hdr — no-API filter.",
    ),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Search for videos. Default path needs no API key and costs no quota."""
    if sort_by not in _SORT_MAP:
        console.print(
            f"[red]Invalid --sort '{escape(sort_by)}'. Choose one of: "
            "relevance, date, views, rating.[/]"
        )
        raise typer.Exit(1)

    settings = Settings.load(env_file)

    if duration or upload_date or features:
        if use_api:
            console.print(
                "[yellow]--duration/--upload-date/--features use the no-API "
                "advanced-filter path (search.with_filters) — ignoring --api.[/]"
            )
        feature_list = [f.strip() for f in features.split(",") if f.strip()] if features else None
        with _handle():
            result = _toolkit().search.with_filters(
                query,
                duration=duration,
                upload_date=upload_date,
                sort_by=sort_by,
                features=feature_list,
                max_results=limit,
            )
        console.print(render.search_filtered_table(query, result))
        return

    if use_api and not settings.api_key:
        console.print(
            "[yellow]--api needs YOUTUBE_API_KEY set (a Data API key from "
            "Google Cloud Console). Falling back to the no-API search "
            "instead, or set the key and retry.[/]"
        )
        use_api = False

    if use_api and sort_by != "relevance":
        console.print(
            "[yellow]--sort doesn't apply to --api search (Data API result "
            "ordering isn't wired up upstream yet) — ignoring it.[/]"
        )

    with _handle():
        results = _toolkit().search.videos(
            query, limit=limit, use_api=use_api, sort_by=_SORT_MAP[sort_by]
        )
    console.print(render.search_table(query, results))


_INCLUDE_CHOICES = {"chapters", "heatmap", "key_moments", "transcript", "lyrics"}


@app.command()
def info(
    url: str,
    include: list[str] = typer.Option(
        None,
        "--include",
        "-i",
        help="Extra data: chapters|heatmap|key_moments|transcript|lyrics "
        "(repeatable, or comma-separated).",
    ),
    chapters: bool = typer.Option(
        False, "--chapters", help="Include chapter list (shorthand for --include chapters)."
    ),
) -> None:
    """Get a video's core metadata."""
    requested: list[str] = []
    for item in include or []:
        requested.extend(part.strip() for part in item.split(",") if part.strip())
    if chapters and "chapters" not in requested:
        requested.append("chapters")

    invalid = [r for r in requested if r not in _INCLUDE_CHOICES]
    if invalid:
        console.print(
            f"[red]Invalid --include value(s): {', '.join(escape(v) for v in invalid)}. "
            f"Choose from: {', '.join(sorted(_INCLUDE_CHOICES))}.[/]"
        )
        raise typer.Exit(1)

    with _handle():
        video = _toolkit().get.video(url, include=requested or None)
    console.print(render.video_info_panel(video))

    if "chapters" in requested and video.chapters:
        console.print(f"[dim]{len(video.chapters)} chapters:[/]")
        for ch in video.chapters:
            start = escape(str(ch.get("formatted_start", "")))
            title = escape(str(ch.get("title", "")))
            console.print(f"  {start}  {title}")
    if "heatmap" in requested and getattr(video, "heatmap", None):
        console.print(f"[dim]Heatmap: {len(video.heatmap)} segments[/]")
    if "key_moments" in requested and getattr(video, "key_moments", None):
        console.print(f"[dim]Key moments ({len(video.key_moments)}):[/]")
        for km in video.key_moments[:10]:
            console.print(f"  {escape(str(km))}")
    if "transcript" in requested and getattr(video, "transcript", None):
        console.print("[dim]Transcript:[/]")
        console.print(escape(str(video.transcript)))
    if "lyrics" in requested and getattr(video, "lyrics", None):
        console.print("[dim]Lyrics:[/]")
        console.print(escape(str(video.lyrics)))


@app.command()
def transcript(
    url: str,
    lang: str = typer.Option("en", "--lang"),
) -> None:
    """Get a video's auto-generated transcript."""
    with _handle():
        text = _toolkit().get.transcript(url, lang=lang)
    if not text:
        console.print("[yellow]No transcript available for this video/language.[/]")
        raise typer.Exit(1)
    # Transcript text is untrusted content (auto-captions routinely contain
    # bracketed tags like "[Music]") — escape so it isn't parsed as markup.
    console.print(escape(text))


@app.command()
def analyze(url: str) -> None:
    """Comprehensive metadata (50+ fields) + engagement (heatmap/key moments)."""
    toolkit = _toolkit()
    with _handle():
        meta = toolkit.analyze.metadata(url)
    with _handle():
        engagement = toolkit.analyze.engagement(url)

    console.print(f"[bold]{escape(str(meta.get('title', url)))}[/]")
    skip = {"title"}
    for key, value in meta.items():
        if key in skip or value in (None, "", [], {}):
            continue
        console.print(f"  [dim]{escape(str(key))}:[/] {escape(str(value))}")

    if engagement.get("key_moments"):
        console.print(f"\n[bold]Key moments[/] ({len(engagement['key_moments'])}):")
        for km in engagement["key_moments"][:10]:
            console.print(f"  {escape(str(km))}")
    if engagement.get("heatmap"):
        console.print(f"\n[dim]Heatmap: {len(engagement['heatmap'])} segments[/]")


@app.command()
def download(
    url: str,
    audio: bool = typer.Option(False, "--audio", help="Audio only."),
    video: bool = typer.Option(False, "--video", help="Video (default if neither flag given)."),
    sponsorblock: bool = typer.Option(
        False, "--sponsorblock", help="Cut sponsor/self-promo/intro/outro segments."
    ),
    sponsorblock_action: str = typer.Option(
        "remove",
        "--sponsorblock-action",
        help="'remove' (cut out) or 'mark' (add as chapters) — --sponsorblock mode only.",
    ),
    sponsorblock_categories: str = typer.Option(
        None,
        "--sponsorblock-categories",
        help="Comma-separated categories (default: sponsor,selfpromo,intro,outro) "
        "— --sponsorblock mode only.",
    ),
    quality: str = typer.Option("best", "--quality", help="Video quality (video mode only)."),
    format: str = typer.Option("mp3", "--format", help="Audio format (audio mode only)."),
    bitrate: str = typer.Option(
        "best", "--bitrate", help="Audio bitrate: best|320k|256k|192k|128k (audio mode only)."
    ),
    prefer_yt_dlp: bool = typer.Option(
        False, "--prefer-yt-dlp", help="Force the yt-dlp backend instead of pytubefix."
    ),
    dest: Path = typer.Option(None, "--dest", "-o", help="Output dir/path."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Download a video or its audio."""
    settings = Settings.load(env_file)
    out = str(dest) if dest else str(settings.download_dir)
    toolkit = _toolkit()

    if sponsorblock:
        ignored = []
        if audio:
            ignored.append("--audio")
        if quality != "best":
            ignored.append("--quality")
        if format != "mp3":
            ignored.append("--format")
        if bitrate != "best":
            ignored.append("--bitrate")
        if prefer_yt_dlp:
            ignored.append("--prefer-yt-dlp")
        if ignored:
            console.print(
                f"[yellow]{', '.join(ignored)} ignored in --sponsorblock "
                "mode — it always cuts segments from a video download.[/]"
            )
        categories = (
            [c.strip() for c in sponsorblock_categories.split(",") if c.strip()]
            if sponsorblock_categories
            else None
        )
        with _handle():
            path = toolkit.download.with_sponsorblock(
                url, output_path=out, action=sponsorblock_action, categories=categories
            )
    elif audio and not video:
        with _handle():
            path = toolkit.download.audio(
                url,
                format=format,
                output_path=out,
                bitrate=bitrate,
                prefer_yt_dlp=prefer_yt_dlp,
            )
    else:
        with _handle():
            path = toolkit.download.video(
                url, quality=quality, output_path=out, prefer_yt_dlp=prefer_yt_dlp
            )

    console.print(f"[green]✓ {escape(str(path))}[/]")


@app.command()
def comments(
    url: str,
    limit: int = typer.Option(20, "--limit", "-n"),
    order: str = typer.Option("relevance", "--order", help="relevance|time"),
    search: str = typer.Option(None, "--search", help="Search within this video's comments."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Get comments from a video.

    Unlike search/info/transcript/analyze/download, this always needs
    YOUTUBE_API_KEY — youtube-toolkit's comments path has no no-API fallback.
    """
    settings = Settings.load(env_file)
    if not settings.api_key:
        console.print(
            "[yellow]`comments` needs YOUTUBE_API_KEY (a Data API key from "
            "Google Cloud Console) — unlike the other commands, there's no "
            "no-API fallback for comments in youtube-toolkit.[/]"
        )
        raise typer.Exit(1)

    if search:
        with _handle():
            matches = _toolkit().get.comments.search(url, search, limit=limit)
        console.print(render.comments_search_table(search, matches))
        return

    with _handle():
        result = _toolkit().get.comments(url, limit=limit, order=order)
    if result.error:
        # Already redacted upstream (no key leakage) — still escape, since
        # it's free-form text that could contain markup-like characters.
        console.print(f"[red]{escape(result.error)}[/]")
        raise typer.Exit(1)
    console.print(render.comments_table(result, limit))


_CHANNEL_SORT = {"newest", "oldest", "popular"}


@app.command()
def channel(
    handle: str,
    videos: bool = typer.Option(False, "--videos", help="List the channel's videos."),
    shorts: bool = typer.Option(False, "--shorts", help="List the channel's shorts."),
    streams: bool = typer.Option(False, "--streams", help="List the channel's live streams."),
    limit: int = typer.Option(20, "--limit", "-n"),
    sort: str = typer.Option(
        "newest", "--sort",
        help="newest|oldest|popular (applies to --videos/--shorts/--streams).",
    ),
) -> None:
    """Get channel metadata, or list its videos/shorts/streams."""
    if sum([videos, shorts, streams]) > 1:
        console.print("[red]Choose only one of --videos/--shorts/--streams.[/]")
        raise typer.Exit(1)

    if (videos or shorts or streams) and sort not in _CHANNEL_SORT:
        console.print(
            f"[red]Invalid --sort '{escape(sort)}'. Choose one of: "
            "newest, oldest, popular.[/]"
        )
        raise typer.Exit(1)

    toolkit = _toolkit()

    if videos:
        with _handle():
            items = toolkit.get.channel.videos(handle, limit=limit, sort_by=sort)
        console.print(render.channel_items_table(handle, "videos", items))
    elif shorts:
        with _handle():
            items = toolkit.get.channel.shorts(handle, limit=limit, sort_by=sort)
        console.print(render.channel_items_table(handle, "shorts", items))
    elif streams:
        with _handle():
            items = toolkit.get.channel.streams(handle, limit=limit, sort_by=sort)
        console.print(render.channel_items_table(handle, "streams", items))
    else:
        with _handle():
            info = toolkit.get.channel(handle)
        console.print(render.channel_panel(info))


@app.command()
def playlist(
    url: str,
    videos: bool = typer.Option(False, "--videos", help="List the playlist's videos."),
    limit: int = typer.Option(20, "--limit", "-n"),
    download: bool = typer.Option(
        False, "--download", help="Batch-download every video in the playlist."
    ),
    type: str = typer.Option("audio", "--type", help="audio|video (--download mode only)."),
    format: str = typer.Option("mp3", "--format", help="Output format (--download mode only)."),
    dest: Path = typer.Option(None, "--dest", "-o", help="Output directory (--download mode)."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Get playlist metadata, list its videos, or batch-download it."""
    if sum([videos, download]) > 1:
        console.print("[red]Choose only one of --videos/--download.[/]")
        raise typer.Exit(1)

    toolkit = _toolkit()

    if download:
        settings = Settings.load(env_file)
        out = str(dest) if dest else str(settings.download_dir)
        with _handle():
            result = toolkit.download.playlist(url, type=type, format=format, output_path=out)
        console.print(render.playlist_download_summary(result))
        return

    if videos:
        with _handle():
            # use_scrapetube=True → real video detail (title/views/…) in one
            # scrape; degrades to {url, video_id} dicts if scrapetube is absent.
            items = toolkit.get.playlist.videos(url, limit=limit, use_scrapetube=True)
        console.print(render.playlist_videos_table(url, items))
        return

    with _handle():
        info = toolkit.get.playlist(url)
    console.print(render.playlist_panel(info))


@app.command()
def captions(
    url: str,
    lang: str = typer.Option("en", "--lang"),
    format: str = typer.Option("srt", "--format", help="srt|vtt|txt|json"),
    dest: Path = typer.Option(None, "--dest", "-o", help="Output directory/path."),
) -> None:
    """Download a real subtitle/caption file (srt/vtt/txt/json).

    Distinct from `transcript`, which prints plain auto-generated-transcript
    text to the terminal rather than writing a subtitle file to disk.
    """
    out = str(dest) if dest else None
    with _handle():
        path = _toolkit().download.captions(url, lang=lang, format=format, output_path=out)
    console.print(f"[green]✓ {escape(str(path))}[/]")


@app.command()
def formats(url: str) -> None:
    """List available resolutions/codecs for a video — a pre-download planning aid."""
    with _handle():
        data = _toolkit().get.formats(url)
    console.print(render.formats_table(data))


@app.command()
def sponsorblock(url: str) -> None:
    """Preview SponsorBlock segments (sponsor/intro/outro/...) without downloading."""
    with _handle():
        segments = _toolkit().analyze.sponsorblock(url)
    console.print(render.sponsorblock_table(url, segments))


if __name__ == "__main__":
    app()
