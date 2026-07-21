"""Presentation only — rich tables/panels. No httpx, no auth, no client calls.

Every upstream string (song/album/playlist names, curators) is run through
`rich.markup.escape` before it reaches a cell or title: a name like
"Cool Song [Remix]" would otherwise have its bracketed part silently eaten as
rich markup. Same discipline as sibling spotify-cli's render.py.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.markup import escape
from rich.table import Table

from .models import Album, Artist, LibrarySong, Playlist, PlaylistDetail, SearchResults, Song


def _dur(ms: int | None) -> str:
    if not ms:
        return "-"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _artists(names: tuple[str, ...]) -> str:
    return escape(", ".join(n for n in names if n)) or "-"


def songs_table(songs: tuple[Song, ...], title: str = "Songs") -> Table:
    t = Table(title=title, show_lines=False)
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Album", overflow="fold")
    t.add_column("Dur", justify="right")
    t.add_column("id", style="dim")
    for s in songs:
        t.add_row(escape(s.name), _artists(s.artists), escape(s.album or "-"), _dur(s.duration_ms), s.id)
    return t


def library_songs_table(items: Sequence[LibrarySong], title: str = "Library songs") -> Table:
    """Like songs_table, but for collection members — adds an Added (date) column,
    the membership timestamp `--since/--until` filter on."""
    t = Table(title=title, show_lines=False)
    t.add_column("Added", style="dim")
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Album", overflow="fold")
    t.add_column("Dur", justify="right")
    t.add_column("id", style="dim")
    for it in items:
        s = it.song
        added = (it.added_at or "")[:10] or "-"
        t.add_row(added, escape(s.name), _artists(s.artists), escape(s.album or "-"), _dur(s.duration_ms), s.id)
    return t


def albums_table(albums: tuple[Album, ...], title: str = "Albums") -> Table:
    t = Table(title=title)
    t.add_column("Name", overflow="fold")
    t.add_column("Artists")
    t.add_column("Released")
    t.add_column("#", justify="right")
    t.add_column("id", style="dim")
    for a in albums:
        t.add_row(escape(a.name), _artists(a.artists), a.release_date or "-", str(a.track_count or "-"), a.id)
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
    t.add_column("Curator")
    t.add_column("Tracks", justify="right")
    t.add_column("id", style="dim")
    for p in playlists:
        t.add_row(escape(p.name), escape(p.curator or "-"), str(p.track_count or "-"), p.id)
    return t


def search_tables(results: SearchResults) -> list[Table]:
    out: list[Table] = []
    if results.songs:
        out.append(songs_table(results.songs))
    if results.albums:
        out.append(albums_table(results.albums))
    if results.artists:
        out.append(artists_table(results.artists))
    if results.playlists:
        out.append(playlists_table(results.playlists))
    return out


def playlist_detail_tables(detail: PlaylistDetail, items: Sequence[LibrarySong] | None = None) -> list[Table]:
    """Render a playlist. `items` overrides `detail.items` so a command can pass a
    filtered subset without rebuilding the frozen PlaylistDetail."""
    out = [playlists_table((detail.playlist,), title="Playlist")]
    if detail.items_visible:
        out.append(library_songs_table(detail.items if items is None else items, title="Tracks"))
    return out
