"""Presentation only — rich tables/panels. No httpx, no auth, no client calls.

Every upstream string (track/album/playlist names, owners) is run through
`rich.markup.escape` before it reaches a cell or title: a name like
"Cool Song [Remix]" would otherwise have its bracketed part silently eaten as
rich markup. Same discipline as sibling youtube-cli's render.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.markup import escape
from rich.table import Table

from .models import Album, Artist, Playlist, PlaylistDetail, SavedTrack, SearchResults, Track


def _dur(ms: int | None) -> str:
    if not ms:
        return "-"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _artists(names: tuple[str, ...]) -> str:
    return escape(", ".join(n for n in names if n)) or "-"


def tracks_table(tracks: tuple[Track, ...], title: str = "Tracks") -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Album", overflow="fold")
    t.add_column("Dur", justify="right")
    t.add_column("id", style="dim")
    for tr in tracks:
        t.add_row(escape(tr.name), _artists(tr.artists), escape(tr.album or "-"), _dur(tr.duration_ms), tr.id)
    return t


def saved_tracks_table(items: Sequence[SavedTrack], title: str = "Saved tracks") -> Table:
    """Like tracks_table, but for collection members — adds an Added (date) column,
    the membership timestamp `--since/--until` filter on."""
    t = Table(title=title, show_lines=False)
    t.add_column("Added", style="dim")
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Album", overflow="fold")
    t.add_column("Dur", justify="right")
    t.add_column("id", style="dim")
    for s in items:
        tr = s.track
        added = (s.added_at or "")[:10] or "-"
        t.add_row(added, escape(tr.name), _artists(tr.artists), escape(tr.album or "-"), _dur(tr.duration_ms), tr.id)
    return t


def albums_table(albums: tuple[Album, ...], title: str = "Albums") -> Table:
    t = Table(title=title)
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Released")
    t.add_column("#", justify="right")
    t.add_column("id", style="dim")
    for a in albums:
        t.add_row(escape(a.name), _artists(a.artists), a.release_date or "-", str(a.total_tracks or "-"), a.id)
    return t


def artists_table(artists: tuple[Artist, ...], title: str = "Artists") -> Table:
    t = Table(title=title)
    t.add_column("Name", overflow="fold")
    t.add_column("Genres", overflow="fold")
    t.add_column("id", style="dim")
    for a in artists:
        t.add_row(escape(a.name), escape(", ".join(a.genres)) or "-", a.id)
    return t


def playlists_table(playlists: tuple[Playlist, ...], title: str = "Playlists") -> Table:
    t = Table(title=title)
    t.add_column("Name", overflow="fold")
    t.add_column("Owner")
    t.add_column("Tracks", justify="right")
    t.add_column("Public")
    t.add_column("id", style="dim")
    for p in playlists:
        pub = "-" if p.public is None else ("yes" if p.public else "no")
        t.add_row(escape(p.name), escape(p.owner or "-"), str(p.total_tracks or "-"), pub, p.id)
    return t


def search_tables(results: SearchResults) -> list[Table]:
    out: list[Table] = []
    if results.tracks:
        out.append(tracks_table(results.tracks))
    if results.albums:
        out.append(albums_table(results.albums))
    if results.artists:
        out.append(artists_table(results.artists))
    if results.playlists:
        out.append(playlists_table(results.playlists))
    return out


def playlist_detail_tables(detail: PlaylistDetail, items: Sequence[SavedTrack] | None = None) -> list[Table]:
    """Render a playlist. `items` overrides `detail.items` so a command can pass a
    filtered subset without rebuilding the frozen PlaylistDetail."""
    out = [playlists_table((detail.playlist,), title="Playlist")]
    if detail.items_visible:
        out.append(saved_tracks_table(detail.items if items is None else items, title="Items"))
    return out
