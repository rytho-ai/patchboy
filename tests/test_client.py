"""Field-mapping and auth-classification logic — pure functions, no network."""

from __future__ import annotations

import httpx
import pytest

from splice_cli.auth import Session, SessionExpired
from splice_cli.client import SpliceClient, _check_response, _to_sample

_FAKE_REQUEST = httpx.Request("POST", "https://surfaces-graphql.splice.com/graphql")


def _response(status: int, **kwargs) -> httpx.Response:
    """A standalone httpx.Response usable with .raise_for_status()."""
    return httpx.Response(status, request=_FAKE_REQUEST, **kwargs)


def test_to_sample_maps_licensed_and_price():
    item = {
        "uuid": "hash123",
        "catalog_uuid": "real-uuid-1",
        "name": "Pack_Name/loops/foo_120_Cm.wav",
        "asset_type_slug": "sample",
        "licensed": True,
        "asset_prices": [{"amount": 1, "currency": "Credits"}],
        "bpm": 120,
        "key": "c",
        "duration": 4.5,
        "tags": [{"label": "soul"}, {"label": "vocal"}],
        "files": [
            {"asset_file_type_slug": "preview_mp3", "url": "https://example/preview.mp3"},
            {"asset_file_type_slug": "source", "url": "https://example/source.wav"},
        ],
    }
    s = _to_sample(item)
    assert s.catalog_uuid == "real-uuid-1"
    assert s.pack_name == "Pack_Name"
    assert s.licensed is True
    assert s.price == 1
    assert s.price_currency == "Credits"
    assert s.tags == ["soul", "vocal"]
    assert s.preview_url == "https://example/preview.mp3"  # not the source url


def test_to_sample_defaults_when_fields_absent():
    item = {"uuid": "hash-only", "name": "flat_name.wav"}
    s = _to_sample(item)
    assert s.catalog_uuid is None
    assert s.pack_name is None  # no "/" in name
    assert s.licensed is False
    assert s.price is None
    assert s.preview_url is None
    assert s.asset_type == "sample"  # fallback default
    assert s.provider_name is None  # no provider on non-pack items


def test_to_sample_maps_provider_name_from_pack_item():
    # FEATURE GAP 4 — provider{name} only appears on PackAsset items in the
    # query (samples_search.graphql); guard for absence rather than assuming
    # every item type carries one.
    item = {
        "uuid": "pack-hash",
        "catalog_uuid": "pack-uuid-1",
        "name": "Origin Sound",
        "asset_type_slug": "pack",
        "provider": {"name": "Origin Sound Co.", "permalink_slug": "origin-sound"},
    }
    s = _to_sample(item)
    assert s.provider_name == "Origin Sound Co."


def _fake_session() -> Session:
    return Session(cookies={"_splice_token_prod": "not-a-real-jwt"}, user_agent="test-ua")


def _capture_search_variables(monkeypatch) -> dict:
    """Build a real SpliceClient, monkeypatch its HTTP post to capture the
    GraphQL payload's `variables`, and return an empty-but-valid response."""
    client = SpliceClient(_fake_session())
    captured: dict = {}

    def _fake_post(url, json):  # noqa: A002 - matches httpx.Client.post's kwarg name
        captured["variables"] = json["variables"]
        return _response(
            200,
            json={"data": {"assetsSearch": {"items": [], "response_metadata": {"records": 0}}}},
        )

    monkeypatch.setattr(client._http, "post", _fake_post)
    return client, captured


def test_search_omits_new_filters_when_not_provided(monkeypatch):
    client, captured = _capture_search_variables(monkeypatch)
    client.search("drums")
    variables = captured["variables"]
    assert "key" not in variables
    assert "bpm" not in variables
    assert "min_bpm" not in variables
    assert "max_bpm" not in variables


def test_search_threads_key_bpm_range_and_tags_when_provided(monkeypatch):
    client, captured = _capture_search_variables(monkeypatch)
    client.search(
        "drums", key="c minor", min_bpm=80, max_bpm=100, tags=["tag-1", "tag-2"],
    )
    variables = captured["variables"]
    assert variables["key"] == "c minor"
    assert variables["min_bpm"] == 80
    assert variables["max_bpm"] == 100
    assert variables["tags"] == ["tag-1", "tag-2"]


def test_search_threads_exact_bpm_when_provided(monkeypatch):
    client, captured = _capture_search_variables(monkeypatch)
    client.search("drums", bpm="120")
    assert captured["variables"]["bpm"] == "120"


def test_check_response_ok_passes_through_body():
    resp = _response(200, json={"data": {"ok": True}})
    body = _check_response(resp)
    assert body == {"data": {"ok": True}}


def test_check_response_outer_401_raises_session_expired():
    resp = _response(401)
    with pytest.raises(SessionExpired):
        _check_response(resp)


def test_check_response_inner_unauthenticated_raises_session_expired():
    # The real-world gotcha: outer 200, GraphQL errors[] wraps a 401.
    resp = _response(
        200,
        json={
            "errors": [
                {
                    "message": "401: Unauthorized",
                    "extensions": {"code": "UNAUTHENTICATED"},
                }
            ]
        },
    )
    with pytest.raises(SessionExpired):
        _check_response(resp)


def test_check_response_other_graphql_error_is_plain_runtime_error():
    # Must NOT be classified as SessionExpired (SessionExpired IS a
    # RuntimeError, so a plain `pytest.raises(RuntimeError)` wouldn't catch
    # a misclassification here — assert the concrete type instead).
    resp = _response(200, json={"errors": [{"message": "field not found"}]})
    with pytest.raises(RuntimeError) as exc_info:
        _check_response(resp)
    assert type(exc_info.value) is RuntimeError
