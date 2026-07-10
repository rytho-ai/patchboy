"""Rendering helpers — turn youtube-toolkit's return shapes into rich output.

Kept separate from cli.py so the presentation logic (which fields matter,
how to truncate) doesn't clutter the command wiring.

Rich parses ``[tag]``-style markup in BOTH ``console.print`` strings AND
``Table`` cell/title content (verified — a title like "Cool Song [official
video]" silently loses the bracketed part). Every untrusted upstream string
(titles, channel names, comment text, …) is therefore run through
``rich.markup.escape`` before it reaches a cell or a table title.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

console = Console()


def _s(value: Any) -> str:
    """str() an upstream value and neutralize any Rich markup in it, so a
    bracketed substring in a title/name/comment can't be silently eaten."""
    if value is None:
        return ""
    return escape(str(value))


def search_table(query: str, results: list[dict[str, Any]]) -> Table:
    """Render the no-API search path's dict shape (scrapetube-backed).

    Keys per result: video_id, title, channel, views, views_text, duration,
    duration_text, published, thumbnail, description, url.
    """
    table = Table(title=f'YouTube: "{_s(query)}"  ({len(results)} shown)')
    table.add_column("Title", max_width=48, overflow="fold")
    table.add_column("Channel", max_width=20, overflow="fold")
    table.add_column("Duration")
    table.add_column("Views")
    table.add_column("Published")
    table.add_column("video_id", style="dim")
    for r in results:
        table.add_row(
            _s(r.get("title", "")),
            _s(r.get("channel", "")),
            _s(r.get("duration_text", "")),
            _s(r.get("views_text", "") or r.get("views", "")),
            _s(r.get("published", "")),
            _s(r.get("video_id", "")),
        )
    return table


def _fmt_count(value: Any) -> str:
    """Comma-format a count that *should* be an int but, per an observed
    pytubefix quirk (`.likes`/`.views` can come back as numeric strings,
    uncast by youtube-toolkit), sometimes isn't. Never crash rendering over
    an upstream type looseness — just fall back to a plain string.
    """
    if value in (None, ""):
        return ""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _s(value)


def video_info_panel(info: Any) -> Table:
    """Render a VideoInfo dataclass (from toolkit.get.video()) as a key/value table."""
    table = Table(title=_s(info.title), show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")
    rows = [
        ("author", _s(info.author)),
        ("duration", f"{info.duration}s"),
        ("views", _fmt_count(info.views)),
        ("published", _s(info.published_date or "")),
        ("video_id", _s(info.video_id)),
        ("url", _s(info.url)),
    ]
    if info.like_count is not None:
        rows.append(("likes", _fmt_count(info.like_count)))
    if info.comment_count is not None:
        rows.append(("comments", _fmt_count(info.comment_count)))
    if info.category:
        rows.append(("category", _s(info.category)))
    if info.tags:
        rows.append(("tags", _s(", ".join(info.tags[:8]))))
    for field, value in rows:
        table.add_row(field, str(value))
    return table


def comments_table(result: Any, limit: int) -> Table:
    """Render a CommentResult (from toolkit.get.comments()) as a table."""
    total = result.total_results if result.total_results is not None else 0
    table = Table(title=f"Comments ({total:,} total, showing {limit})")
    table.add_column("Author", max_width=20, overflow="fold")
    table.add_column("Comment", max_width=60, overflow="fold")
    table.add_column("Likes", justify="right")
    for c in result.comments[:limit]:
        author = c.author.display_name if c.author else ""
        likes = c.metrics.like_count if c.metrics else 0
        table.add_row(_s(author), _s(c.text), f"{likes:,}" if likes else "")
    return table


def comments_search_table(query: str, matches: list[Any]) -> Table:
    """Render toolkit.get.comments.search()'s result — a bare list of Comment
    objects (unlike toolkit.get.comments(), which wraps them in CommentResult)."""
    table = Table(title=f'Comments matching "{_s(query)}" ({len(matches)} shown)')
    table.add_column("Author", max_width=20, overflow="fold")
    table.add_column("Comment", max_width=60, overflow="fold")
    table.add_column("Likes", justify="right")
    for c in matches:
        author = c.author.display_name if getattr(c, "author", None) else ""
        likes = c.metrics.like_count if getattr(c, "metrics", None) else 0
        table.add_row(_s(author), _s(getattr(c, "text", c)), f"{likes:,}" if likes else "")
    return table


def channel_panel(info: dict[str, Any]) -> Table:
    """Render toolkit.get.channel()'s metadata dict as a key/value table."""
    table = Table(title=_s(info.get("channel_name", "")), show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")
    rows = [
        ("channel_id", _s(info.get("channel_id", ""))),
        ("url", _s(info.get("channel_url", ""))),
        ("vanity_url", _s(info.get("vanity_url", ""))),
        ("videos", _fmt_count(info.get("video_count"))),
        ("total views", _fmt_count(info.get("total_views"))),
        ("last_updated", _s(info.get("last_updated", ""))),
    ]
    description = info.get("description")
    if description:
        rows.append(("description", _s(str(description)[:300])))
    for field, value in rows:
        if value:
            table.add_row(field, value)
    return table


def channel_items_table(handle: str, kind: str, items: list[dict[str, Any]]) -> Table:
    """Render toolkit.get.channel.videos/shorts/streams()'s list of dicts."""
    table = Table(title=f"{_s(handle)} — {kind} ({len(items)} shown)")
    table.add_column("Title", max_width=48, overflow="fold")
    table.add_column("Length")
    table.add_column("Views")
    table.add_column("Published")
    table.add_column("video_id", style="dim")
    for item in items:
        table.add_row(
            _s(item.get("title", "")),
            _s(item.get("length", "")),
            _fmt_count(item.get("views")),
            _s(item.get("publish_date", "")),
            _s(item.get("video_id", "")),
        )
    return table


def playlist_panel(info: dict[str, Any]) -> Table:
    """Render toolkit.get.playlist()'s metadata dict as a key/value table."""
    table = Table(title=_s(info.get("title", "")), show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")
    rows = [
        ("playlist_id", _s(info.get("playlist_id", ""))),
        ("owner", _s(info.get("owner", ""))),
        ("videos", _fmt_count(info.get("video_count"))),
        ("views", _fmt_count(info.get("views"))),
        ("last_updated", _s(info.get("last_updated", ""))),
    ]
    description = info.get("description")
    if description:
        rows.append(("description", _s(str(description)[:300])))
    for field, value in rows:
        if value:
            table.add_row(field, value)
    return table


def playlist_videos_table(url: str, items: list[Any]) -> Table:
    """Render toolkit.get.playlist.videos()'s result.

    Without ``use_scrapetube=True`` (not exposed as a CLI flag), this falls
    back to a bare list of URL strings rather than video-detail dicts — handle
    both shapes so the render doesn't crash on the plain-URL case.
    """
    table = Table(title=f"Playlist videos ({len(items)} shown)")
    if items and isinstance(items[0], dict):
        table.add_column("Title", max_width=48, overflow="fold")
        table.add_column("Channel", max_width=20, overflow="fold")
        table.add_column("Views")
        table.add_column("video_id", style="dim")
        for item in items:
            table.add_row(
                _s(item.get("title", "")),
                _s(item.get("channel", item.get("author", ""))),
                _fmt_count(item.get("views")),
                _s(item.get("video_id", "")),
            )
    else:
        table.add_column("URL")
        for item in items:
            table.add_row(_s(item))
    return table


def playlist_download_summary(result: dict[str, Any]) -> Table:
    """Render toolkit.download.playlist()'s result dict."""
    table = Table(title="Playlist download", show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")
    if not result.get("success"):
        table.add_row("success", "False")
        if result.get("error"):
            table.add_row("error", _s(result["error"]))
        return table

    table.add_row("success", "True")
    table.add_row("playlist_dir", _s(result.get("playlist_dir", "")))
    table.add_row("metadata_path", _s(result.get("metadata_path", "")))
    summary = (result.get("metadata") or {}).get("download_summary", {})
    if summary:
        table.add_row("total videos", _fmt_count(summary.get("total_videos")))
        table.add_row("succeeded", _fmt_count(summary.get("successful_downloads")))
        table.add_row("failed", _fmt_count(summary.get("failed_downloads")))
    return table


def formats_table(data: dict[str, Any]) -> Table:
    """Render toolkit.get.formats()'s {'video_formats', 'audio_formats',
    'best_video', 'best_audio'} dict as a resolution/bitrate x itag table."""
    table = Table(
        title="Available formats"
        f" (best video: {_s(data.get('best_video'))}, best audio: {_s(data.get('best_audio'))})"
    )
    table.add_column("Kind")
    table.add_column("Resolution/Bitrate")
    table.add_column("itag")
    table.add_column("Filesize", justify="right")
    table.add_column("mime_type", max_width=24, overflow="fold")

    for res, streams in (data.get("video_formats") or {}).items():
        for stream in streams:
            table.add_row(
                "video",
                _s(res),
                _s(stream.get("itag", "")),
                _fmt_count(stream.get("filesize")),
                _s(stream.get("mime_type", "")),
            )
    for abr, streams in (data.get("audio_formats") or {}).items():
        for stream in streams:
            table.add_row(
                "audio",
                _s(abr),
                _s(stream.get("itag", "")),
                _fmt_count(stream.get("filesize")),
                _s(stream.get("mime_type", "")),
            )
    return table


def sponsorblock_table(url: str, segments: list[dict[str, Any]]) -> Table:
    """Render toolkit.analyze.sponsorblock()'s list of segment dicts."""
    table = Table(title=f"SponsorBlock segments ({len(segments)} found)")
    table.add_column("Category")
    table.add_column("Description", max_width=32, overflow="fold")
    table.add_column("Start", justify="right")
    table.add_column("End", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Votes", justify="right")
    for seg in segments:
        table.add_row(
            _s(seg.get("category", "")),
            _s(seg.get("description", "")),
            _s(seg.get("start_time", "")),
            _s(seg.get("end_time", "")),
            _s(seg.get("duration", "")),
            _fmt_count(seg.get("votes")),
        )
    return table


def search_filtered_table(query: str, result: dict[str, Any]) -> Table:
    """Render toolkit.search.with_filters()'s {'videos', 'shorts', 'channels',
    'playlists', 'completion_suggestions'} dict — flatten 'videos' (the
    common case) into the same shape as the default search_table."""
    videos = result.get("videos") or []
    table = Table(title=f'YouTube (filtered): "{_s(query)}"  ({len(videos)} shown)')
    table.add_column("Title", max_width=48, overflow="fold")
    table.add_column("Channel", max_width=20, overflow="fold")
    table.add_column("Duration")
    table.add_column("Views")
    table.add_column("video_id", style="dim")
    for r in videos:
        table.add_row(
            _s(r.get("title", "")),
            _s(r.get("channel", "")),
            _s(r.get("duration_text", r.get("duration", ""))),
            _s(r.get("views_text", "") or r.get("views", "")),
            _s(r.get("video_id", "")),
        )
    return table
