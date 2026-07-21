"""client.py unit tests — the caged wire mapping. No network: httpx.request is
monkeypatched (same pattern as sibling spotify-cli's test_client.py)."""

from __future__ import annotations

import pytest

from threads_cli import client as client_module
from threads_cli.auth import ThreadsAuthError, ThreadsSession
from threads_cli.client import Client, RateLimited, ThreadsApiError
from threads_cli.models import Post, Profile


def _session() -> ThreadsSession:
    return ThreadsSession(access_token="tok", user_id="42", access_expiry=2**40)


class _Resp:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"x"  # truthy — _handle_response only calls .json() when non-empty
        self.headers = headers or {}
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


def test_me_maps_json_to_profile(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        assert kw["params"]["access_token"] == "tok"
        return _Resp(
            200,
            {
                "id": "17841400",
                "username": "example",
                "name": "Example",
                "threads_profile_picture_url": "https://x/pic.jpg",
                "threads_biography": "hi",
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).me()

    assert result == Profile(
        id="17841400",
        username="example",
        name="Example",
        profile_picture_url="https://x/pic.jpg",
        biography="hi",
    )


def test_me_request_shape(monkeypatch) -> None:
    captured: dict = {}

    def _fake_request(method, url, **kw):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp(200, {"id": "1", "username": "u"})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    Client(_session()).me()

    assert captured["method"] == "GET"
    assert captured["url"] == "https://graph.threads.net/me"
    assert "fields" in captured["params"]


def test_my_posts_maps_json_to_post_models(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {
                "data": [
                    {
                        "id": "p1",
                        "text": "hello world",
                        "media_type": "TEXT",
                        "permalink": "https://www.threads.net/@u/post/p1",
                        "timestamp": "2026-07-20T12:00:00+0000",
                    }
                ]
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).my_posts(limit=10)

    assert result == (
        Post(
            id="p1",
            text="hello world",
            media_type="TEXT",
            permalink="https://www.threads.net/@u/post/p1",
            timestamp="2026-07-20T12:00:00+0000",
        ),
    )


def test_my_posts_follows_pagination_until_limit(monkeypatch) -> None:
    """Two pages of 1 post each; limit=2 should follow `paging.next` once via
    the absolute-URL path (_get_absolute), not re-prefix it with _BASE."""
    calls: list[str] = []

    def _fake_request(method, url, **kw):
        calls.append(url)
        if url == "https://graph.threads.net/me/threads":
            return _Resp(
                200,
                {
                    "data": [{"id": "p1", "text": "first"}],
                    "paging": {"next": "https://graph.threads.net/v1.0/12345/threads?after=CURSOR"},
                },
            )
        assert url == "https://graph.threads.net/v1.0/12345/threads?after=CURSOR"
        return _Resp(200, {"data": [{"id": "p2", "text": "second"}]})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).my_posts(limit=2)

    assert [p.id for p in result] == ["p1", "p2"]
    assert calls == [
        "https://graph.threads.net/me/threads",
        "https://graph.threads.net/v1.0/12345/threads?after=CURSOR",
    ]


def test_my_posts_stops_at_limit_without_following_next(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {
                "data": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
                "paging": {"next": "https://graph.threads.net/v1.0/x/threads?after=Y"},
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).my_posts(limit=2)
    assert len(result) == 2


def test_publish_text_two_step_container_then_publish(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_request(method, url, **kw):
        calls.append((method, url))
        if url == "https://graph.threads.net/me/threads":
            assert kw["data"] == {"text": "hi threads", "media_type": "TEXT"}
            return _Resp(200, {"id": "creation123"})
        if url == "https://graph.threads.net/me/threads_publish":
            assert kw["data"] == {"creation_id": "creation123"}
            return _Resp(200, {"id": "published456"})
        if url == "https://graph.threads.net/published456":
            return _Resp(200, {"id": "published456", "text": "hi threads", "media_type": "TEXT"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).publish_text("hi threads")

    assert result.id == "published456"
    assert [c[1] for c in calls] == [
        "https://graph.threads.net/me/threads",
        "https://graph.threads.net/me/threads_publish",
        "https://graph.threads.net/published456",
    ]


def test_publish_text_no_creation_id_raises(monkeypatch) -> None:
    monkeypatch.setattr(client_module.httpx, "request", lambda *a, **k: _Resp(200, {}))
    with pytest.raises(ThreadsApiError, match="Container creation"):
        Client(_session()).publish_text("hi")


def test_auth_failure_oauth_exception_raises_threads_auth_error(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(401, {"error": {"message": "expired token", "type": "OAuthException", "code": 190}})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    with pytest.raises(ThreadsAuthError):
        Client(_session()).me()


def test_rate_limited_on_429(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(429, {}, headers={"retry-after": "30"})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    with pytest.raises(RateLimited) as exc_info:
        Client(_session()).me()
    assert exc_info.value.retry_after == 30


def test_rate_limited_on_graph_rate_limit_code(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(400, {"error": {"message": "limit reached", "type": "OAuthException", "code": 4}})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    with pytest.raises(RateLimited):
        Client(_session()).me()


def test_ordinary_api_error_raises_threads_api_error(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(400, {"error": {"message": "bad request", "type": "GraphMethodException", "code": 100}})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    with pytest.raises(ThreadsApiError):
        Client(_session()).me()


def test_post_insights_maps_values_shape(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {
                "data": [
                    {"name": "views", "title": "Views", "values": [{"value": 120}]},
                    {"name": "likes", "title": "Likes", "values": [{"value": 5}]},
                ]
            },
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).post_insights("p1")

    assert [(m.name, m.value) for m in result.metrics] == [("views", 120), ("likes", 5)]


def test_user_insights_maps_total_value_shape(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        return _Resp(
            200,
            {"data": [{"name": "followers_count", "title": "Followers", "total_value": {"value": 42}}]},
        )

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).user_insights()

    assert result.metrics[0].value == 42


def test_replies_maps_json_to_reply_models(monkeypatch) -> None:
    def _fake_request(method, url, **kw):
        assert url == "https://graph.threads.net/p1/replies"
        return _Resp(200, {"data": [{"id": "r1", "text": "nice", "username": "bob"}]})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    result = Client(_session()).replies("p1")
    assert result[0].id == "r1"
    assert result[0].username == "bob"


def test_conversation_hits_conversation_endpoint(monkeypatch) -> None:
    captured = {}

    def _fake_request(method, url, **kw):
        captured["url"] = url
        return _Resp(200, {"data": []})

    monkeypatch.setattr(client_module.httpx, "request", _fake_request)
    Client(_session()).conversation("p1")
    assert captured["url"] == "https://graph.threads.net/p1/conversation"
