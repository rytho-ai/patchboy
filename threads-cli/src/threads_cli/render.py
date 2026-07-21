"""Presentation only — rich tables/panels. No httpx, no auth, no client calls.

Every upstream string (post text, usernames, bios) is run through
`rich.markup.escape` before it reaches a cell or title — same discipline as
sibling spotify-cli/youtube-cli's render.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .models import Insights, Post, Profile, Reply


def _short(text: str | None, n: int = 80) -> str:
    if not text:
        return "-"
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def profile_panel(p: Profile) -> Panel:
    lines = [f"[bold]@{escape(p.username)}[/]"]
    if p.name:
        lines.append(escape(p.name))
    if p.biography:
        lines.append(escape(p.biography))
    lines.append(f"[dim]id: {p.id}[/]")
    return Panel("\n".join(lines), title="Profile", border_style="cyan")


def posts_table(posts: Sequence[Post], title: str = "Posts") -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("Text", overflow="fold")
    t.add_column("Type")
    t.add_column("Timestamp", style="dim")
    t.add_column("id", style="dim")
    for post in posts:
        t.add_row(escape(_short(post.text)), post.media_type or "-", post.timestamp or "-", post.id)
    return t


def replies_table(replies: Sequence[Reply], title: str = "Replies") -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("From")
    t.add_column("Text", overflow="fold")
    t.add_column("Timestamp", style="dim")
    t.add_column("id", style="dim")
    for r in replies:
        t.add_row(escape(r.username or "-"), escape(_short(r.text)), r.timestamp or "-", r.id)
    return t


def insights_table(insights: Insights, title: str = "Insights") -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for m in insights.metrics:
        label = escape(m.title or m.name)
        t.add_row(label, "-" if m.value is None else str(m.value))
    return t
