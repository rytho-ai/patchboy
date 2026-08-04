"""client.py unit tests — the caged wire mapping. No network: httpx.request is
monkeypatched (same pattern as test_auth.py's httpx.post patching)."""

from __future__ import annotations

import pytest

from spotify_cli import client as client_module
from spotify_cli.auth import SpotifySession
from spotify_cli.client import Client, SpotifyApiError
from spotify_cli.models import Artist, Track


def _session() -> SpotifySession:
    return SpotifySession(access_token="tok", refresh_token="ref", access_expiry=2**40)


class _Resp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"x"  # truthy — _request only calls .json() when non-empty
        self.headers: dict[str, str] = {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


@pytest.mark.parametrize(
    "friendly, wire",
    [("short", "short_term"), ("medium", "medium_term"), ("long", "long_term")],
)
def test_top_items_maps_time_range_to_wire_term(monkeypatch, friendly, wire) -> None:
    captured: dict = {}

    def _fake_request(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp(200, {"items": []})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    Client(_session()).top_items("artists", time_range=friendly, limit=7)

    assert captured["method"] == "GET"
    assert captured["url"].endswith("/me/top/artists")
    assert captured["params"] == {"time_range": wire, "limit": 7}


def test_top_items_artists_maps_json_to_artist_models(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {"items": [{"id": "a1", "name": "Radiohead", "uri": "spotify:artist:a1", "genres": ["art rock"]}]},
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).top_items("artists", time_range="medium")

    assert result == (Artist(id="a1", name="Radiohead", uri="spotify:artist:a1", genres=("art rock",)),)


def test_top_items_tracks_maps_json_to_track_models(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {
                "items": [
                    {
                        "id": "t1",
                        "name": "Idioteque",
                        "uri": "spotify:track:t1",
                        "artists": [{"name": "Radiohead"}],
                        "album": {"name": "Kid A", "release_date": "2000-10-02"},
                        "duration_ms": 292000,
                    }
                ]
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).top_items("tracks", time_range="long")

    assert result == (
        Track(
            id="t1",
            name="Idioteque",
            uri="spotify:track:t1",
            artists=("Radiohead",),
            album="Kid A",
            duration_ms=292000,
            release_date="2000-10-02",
        ),
    )


def test_top_items_clamps_limit_to_50(monkeypatch) -> None:
    captured: dict = {}

    def _fake_request(method, url, **kw):
        captured["params"] = kw.get("params")
        return _Resp(200, {"items": []})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    Client(_session()).top_items("tracks", time_range="short", limit=999)

    assert captured["params"]["limit"] == 50


def test_top_items_unknown_time_range_raises_without_network(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("must not hit the network for an invalid time_range")

    monkeypatch.setattr(client_module.httpx, "request", _boom)
    with pytest.raises(SpotifyApiError):
        Client(_session()).top_items("artists", time_range="decade")


def test_top_items_unknown_kind_raises_without_network(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("must not hit the network for an invalid kind")

    monkeypatch.setattr(client_module.httpx, "request", _boom)
    with pytest.raises(SpotifyApiError):
        Client(_session()).top_items("albums", time_range="medium")


def test_remove_from_playlist_uses_current_items_endpoint_and_item_objects(monkeypatch) -> None:
    captured: dict = {}

    def _fake_request(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kw.get("json")
        return _Resp(200, {"snapshot_id": "snap-2"})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    Client(_session()).remove_from_playlist(
        "playlist-1",
        ("spotify:track:t1", "spotify:track:t2"),
    )

    assert captured == {
        "method": "DELETE",
        "url": "https://api.spotify.com/v1/playlists/playlist-1/items",
        "json": {
            "items": [
                {"uri": "spotify:track:t1"},
                {"uri": "spotify:track:t2"},
            ]
        },
    }


def test_playlist_follows_next_pages_and_preserves_snapshot(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_request(method, url, **kw):
        calls.append(url)
        if url.endswith("/playlists/p1"):
            return _Resp(
                200,
                {
                    "id": "p1",
                    "name": "Breaks Inbox",
                    "uri": "spotify:playlist:p1",
                    "items": {
                        "items": [
                            {
                                "added_at": "2026-08-01",
                                "item": {"id": "t1", "name": "One", "uri": "spotify:track:t1"},
                            }
                        ],
                        "next": "https://api.spotify.com/v1/playlists/p1/items?offset=1",
                    },
                    "snapshot_id": "snap-1",
                },
            )
        return _Resp(
            200,
            {
                "items": [
                    {
                        "added_at": "2026-08-02",
                        "item": {"id": "t2", "name": "Two", "uri": "spotify:track:t2"},
                    }
                ],
                "next": None,
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).playlist("p1")

    assert [item.track.id for item in result.items] == ["t1", "t2"]
    assert result.snapshot_id == "snap-1"
    assert calls == [
        "https://api.spotify.com/v1/playlists/p1",
        "https://api.spotify.com/v1/playlists/p1/items?offset=1",
    ]


def test_my_playlists_follows_next_pages_until_limit(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        if url.endswith("/me/playlists"):
            return _Resp(
                200,
                {
                    "items": [{"id": "p1", "name": "One", "uri": "spotify:playlist:p1"}],
                    "next": "https://api.spotify.com/v1/me/playlists?offset=1",
                },
            )
        return _Resp(
            200,
            {"items": [{"id": "p2", "name": "Two", "uri": "spotify:playlist:p2"}], "next": None},
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).my_playlists(limit=2)

    assert [playlist.id for playlist in result] == ["p1", "p2"]
