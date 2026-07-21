"""Stable vocabulary — hand-authored types the CLI and render speak in.

NOT a raw mirror of Apple's JSON (every real field is nested under
`{id, type, attributes:{…}}`; the flattening happens in the caged client). Keep
these minimal: only the fields the CLI renders. Apple calls a track a "Song".

`type` is carried on every catalog item because Apple's write endpoints
(add-to-library, playlist-add) take `{id, type}` pairs, not bare ids or URIs —
that (id, type) couple is Apple's equivalent of Spotify's `spotify:track:…` URI.
Catalog items are type "songs"/"albums"/…; a user's own library items come back
as "library-songs"/"library-playlists" and carry different ids, so the type
travels with the id rather than being assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Artist:
    id: str
    type: str
    name: str
    genres: tuple[str, ...] = ()
    url: str | None = None


@dataclass(frozen=True)
class Album:
    id: str
    type: str
    name: str
    artists: tuple[str, ...] = ()  # artist names, flattened for display
    release_date: str | None = None
    track_count: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class Song:
    id: str
    type: str
    name: str
    artists: tuple[str, ...] = ()  # artist names, flattened for display
    album: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class LibrarySong:
    """A song *as a member of a collection* (your library, or a playlist) — pairs
    the catalog-shaped Song with the membership timestamp the collection records.
    Kept separate from Song on purpose: `added_at` is knowledge about the
    membership, not about the song, so it isn't an optional field on Song."""

    song: Song
    added_at: str | None = None  # ISO8601 from `attributes.dateAdded`


@dataclass(frozen=True)
class Playlist:
    id: str
    type: str
    name: str
    curator: str | None = None  # attributes.curatorName — the "owner" analog
    description: str | None = None
    track_count: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class SearchResults:
    songs: tuple[Song, ...] = ()
    albums: tuple[Album, ...] = ()
    artists: tuple[Artist, ...] = ()
    playlists: tuple[Playlist, ...] = ()


@dataclass(frozen=True)
class PlaylistDetail:
    playlist: Playlist
    # Catalog (editorial) playlists always return their tracks. A user's own
    # library playlist returns them too, but a foreign library playlist can't be
    # fetched at all. `items_visible` records whether we have the track list so
    # the CLI can say so instead of showing an empty table.
    items_visible: bool
    items: tuple[LibrarySong, ...] = field(default_factory=tuple)
