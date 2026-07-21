"""Auth unit tests — no network. Manual paste-back flow only: URL shape,
redirect-response parsing, state verification, the code→short-lived→long-lived
token chain, and the refresh fallback. No PKCE here — Threads doesn't use it."""

from __future__ import annotations

import time

import pytest

from threads_cli import auth
from threads_cli.auth import ThreadsAuthError, ThreadsSession


def test_begin_login_builds_authorize_url_with_state_and_scopes() -> None:
    ch = auth.begin_login(client_id="cid", redirect_uri="https://example.com/cb")
    assert ch.authorize_url.startswith("https://threads.net/oauth/authorize?")
    assert "client_id=cid" in ch.authorize_url
    assert "response_type=code" in ch.authorize_url
    assert f"state={ch.state}" in ch.authorize_url
    assert ch.redirect_uri == "https://example.com/cb"
    assert "threads_basic" in ch.authorize_url


def test_begin_login_defaults_to_https_localhost_placeholder() -> None:
    ch = auth.begin_login(client_id="cid")
    assert ch.redirect_uri == "https://localhost/"


def test_begin_login_state_is_unique_each_call() -> None:
    assert auth.begin_login(client_id="cid").state != auth.begin_login(client_id="cid").state


# ---- redirect-response parsing ---------------------------------------------


def test_parse_redirect_response_full_url() -> None:
    code, state = auth._parse_redirect_response("https://localhost/?code=AC&state=ST")
    assert (code, state) == ("AC", "ST")


def test_parse_redirect_response_bare_code_has_no_state() -> None:
    code, state = auth._parse_redirect_response("  AC123  ")
    assert code == "AC123"
    assert state is None


def test_parse_redirect_response_empty() -> None:
    assert auth._parse_redirect_response("   ") == (None, None)


def test_parse_redirect_response_query_string_only() -> None:
    code, state = auth._parse_redirect_response("?code=AC&state=ST")
    assert (code, state) == ("AC", "ST")


# ---- complete_login (state check, code presence) ----------------------------


def test_complete_login_no_code_raises() -> None:
    ch = auth.begin_login(client_id="cid")
    with pytest.raises(ThreadsAuthError, match="authorization code"):
        auth.complete_login(ch, "https://localhost/?error=access_denied", client_id="cid", client_secret="sec")


def test_complete_login_rejects_state_mismatch(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid")
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exchange")))
    with pytest.raises(ThreadsAuthError, match="state mismatch"):
        auth.complete_login(ch, "https://localhost/?code=AC&state=WRONG", client_id="cid", client_secret="sec")


def test_complete_login_bare_code_skips_state_check(monkeypatch) -> None:
    """A bare pasted code has no state to verify — PKCE-less Threads still
    lets this through (there's no code_verifier to protect the exchange, but
    that's an accepted tradeoff of the manual-paste convenience path, same as
    spotify-cli/splice-cli's identical fallback)."""
    ch = auth.begin_login(client_id="cid")

    class _ShortResp:
        status_code = 200

        def json(self):
            return {"access_token": "short", "user_id": "u1"}

    class _LongResp:
        status_code = 200

        def json(self):
            return {"access_token": "long", "expires_in": 5184000}

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _ShortResp())
    monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: _LongResp())
    sess = auth.complete_login(ch, "AC123", client_id="cid", client_secret="sec")
    assert sess.access_token == "long"
    assert sess.user_id == "u1"


def test_complete_login_exchanges_code_then_long_lived_token(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid")
    captured: dict = {}

    class _ShortResp:
        status_code = 200

        def json(self):
            return {"access_token": "short_tok", "user_id": "12345"}

    class _LongResp:
        status_code = 200

        def json(self):
            return {"access_token": "long_tok", "expires_in": 5184000}

    def _post(url, data=None, **k):
        captured["post_url"] = url
        captured["post_data"] = data
        return _ShortResp()

    def _get(url, params=None, **k):
        captured["get_url"] = url
        captured["get_params"] = params
        return _LongResp()

    monkeypatch.setattr(auth.httpx, "post", _post)
    monkeypatch.setattr(auth.httpx, "get", _get)

    sess = auth.complete_login(ch, f"https://localhost/?code=AC&state={ch.state}", client_id="cid", client_secret="sec")

    assert captured["post_url"] == "https://graph.threads.net/oauth/access_token"
    assert captured["post_data"]["code"] == "AC"
    assert captured["post_data"]["client_secret"] == "sec"
    assert captured["post_data"]["grant_type"] == "authorization_code"
    assert captured["post_data"]["redirect_uri"] == ch.redirect_uri

    assert captured["get_url"] == "https://graph.threads.net/access_token"
    assert captured["get_params"]["grant_type"] == "th_exchange_token"
    assert captured["get_params"]["access_token"] == "short_tok"

    assert sess.access_token == "long_tok"
    assert sess.user_id == "12345"
    assert not sess.expired()


def test_exchange_code_non_200_raises(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid")

    class _Resp:
        status_code = 400
        text = "bad request"

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(ThreadsAuthError, match="Token exchange failed"):
        auth.complete_login(ch, f"https://localhost/?code=AC&state={ch.state}", client_id="cid", client_secret="sec")


def test_long_lived_exchange_non_200_raises(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid")

    class _ShortResp:
        status_code = 200

        def json(self):
            return {"access_token": "short", "user_id": "u1"}

    class _LongResp:
        status_code = 500
        text = "server error"

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _ShortResp())
    monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: _LongResp())
    with pytest.raises(ThreadsAuthError, match="Long-lived token exchange failed"):
        auth.complete_login(ch, f"https://localhost/?code=AC&state={ch.state}", client_id="cid", client_secret="sec")


# ---- session expiry / near-expiry -------------------------------------------


def test_session_expired_in_the_past() -> None:
    sess = ThreadsSession(access_token="a", user_id="1", access_expiry=time.time() - 10)
    assert sess.expired()
    assert not sess.near_expiry()


def test_session_not_expired_far_in_future() -> None:
    sess = ThreadsSession(access_token="a", user_id="1", access_expiry=time.time() + 60 * 24 * 3600)
    assert not sess.expired()
    assert not sess.near_expiry()


def test_session_near_expiry_within_window() -> None:
    sess = ThreadsSession(access_token="a", user_id="1", access_expiry=time.time() + 3600)  # 1h out
    assert not sess.expired()
    assert sess.near_expiry()


def test_session_params_carries_access_token() -> None:
    sess = ThreadsSession(access_token="tok123", user_id="1", access_expiry=0)
    assert sess.params == {"access_token": "tok123"}


# ---- refresh ------------------------------------------------------------------


def test_refresh_success(monkeypatch) -> None:
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "refreshed", "expires_in": 5184000}

    def _get(url, params=None, **k):
        captured["url"] = url
        captured["params"] = params
        return _Resp()

    monkeypatch.setattr(auth.httpx, "get", _get)
    old = ThreadsSession(access_token="old_tok", user_id="u1", access_expiry=time.time() + 100)
    new = auth.refresh(old)

    assert captured["url"] == "https://graph.threads.net/refresh_access_token"
    assert captured["params"]["grant_type"] == "th_refresh_token"
    assert captured["params"]["access_token"] == "old_tok"
    assert new.access_token == "refreshed"
    assert new.user_id == "u1"  # user_id carried over — refresh response has none


def test_refresh_failure_raises_threads_auth_error(monkeypatch) -> None:
    class _Resp:
        status_code = 400
        text = "invalid token"

    monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: _Resp())
    old = ThreadsSession(access_token="old", user_id="u1", access_expiry=time.time() + 100)
    with pytest.raises(ThreadsAuthError, match="Refresh failed"):
        auth.refresh(old)
