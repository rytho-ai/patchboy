"""CAGED — the only module that knows Apple Music's wire shape.

Everything above this speaks models.py's vocabulary. Every real Apple resource is
`{id, type, attributes:{…}}` and every collection is `{data:[…]}` or nested under
`results`/`relationships`; that structure is flattened here and nowhere else. An
Apple field rename must be fixable in this file without cli.py or models.py moving.

Two header sets, from auth.AppleMusicSession: `catalog_headers` (developer token
only — catalog works logged-out) and `user_headers` (developer token +
media-user-token — required for `/me/*`, and it raises if you never logged in).
`_request(..., user=True)` picks the user set; a `/me/*` call with no
media-user-token therefore fails fast as an AppleMusicAuthError → exit-2 handoff.

Auth-failure mapping: 401 (any) or 403 (on a user call) → session dead →
AppleMusicAuthError (exit-2). 429 → retryable RateLimited with Retry-After.
Other 4xx/5xx → AppleApiError (exit 1).

CAVEAT: several write shapes below reflect Apple's library API, which is
under-documented and has historically shifted (especially removal). They carry
`# verify-live`; confirm each against a real logged-in account before trusting.
"""

from __future__ import annotations

import httpx

from .auth import AppleMusicAuthError, AppleMusicSession
from .models import (
    Album,
    Artist,
    LibrarySong,
    Playlist,
    PlaylistDetail,
    SearchResults,
    Song,
)

_BASE = "https://api.music.apple.com/v1"
# The web player's internal backend host. Almost everything works on the public
# _BASE alias, but ONE operation doesn't: DELETE-from-library. Verified live
# 2026-07-21 — `DELETE /me/library/songs/{id}` 401s (empty body) on _BASE yet
# 204s on amp-api with the identical borrowed token. So library removal, and only
# that, is routed here. Don't move other calls onto it without re-verifying.
_AMP_BASE = "https://amp-api.music.apple.com/v1"

# User-friendly singular → Apple's plural resource type (its `types=` param and
# the keys under `results`). Caged so cli.py never has to know the plural forms.
_SEARCH_TYPES = {"song": "songs", "album": "albums", "artist": "artists", "playlist": "playlists"}
# Apple caps catalog search at 25 per type.
_SEARCH_LIMIT_MAX = 25


class RateLimited(RuntimeError):
    """Apple returned 429. `retry_after` is seconds to wait (may be None)."""

    def __init__(self, retry_after: int | None) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Apple Music rate limit hit — retry after {retry_after}s"
            if retry_after is not None
            else "Apple Music rate limit hit"
        )


class AppleApiError(RuntimeError):
    """An ordinary API failure (not-found, bad request) — exit 1, no handoff."""


class AppleSubscriptionError(AppleApiError):
    """`/me/*` reached a valid, authed account that lacks an ACTIVE Apple Music
    subscription — Apple returns 400 'Insufficient Privileges' (CloudLibrary /
    ListeningHistory). Not an auth failure (login/storefront work), not fixable by
    re-login: the account must subscribe. Exit 1 with a clear message."""


class Client:
    """Thin authed wrapper. Construct with a session whose developer token is
    already fresh (cli._live_client re-scrapes it when `dev_token_expired`)."""

    def __init__(self, session: AppleMusicSession) -> None:
        self._session = session
        self._sf = session.storefront

    # ---- low-level ---------------------------------------------------------

    def _request(self, method: str, path: str, *, user: bool = False, base: str = _BASE, **kw) -> dict:
        headers = self._session.user_headers if user else self._session.catalog_headers
        resp = httpx.request(method, f"{base}{path}", headers=headers, timeout=15, **kw)
        if resp.status_code == 401 or (user and resp.status_code == 403):
            raise AppleMusicAuthError(
                "Apple Music rejected the request (developer token stale, or your "
                "media-user-token expired/insufficient). Run `applemusic login` again."
            )
        if resp.status_code == 429:
            ra = resp.headers.get("retry-after")
            raise RateLimited(int(ra) if ra and ra.isdigit() else None)
        if resp.status_code == 204:
            return {}
        if resp.status_code == 400 and "Insufficient Privileges" in resp.text:
            raise AppleSubscriptionError(
                "This needs an ACTIVE Apple Music subscription — the signed-in account's "
                "tier lacks the privilege (your login is fine; catalog commands work). "
                "Subscribe at music.apple.com, or stick to catalog commands."
            )
        if resp.status_code >= 400:
            raise AppleApiError(f"Apple Music API error (status {resp.status_code}): {resp.text}")
        return resp.json() if resp.content else {}

    def _get(self, path: str, *, user: bool = False, **kw) -> dict:
        return self._request("GET", path, user=user, **kw)

    def _catalog(self, path: str) -> str:
        return f"/catalog/{self._sf}{path}"

    # ---- catalog -----------------------------------------------------------

    def search(self, query: str, types: tuple[str, ...], limit: int = 25) -> SearchResults:
        wire_types = [_SEARCH_TYPES[t] for t in types if t in _SEARCH_TYPES]
        data = self._get(
            self._catalog("/search"),
            params={
                "term": query,
                "types": ",".join(wire_types),
                "limit": min(limit, _SEARCH_LIMIT_MAX),
            },
        )
        results = data.get("results", {})
        return SearchResults(
            songs=tuple(_song(o) for o in _data(results.get("songs"))),
            albums=tuple(_album(o) for o in _data(results.get("albums"))),
            artists=tuple(_artist(o) for o in _data(results.get("artists"))),
            playlists=tuple(_playlist(o) for o in _data(results.get("playlists"))),
        )

    def song(self, song_id: str) -> Song:
        return _song(_one(self._get(self._catalog(f"/songs/{song_id}"))))

    def album(self, album_id: str) -> Album:
        return _album(_one(self._get(self._catalog(f"/albums/{album_id}"))))

    def artist(self, artist_id: str) -> Artist:
        return _artist(_one(self._get(self._catalog(f"/artists/{artist_id}"))))

    def playlist(self, playlist_id: str) -> PlaylistDetail:
        # A catalog (editorial) playlist returns its tracks inline under
        # relationships.tracks.data. `include=tracks` makes that explicit.
        data = self._get(self._catalog(f"/playlists/{playlist_id}"), params={"include": "tracks"})
        obj = _one(data)
        pl = _playlist(obj)
        rows = (obj.get("relationships", {}).get("tracks") or {}).get("data")
        if rows is None:
            return PlaylistDetail(playlist=pl, items_visible=False)
        return PlaylistDetail(
            playlist=pl, items_visible=True, items=tuple(_library_song(r) for r in rows)
        )

    # ---- user library (needs media-user-token) -----------------------------

    def my_playlists(self, limit: int = 25) -> tuple[Playlist, ...]:
        data = self._get("/me/library/playlists", user=True, params={"limit": limit})
        return tuple(_playlist(o) for o in _data(data))

    def library_songs(self, limit: int = 25) -> tuple[LibrarySong, ...]:
        data = self._get("/me/library/songs", user=True, params={"limit": min(limit, 100)})
        return tuple(_library_song(o) for o in _data(data))

    def add_to_library(self, song_ids: tuple[str, ...]) -> None:
        # verify-live: add CATALOG song ids to the library. Apple takes them as a
        # bracketed query param (`ids[songs]=a,b`), not a JSON body; returns 202.
        self._request("POST", "/me/library", user=True, params={"ids[songs]": ",".join(song_ids)})

    def remove_from_library(self, library_song_ids: tuple[str, ...]) -> None:
        # Verified live 2026-07-21: removal takes LIBRARY ids (the `i.xxxx` ones
        # from `library_songs`), one DELETE each (no batch delete), and MUST go to
        # amp-api — the public host 401s this exact call. Returns 204.
        for lid in library_song_ids:
            self._request("DELETE", f"/me/library/songs/{lid}", user=True, base=_AMP_BASE)

    def create_playlist(
        self, name: str, *, description: str | None = None, song_ids: tuple[str, ...] = ()
    ) -> Playlist:
        # verify-live: POST /me/library/playlists. Attributes carry name/description;
        # optional seed tracks go in relationships.tracks.data as {id, type:songs}.
        attributes: dict = {"name": name}
        if description:
            attributes["description"] = description
        body: dict = {"attributes": attributes}
        if song_ids:
            body["relationships"] = {"tracks": {"data": [_ref(i) for i in song_ids]}}
        return _playlist(_one(self._request("POST", "/me/library/playlists", user=True, json=body)))

    def add_to_playlist(self, library_playlist_id: str, song_ids: tuple[str, ...]) -> None:
        # verify-live: add CATALOG song ids to a LIBRARY playlist; 201/204.
        self._request(
            "POST",
            f"/me/library/playlists/{library_playlist_id}/tracks",
            user=True,
            json={"data": [_ref(i) for i in song_ids]},
        )

    # ---- behavioral (needs media-user-token) -------------------------------

    def recent_played(self, limit: int = 25) -> tuple[Song, ...]:
        data = self._get("/me/recent/played/tracks", user=True, params={"limit": min(limit, 30)})
        return tuple(_song(o) for o in _data(data))

    def heavy_rotation(self, limit: int = 25) -> SearchResults:
        """The user's most-played content — a mix of albums/playlists/songs. Mapped
        into SearchResults (reusing its buckets) so render can lay it out like search;
        Apple's `station` items, which have no CLI vocabulary, are dropped."""
        data = self._get("/me/history/heavy-rotation", user=True, params={"limit": min(limit, 30)})
        songs, albums, playlists = [], [], []
        for o in _data(data):
            kind = o.get("type", "")
            if kind in ("songs", "library-songs"):
                songs.append(_song(o))
            elif kind in ("albums", "library-albums"):
                albums.append(_album(o))
            elif kind in ("playlists", "library-playlists"):
                playlists.append(_playlist(o))
        return SearchResults(songs=tuple(songs), albums=tuple(albums), playlists=tuple(playlists))

    def my_storefront(self) -> str | None:
        """The account's own storefront id (e.g. 'tw'), used to correct the catalog
        region once logged in. Returns None if unavailable."""
        data = self._get("/me/storefront", user=True)
        items = _data(data)
        return items[0].get("id") if items else None


# ---- wire → model maps (the caged knowledge) -------------------------------


def _data(section: dict | None) -> list[dict]:
    """The `data` array of a collection/relationship section (or [])."""
    return section.get("data", []) if section else []


def _one(payload: dict) -> dict:
    """A single-item GET returns `{data:[{…}]}` — pull the one object, or raise."""
    items = payload.get("data", [])
    if not items:
        raise AppleApiError("Apple Music returned no item for that id.")
    return items[0]


def _ref(song_id: str) -> dict:
    """A catalog-song reference for a write body: {id, type:"songs"}."""
    return {"id": song_id, "type": "songs"}


def _attrs(obj: dict) -> dict:
    return obj.get("attributes", {}) or {}


def _artist_names(attrs: dict) -> tuple[str, ...]:
    name = attrs.get("artistName")
    return (name,) if name else ()


def _song(obj: dict) -> Song:
    a = _attrs(obj)
    return Song(
        id=obj.get("id", ""),
        type=obj.get("type", "songs"),
        name=a.get("name", ""),
        artists=_artist_names(a),
        album=a.get("albumName"),
        duration_ms=a.get("durationInMillis"),
        release_date=a.get("releaseDate"),
        url=a.get("url"),
    )


def _library_song(obj: dict) -> LibrarySong:
    """Map a library/playlist row: the same {id,type,attributes} shape as a
    catalog song, plus `attributes.dateAdded` for the membership timestamp."""
    return LibrarySong(song=_song(obj), added_at=_attrs(obj).get("dateAdded"))


def _album(obj: dict) -> Album:
    a = _attrs(obj)
    return Album(
        id=obj.get("id", ""),
        type=obj.get("type", "albums"),
        name=a.get("name", ""),
        artists=_artist_names(a),
        release_date=a.get("releaseDate"),
        track_count=a.get("trackCount"),
        url=a.get("url"),
    )


def _artist(obj: dict) -> Artist:
    a = _attrs(obj)
    return Artist(
        id=obj.get("id", ""),
        type=obj.get("type", "artists"),
        name=a.get("name", ""),
        genres=tuple(a.get("genreNames", [])),
        url=a.get("url"),
    )


def _playlist(obj: dict) -> Playlist:
    a = _attrs(obj)
    desc = a.get("description") or {}
    return Playlist(
        id=obj.get("id", ""),
        type=obj.get("type", "playlists"),
        name=a.get("name", ""),
        curator=a.get("curatorName"),
        description=desc.get("standard") or desc.get("short") if isinstance(desc, dict) else None,
        track_count=a.get("trackCount"),
        url=a.get("url"),
    )
