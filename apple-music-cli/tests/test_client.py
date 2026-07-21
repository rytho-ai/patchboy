"""client.py unit tests — the caged wire mapping. No network: httpx.request is
monkeypatched."""

from __future__ import annotations

import pytest

from apple_music_cli import client as client_module
from apple_music_cli.auth import AppleMusicAuthError, AppleMusicSession
from apple_music_cli.client import AppleApiError, AppleSubscriptionError, Client, RateLimited
from apple_music_cli.models import Song


def _session(media: str | None = "mut") -> AppleMusicSession:
    return AppleMusicSession(
        developer_token="dev", developer_token_expiry=2**40, storefront="us", media_user_token=media
    )


class _Resp:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"x"
        self.headers = headers or {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


def _patch(monkeypatch, resp: _Resp, sink: dict | None = None) -> None:
    def _fake_request(method, url, **kw):
        if sink is not None:
            sink.update(method=method, url=url, headers=kw.get("headers"), params=kw.get("params"), json=kw.get("json"))
        return resp

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)


def test_search_maps_results_and_builds_catalog_path(monkeypatch) -> None:
    sink: dict = {}
    payload = {
        "results": {
            "songs": {
                "data": [
                    {
                        "id": "1",
                        "type": "songs",
                        "attributes": {
                            "name": "Idioteque",
                            "artistName": "Radiohead",
                            "albumName": "Kid A",
                            "durationInMillis": 292000,
                            "releaseDate": "2000-10-02",
                        },
                    }
                ]
            }
        }
    }
    _patch(monkeypatch, _Resp(200, payload), sink)
    result = Client(_session()).search("kid a", ("song", "album"), limit=5)

    assert sink["url"].endswith("/catalog/us/search")
    assert sink["params"] == {"term": "kid a", "types": "songs,albums", "limit": 5}
    assert result.songs == (
        Song(id="1", type="songs", name="Idioteque", artists=("Radiohead",), album="Kid A", duration_ms=292000, release_date="2000-10-02", url=None),
    )


def test_search_clamps_limit_to_25(monkeypatch) -> None:
    sink: dict = {}
    _patch(monkeypatch, _Resp(200, {"results": {}}), sink)
    Client(_session()).search("q", ("song",), limit=999)
    assert sink["params"]["limit"] == 25


def test_song_unwraps_single_item_envelope(monkeypatch) -> None:
    payload = {"data": [{"id": "9", "type": "songs", "attributes": {"name": "One", "artistName": "A"}}]}
    _patch(monkeypatch, _Resp(200, payload))
    assert Client(_session()).song("9") == Song(id="9", type="songs", name="One", artists=("A",))


def test_single_item_empty_data_raises(monkeypatch) -> None:
    _patch(monkeypatch, _Resp(200, {"data": []}))
    with pytest.raises(AppleApiError):
        Client(_session()).artist("nope")


def test_library_song_carries_date_added(monkeypatch) -> None:
    payload = {
        "data": [
            {"id": "i.1", "type": "library-songs", "attributes": {"name": "L", "artistName": "A", "dateAdded": "2026-07-09T15:03:40Z"}}
        ]
    }
    sink: dict = {}
    _patch(monkeypatch, _Resp(200, payload), sink)
    items = Client(_session()).library_songs()
    assert sink["url"].endswith("/me/library/songs")
    assert items[0].added_at == "2026-07-09T15:03:40Z"
    assert items[0].song.id == "i.1"


def test_user_call_sends_media_user_token_header(monkeypatch) -> None:
    sink: dict = {}
    _patch(monkeypatch, _Resp(200, {"data": []}), sink)
    Client(_session("secret-mut")).my_playlists()
    assert sink["headers"]["media-user-token"] == "secret-mut"


def test_user_call_without_token_raises_before_network(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("must not hit the network without a media-user-token")

    monkeypatch.setattr(client_module.httpx, "request", _boom)
    with pytest.raises(AppleMusicAuthError):
        Client(_session(media=None)).my_playlists()


def test_heavy_rotation_routes_by_type(monkeypatch) -> None:
    payload = {
        "data": [
            {"id": "s", "type": "songs", "attributes": {"name": "S", "artistName": "A"}},
            {"id": "al", "type": "albums", "attributes": {"name": "Al", "artistName": "A"}},
            {"id": "pl", "type": "playlists", "attributes": {"name": "Pl"}},
            {"id": "st", "type": "stations", "attributes": {"name": "St"}},  # dropped
        ]
    }
    _patch(monkeypatch, _Resp(200, payload))
    res = Client(_session()).heavy_rotation()
    assert [s.id for s in res.songs] == ["s"]
    assert [a.id for a in res.albums] == ["al"]
    assert [p.id for p in res.playlists] == ["pl"]


def test_add_to_library_uses_bracketed_query_param(monkeypatch) -> None:
    sink: dict = {}
    _patch(monkeypatch, _Resp(202, {}), sink)
    Client(_session()).add_to_library(("111", "222"))
    assert sink["method"] == "POST"
    assert sink["url"].endswith("/me/library")
    assert sink["params"] == {"ids[songs]": "111,222"}


def test_remove_from_library_routes_to_amp_api(monkeypatch) -> None:
    sink: dict = {}
    _patch(monkeypatch, _Resp(204, {}), sink)
    Client(_session()).remove_from_library(("i.abc",))
    assert sink["method"] == "DELETE"
    assert sink["url"] == "https://amp-api.music.apple.com/v1/me/library/songs/i.abc"


def test_playlist_add_sends_ref_body(monkeypatch) -> None:
    sink: dict = {}
    _patch(monkeypatch, _Resp(201, {}), sink)
    Client(_session()).add_to_playlist("p.1", ("111",))
    assert sink["json"] == {"data": [{"id": "111", "type": "songs"}]}


def test_401_maps_to_auth_error(monkeypatch) -> None:
    _patch(monkeypatch, _Resp(401, {"errors": [{"status": "401"}]}))
    with pytest.raises(AppleMusicAuthError):
        Client(_session()).song("1")


def test_429_maps_to_rate_limited_with_retry_after(monkeypatch) -> None:
    _patch(monkeypatch, _Resp(429, {}, headers={"retry-after": "7"}))
    with pytest.raises(RateLimited) as ei:
        Client(_session()).song("1")
    assert ei.value.retry_after == 7


def test_404_maps_to_api_error(monkeypatch) -> None:
    _patch(monkeypatch, _Resp(404, {"errors": [{"status": "404"}]}))
    with pytest.raises(AppleApiError):
        Client(_session()).song("1")


def test_400_insufficient_privileges_maps_to_subscription_error(monkeypatch) -> None:
    resp = _Resp(400, {})
    resp.text = '{"errors":[{"title":"Insufficient Privileges","detail":"...CloudLibrary"}]}'
    _patch(monkeypatch, resp)
    with pytest.raises(AppleSubscriptionError):
        Client(_session()).library_songs()
