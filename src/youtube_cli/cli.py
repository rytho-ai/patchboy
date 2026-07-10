"""youtube — thin CLI shell over youtube-toolkit, meant for a coding agent to drive.

Every command is a few lines: parse args, call youtube_toolkit, render. No
extraction/fallback logic lives here — that's youtube-toolkit's job. Unlike
splice-cli, there's no login/session/budget layer: everything here is public,
unauthenticated access (search's Data API path needs a key, not a login).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from youtube_toolkit import YouTubeToolkit

from . import render
from .config import Settings

app = typer.Typer(add_completion=False, help="Thin CLI over youtube-toolkit.")
console = Console()


def _toolkit() -> YouTubeToolkit:
    return YouTubeToolkit()


@app.command()
def search(
    query: str,
    limit: int = typer.Option(20, "--limit", "-n"),
    use_api: bool = typer.Option(
        False, "--api", help="Use the YouTube Data API (needs YOUTUBE_API_KEY, costs quota)."
    ),
    sort_by: str = typer.Option("relevance", "--sort", help="relevance|date|views|rating"),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Search for videos. Default path needs no API key and costs no quota."""
    settings = Settings.load(env_file)
    if use_api and not settings.api_key:
        console.print(
            "[yellow]--api needs YOUTUBE_API_KEY set (a Data API key from "
            "Google Cloud Console). Falling back to the no-API search "
            "instead, or set the key and retry.[/]"
        )
        use_api = False

    results = _toolkit().search.videos(query, limit=limit, use_api=use_api, sort_by=sort_by)
    console.print(render.search_table(query, results))


@app.command()
def info(
    url: str,
    chapters: bool = typer.Option(False, "--chapters", help="Include chapter list."),
) -> None:
    """Get a video's core metadata."""
    include = ["chapters"] if chapters else None
    video = _toolkit().get.video(url, include=include)
    console.print(render.video_info_panel(video))
    if chapters and video.chapters:
        console.print(f"[dim]{len(video.chapters)} chapters:[/]")
        for ch in video.chapters:
            console.print(f"  {ch.get('formatted_start', '')}  {ch.get('title', '')}")


@app.command()
def transcript(
    url: str,
    lang: str = typer.Option("en", "--lang"),
) -> None:
    """Get a video's auto-generated transcript."""
    text = _toolkit().get.transcript(url, lang=lang)
    if not text:
        console.print("[yellow]No transcript available for this video/language.[/]")
        raise typer.Exit(1)
    console.print(text)


@app.command()
def analyze(url: str) -> None:
    """Comprehensive metadata (50+ fields) + engagement (heatmap/key moments)."""
    toolkit = _toolkit()
    meta = toolkit.analyze.metadata(url)
    engagement = toolkit.analyze.engagement(url)

    console.print(f"[bold]{meta.get('title', url)}[/]")
    skip = {"title"}
    for key, value in meta.items():
        if key in skip or value in (None, "", [], {}):
            continue
        console.print(f"  [dim]{key}:[/] {value}")

    if engagement.get("key_moments"):
        console.print(f"\n[bold]Key moments[/] ({len(engagement['key_moments'])}):")
        for km in engagement["key_moments"][:10]:
            console.print(f"  {km}")
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
    quality: str = typer.Option("best", "--quality", help="Video quality (video mode only)."),
    format: str = typer.Option("mp3", "--format", help="Audio format (audio mode only)."),
    dest: Path = typer.Option(None, "--dest", "-o", help="Output dir/path."),
    env_file: Path = typer.Option(None, "--env"),
) -> None:
    """Download a video or its audio."""
    settings = Settings.load(env_file)
    out = str(dest) if dest else str(settings.download_dir)
    toolkit = _toolkit()

    if sponsorblock:
        path = toolkit.download.with_sponsorblock(url, output_path=out)
    elif audio and not video:
        path = toolkit.download.audio(url, format=format, output_path=out)
    else:
        path = toolkit.download.video(url, quality=quality, output_path=out)

    console.print(f"[green]✓ {path}[/]")


@app.command()
def comments(
    url: str,
    limit: int = typer.Option(20, "--limit", "-n"),
    order: str = typer.Option("relevance", "--order", help="relevance|time"),
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
    result = _toolkit().get.comments(url, limit=limit, order=order)
    console.print(render.comments_table(result, limit))


if __name__ == "__main__":
    app()
