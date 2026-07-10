"""Rendering helpers — turn youtube-toolkit's return shapes into rich output.

Kept separate from cli.py so the presentation logic (which fields matter,
how to truncate) doesn't clutter the command wiring.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def search_table(query: str, results: list[dict[str, Any]]) -> Table:
    """Render the no-API search path's dict shape (scrapetube-backed).

    Keys per result: video_id, title, channel, views, views_text, duration,
    duration_text, published, thumbnail, description, url.
    """
    table = Table(title=f'YouTube: "{query}"  ({len(results)} shown)')
    table.add_column("Title", max_width=48, overflow="fold")
    table.add_column("Channel", max_width=20, overflow="fold")
    table.add_column("Duration")
    table.add_column("Views")
    table.add_column("Published")
    table.add_column("video_id", style="dim")
    for r in results:
        table.add_row(
            r.get("title", ""),
            r.get("channel", ""),
            r.get("duration_text", ""),
            r.get("views_text", "") or str(r.get("views", "")),
            r.get("published", ""),
            r.get("video_id", ""),
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
        return str(value)


def video_info_panel(info: Any) -> Table:
    """Render a VideoInfo dataclass (from toolkit.get.video()) as a key/value table."""
    table = Table(title=info.title, show_header=False)
    table.add_column("field", style="dim")
    table.add_column("value")
    rows = [
        ("author", info.author),
        ("duration", f"{info.duration}s"),
        ("views", _fmt_count(info.views)),
        ("published", info.published_date or ""),
        ("video_id", info.video_id),
        ("url", info.url),
    ]
    if info.like_count is not None:
        rows.append(("likes", _fmt_count(info.like_count)))
    if info.comment_count is not None:
        rows.append(("comments", _fmt_count(info.comment_count)))
    if info.category:
        rows.append(("category", info.category))
    if info.tags:
        rows.append(("tags", ", ".join(info.tags[:8])))
    for field, value in rows:
        table.add_row(field, str(value))
    return table


def comments_table(result: Any, limit: int) -> Table:
    """Render a CommentResult (from toolkit.get.comments()) as a table."""
    table = Table(title=f"Comments ({result.total_results:,} total, showing {limit})")
    table.add_column("Author", max_width=20, overflow="fold")
    table.add_column("Comment", max_width=60, overflow="fold")
    table.add_column("Likes", justify="right")
    for c in result.comments[:limit]:
        author = c.author.display_name if c.author else ""
        likes = c.metrics.like_count if c.metrics else 0
        table.add_row(author, c.text, f"{likes:,}" if likes else "")
    return table
