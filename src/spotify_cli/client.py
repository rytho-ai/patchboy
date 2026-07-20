"""CAGED — the only module that knows Spotify's Web API wire shape.

Everything above this speaks models.py's vocabulary. A Spotify field rename or
endpoint reshape must be fixable here without cli.py or models.py moving. Raw
httpx (not spotipy) on purpose: Development Mode can only reach a narrow surface
(single-item GETs, search, /me/* — no batch, no recommendations/audio-features),
so a hand-pinned client is a better fit than an SDK whose surface is far wider
than what this app can actually call. See CLAUDE.md.

Two Spotify auth-failure shapes both mean "session dead" → SpotifyAuthError
(the CLI turns that into an exit-2 human handoff): an outer 401, or a 403 that
is specifically an expired/insufficient token. 429 (rate limit) is a distinct,
retryable RateLimited with the server's Retry-After.

CAVEAT: several endpoint shapes below reflect Spotify's Feb 2026 restructuring,
which is recent and fast-moving. They are marked `# verify-live`; confirm each
against the account's real responses once the Dashboard app exists. Nothing here
has been exercised against the live API yet.
"""

from __future__ import annotations

import httpx

from .auth import SpotifyAuthError, SpotifySession
from .models import (
    Album,
    Artist,
    Playlist,
    PlaylistDetail,
    SavedTrack,
    SearchResults,
    Track,
)

_BASE = "https://api.spotify.com/v1"

# User-friendly time_range → Spotify's wire value. Caged here so cli.py never
# has to know the `_term` suffix exists.
_TIME_RANGES = {"short": "short_term", "medium": "medium_term", "long": "long_term"}


class RateLimited(RuntimeError):
    """Spotify returned 429. `retry_after` is seconds to wait (may be None)."""

    def __init__(self, retry_after: int | None) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Spotify rate limit hit — retry after {retry_after}s"
            if retry_after is not None
            else "Spotify rate limit hit"
        )


class SpotifyApiError(RuntimeError):
    """An ordinary API failure (not-found, bad request) — exit 1, no handoff."""


class Client:
    """Thin authed wrapper. Construct with a live (already-refreshed) session."""

    def __init__(self, session: SpotifySession) -> None:
        self._session = session

    # ---- low-level ---------------------------------------------------------

    def _request(self, method: str, path: str, **kw) -> dict:
        resp = httpx.request(
            method,
            f"{_BASE}{path}",
            headers=self._session.auth_header,
            timeout=15,
            **kw,
        )
        if resp.status_code in (401, 403) and _is_auth_failure(resp):
            raise SpotifyAuthError(
                "Spotify rejected the access token (expired or insufficient scope). "
                "Run `spotify login` again."
            )
        if resp.status_code == 429:
            ra = resp.headers.get("retry-after")
            raise RateLimited(int(ra) if ra and ra.isdigit() else None)
        if resp.status_code == 204:
            return {}
        if resp.status_code >= 400:
            raise SpotifyApiError(f"Spotify API error (status {resp.status_code}): {resp.text}")
        return resp.json() if resp.content else {}

    def _get(self, path: str, **kw) -> dict:
        return self._request("GET", path, **kw)

    # ---- catalog -----------------------------------------------------------

    def search(
        self,
        query: str,
        types: tuple[str, ...],
        limit: int = 10,
        *,
        year: str | None = None,
        genre: str | None = None,
    ) -> SearchResults:
        # Feb 2026: limit max is 10 (was 50). Caller should clamp; we clamp too.
        # `year:`/`genre:` are Spotify's own query field-filter SYNTAX (server-side)
        # — that's wire knowledge, so building it belongs here in the caged client,
        # not in cli.py. year accepts "2020" or a "2015-2020" range.
        q = query
        if year:
            q += f" year:{year}"
        if genre:
            q += f" genre:{genre}"
        data = self._get(
            "/search",
            params={"q": q, "type": ",".join(types), "limit": min(limit, 10)},
        )
        return SearchResults(
            tracks=tuple(_track(t) for t in _items(data.get("tracks"))),
            albums=tuple(_album(a) for a in _items(data.get("albums"))),
            artists=tuple(_artist(a) for a in _items(data.get("artists"))),
            playlists=tuple(_playlist(p) for p in _items(data.get("playlists"))),
        )

    def track(self, track_id: str) -> Track:
        return _track(self._get(f"/tracks/{track_id}"))

    def album(self, album_id: str) -> Album:
        return _album(self._get(f"/albums/{album_id}"))

    def artist(self, artist_id: str) -> Artist:
        return _artist(self._get(f"/artists/{artist_id}"))

    def playlist(self, playlist_id: str) -> PlaylistDetail:
        data = self._get(f"/playlists/{playlist_id}")
        pl = _playlist(data)
        # verify-live: Feb 2026 renamed /tracks → /items and narrowed visibility
        # (full items only for playlists you own/collaborate on). Absent items
        # object → metadata-only, not an empty playlist.
        items_field = data.get("items") or data.get("tracks")
        if items_field is None:
            return PlaylistDetail(playlist=pl, items_visible=False)
        rows = items_field.get("items", []) if isinstance(items_field, dict) else []
        items = tuple(_saved(r) for r in rows if (r.get("item") or r.get("track")))
        return PlaylistDetail(playlist=pl, items_visible=True, items=items)

    # ---- user account ------------------------------------------------------

    def my_playlists(self, limit: int = 20) -> tuple[Playlist, ...]:
        data = self._get("/me/playlists", params={"limit": limit})
        return tuple(_playlist(p) for p in data.get("items", []))

    def saved_tracks(self, limit: int = 20) -> tuple[SavedTrack, ...]:
        data = self._get("/me/tracks", params={"limit": min(limit, 50)})
        return tuple(_saved(r) for r in data.get("items", []) if r.get("track"))

    def top_items(
        self, kind: str, *, time_range: str = "medium", limit: int = 20
    ) -> tuple[Artist, ...] | tuple[Track, ...]:
        """GET /me/top/{artists|tracks} — the user's most-played items.

        `time_range` is the user-friendly "short"/"medium"/"long"; the
        `*_term` suffix Spotify's wire actually wants is caged here, never
        surfaced to cli.py. Limit is NOT capped at 10 like search — this
        endpoint kept the full 50.
        """
        if kind not in ("artists", "tracks"):
            raise SpotifyApiError(f"Unknown top-items kind: {kind!r} (expected 'artists' or 'tracks').")
        term = _TIME_RANGES.get(time_range)
        if term is None:
            raise SpotifyApiError(
                f"Unknown time_range: {time_range!r} (expected 'short', 'medium', or 'long')."
            )
        data = self._get(f"/me/top/{kind}", params={"time_range": term, "limit": min(limit, 50)})
        items = _items(data)
        if kind == "artists":
            return tuple(_artist(item) for item in items)
        return tuple(_track(item) for item in items)

    def create_playlist(self, name: str, *, public: bool = False, description: str | None = None) -> Playlist:
        # Verified live 2026-07-20: POST /me/playlists → 201. The classic
        # POST /users/{id}/playlists now returns 403 Forbidden (Feb 2026 change).
        body: dict = {"name": name, "public": public}
        if description:
            body["description"] = description
        return _playlist(self._request("POST", "/me/playlists", json=body))

    def add_to_playlist(self, playlist_id: str, uris: tuple[str, ...]) -> None:
        # Verified live 2026-07-20: /items → 201. The old /tracks path now 403s.
        self._request("POST", f"/playlists/{playlist_id}/items", json={"uris": list(uris)})

    def save_tracks(self, uris: tuple[str, ...]) -> None:
        # Verified live 2026-07-20: PUT /me/library with `uris` as a COMMA-SEPARATED
        # QUERY param (not a JSON body — a JSON body 400s "Missing required field").
        # The classic PUT /me/tracks (ids) now 403s. Feb 2026 change.
        self._request("PUT", "/me/library", params={"uris": ",".join(uris)})

    def remove_saved_tracks(self, uris: tuple[str, ...]) -> None:
        # Verified live 2026-07-20: DELETE /me/library, `uris` as a query param.
        self._request("DELETE", "/me/library", params={"uris": ",".join(uris)})


# ---- wire → model maps (the caged knowledge) -------------------------------


def _is_auth_failure(resp: httpx.Response) -> bool:
    """A 401 is always auth. A 403 is auth only if the body says so (Spotify
    also uses 403 for non-auth policy denials we don't want to treat as login)."""
    if resp.status_code == 401:
        return True
    try:
        msg = resp.json().get("error", {}).get("message", "").lower()
    except Exception:
        return False
    return "token" in msg or "expired" in msg or "scope" in msg


def _items(section: dict | None) -> list[dict]:
    return section.get("items", []) if section else []


def _artist_names(obj: dict) -> tuple[str, ...]:
    return tuple(a.get("name", "") for a in obj.get("artists", []))


def _track(obj: dict) -> Track:
    return Track(
        id=obj.get("id", ""),
        name=obj.get("name", ""),
        uri=obj.get("uri", ""),
        artists=_artist_names(obj),
        album=(obj.get("album") or {}).get("name"),
        duration_ms=obj.get("duration_ms"),
        release_date=(obj.get("album") or {}).get("release_date"),
    )


def _saved(row: dict) -> SavedTrack:
    """Map a collection row ({added_at, track/item}) — used by both /me/tracks and
    playlist items, which share this envelope shape."""
    return SavedTrack(track=_track(row.get("item") or row.get("track") or {}), added_at=row.get("added_at"))


def _album(obj: dict) -> Album:
    return Album(
        id=obj.get("id", ""),
        name=obj.get("name", ""),
        uri=obj.get("uri", ""),
        artists=_artist_names(obj),
        release_date=obj.get("release_date"),
        total_tracks=obj.get("total_tracks"),
    )


def _artist(obj: dict) -> Artist:
    return Artist(
        id=obj.get("id", ""),
        name=obj.get("name", ""),
        uri=obj.get("uri", ""),
        genres=tuple(obj.get("genres", [])),
    )


def _playlist(obj: dict) -> Playlist:
    return Playlist(
        id=obj.get("id", ""),
        name=obj.get("name", ""),
        uri=obj.get("uri", ""),
        owner=(obj.get("owner") or {}).get("display_name") or (obj.get("owner") or {}).get("id"),
        # Feb 2026: the track-count summary moved from `tracks.total` to
        # `items.total` (the whole `tracks` field was renamed `items`). Read the
        # new key, fall back to the old for safety. Verified live 2026-07-20.
        total_tracks=(obj.get("items") or obj.get("tracks") or {}).get("total"),
        public=obj.get("public"),
        description=obj.get("description"),
    )
