"""Stable vocabulary — hand-authored types the CLI and render speak in.

NOT a raw mirror of Spotify's JSON. A field rename or reshape upstream (Spotify
has done several — see the Feb 2026 changes) is absorbed by a one-line map in
the caged client.py, invisible here. Keep these minimal: only the fields the CLI
actually renders. `uri` is carried everywhere because the write endpoints
(save/remove and playlist mutations) take Spotify URIs, not bare ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Artist:
    id: str
    name: str
    uri: str
    genres: tuple[str, ...] = ()


@dataclass(frozen=True)
class Album:
    id: str
    name: str
    uri: str
    artists: tuple[str, ...] = ()  # artist names, flattened for display
    release_date: str | None = None
    total_tracks: int | None = None


@dataclass(frozen=True)
class Track:
    id: str
    name: str
    uri: str
    artists: tuple[str, ...] = ()  # artist names, flattened for display
    album: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None  # the album's release date ("YYYY", "YYYY-MM-DD")


@dataclass(frozen=True)
class SavedTrack:
    """A track *as a member of a collection* (liked songs, or a playlist) — pairs
    the catalog Track with the membership timestamp the collection records. Kept
    separate from Track on purpose: `added_at` is knowledge about the membership,
    not about the track, so it doesn't belong as an optional field on Track."""

    track: Track
    added_at: str | None = None  # ISO8601, e.g. "2026-07-09T15:03:40Z"


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    uri: str
    owner: str | None = None
    total_tracks: int | None = None
    public: bool | None = None
    description: str | None = None


@dataclass(frozen=True)
class SearchResults:
    tracks: tuple[Track, ...] = ()
    albums: tuple[Album, ...] = ()
    artists: tuple[Artist, ...] = ()
    playlists: tuple[Playlist, ...] = ()


@dataclass(frozen=True)
class PlaylistDetail:
    playlist: Playlist
    # Feb 2026: full items are only returned for playlists you own/collaborate
    # on; for others `items` is absent (metadata-only). `items_visible` records
    # which case this is so the CLI can say so instead of showing an empty list.
    items_visible: bool
    items: tuple[SavedTrack, ...] = field(default_factory=tuple)
