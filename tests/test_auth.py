"""auth.py unit tests — token metadata + the borrowed-token scrape. No network:
httpx.Client is monkeypatched."""

from __future__ import annotations

import base64
import json
import time

import pytest

from apple_music_cli import auth
from apple_music_cli.auth import AppleMusicAuthError, AppleMusicSession


def _jwt(exp: float | None) -> str:
    payload = {"exp": exp} if exp is not None else {}
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJFUzI1NiJ9.{seg}.c2ln"


def test_jwt_exp_reads_the_claim() -> None:
    assert auth._jwt_exp(_jwt(1893456000.0)) == 1893456000.0


def test_jwt_exp_falls_back_when_absent() -> None:
    before = time.time()
    got = auth._jwt_exp(_jwt(None))
    assert got > before  # a future fallback, not a crash


def test_catalog_headers_carry_bearer_and_origin() -> None:
    s = AppleMusicSession(developer_token="dev", developer_token_expiry=2**40)
    assert s.catalog_headers == {"authorization": "Bearer dev", "origin": "https://music.apple.com"}


def test_user_headers_include_media_user_token_and_origin() -> None:
    s = AppleMusicSession(developer_token="dev", developer_token_expiry=2**40, media_user_token="mut")
    assert s.user_headers == {
        "authorization": "Bearer dev",
        "media-user-token": "mut",
        "origin": "https://music.apple.com",
    }


def test_user_headers_raise_without_media_user_token() -> None:
    s = AppleMusicSession(developer_token="dev", developer_token_expiry=2**40)
    with pytest.raises(AppleMusicAuthError):
        _ = s.user_headers


def test_dev_token_expired_accounts_for_skew() -> None:
    fresh = AppleMusicSession(developer_token="d", developer_token_expiry=time.time() + 10 * 24 * 3600)
    stale = AppleMusicSession(developer_token="d", developer_token_expiry=time.time() + 60)  # inside skew
    assert not fresh.dev_token_expired()
    assert stale.dev_token_expired()


class _FakeHttp:
    """Minimal stand-in for httpx.Client used as a context manager."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def __enter__(self) -> "_FakeHttp":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def get(self, url: str):
        class _R:
            text = self._responses.get(url, "")

        return _R()


def test_fetch_developer_token_from_a_bundle(monkeypatch) -> None:
    tok = _jwt(2**40)
    page = '<script src="/assets/index-abc.js"></script>'
    bundle = f'var config={{"token":"{tok}"}};'
    responses = {
        auth._WEB_PLAYER: page,
        auth._WEB_ORIGIN + "/assets/index-abc.js": bundle,
    }
    monkeypatch.setattr(auth.httpx, "Client", lambda **kw: _FakeHttp(responses))

    got, exp = auth.fetch_developer_token()
    assert got == tok
    assert exp == float(2**40)


def test_fetch_developer_token_inline_fast_path(monkeypatch) -> None:
    tok = _jwt(2**40)
    responses = {auth._WEB_PLAYER: f"window.MusicKit={{developerToken:'{tok}'}}"}
    monkeypatch.setattr(auth.httpx, "Client", lambda **kw: _FakeHttp(responses))

    got, _ = auth.fetch_developer_token()
    assert got == tok


def test_fetch_developer_token_raises_when_none_found(monkeypatch) -> None:
    responses = {auth._WEB_PLAYER: "<html>no token here</html>"}
    monkeypatch.setattr(auth.httpx, "Client", lambda **kw: _FakeHttp(responses))

    with pytest.raises(AppleMusicAuthError):
        auth.fetch_developer_token()
