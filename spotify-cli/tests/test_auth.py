"""Auth unit tests — no network. PKCE correctness, redirect-URI shape, and the
refresh fallback when Spotify omits a new refresh token."""

from __future__ import annotations

import base64
import hashlib

from spotify_cli import auth
from spotify_cli.auth import SpotifySession


def test_pkce_pair_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = auth._pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge  # base64url, unpadded


def test_pkce_pairs_are_unique() -> None:
    assert auth._pkce_pair()[0] != auth._pkce_pair()[0]


def test_session_expiry_and_header() -> None:
    fresh = SpotifySession("tok", "ref", access_expiry=2**40)
    assert not fresh.expired()
    assert fresh.auth_header == {"authorization": "Bearer tok"}
    stale = SpotifySession("tok", "ref", access_expiry=0)
    assert stale.expired()


def test_refresh_keeps_old_refresh_token_when_absent(monkeypatch) -> None:
    """Spotify's docs: the refresh response 'might not include a new refresh
    token'. We must keep the old one rather than lose it."""

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "new", "expires_in": 3600}  # no refresh_token

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _Resp())
    old = SpotifySession("old_access", "keep_me", access_expiry=0)
    new = auth.refresh(old, client_id="cid")
    assert new.access_token == "new"
    assert new.refresh_token == "keep_me"


def test_refresh_uses_returned_refresh_token_when_present(monkeypatch) -> None:
    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _Resp())
    new = auth.refresh(SpotifySession("a", "old", access_expiry=0), client_id="cid")
    assert new.refresh_token == "rotated"


# ---- manual (no-server) login flow ----------------------------------------


def test_begin_login_builds_127_0_0_1_redirect_and_pkce() -> None:
    ch = auth.begin_login(client_id="cid", redirect_port=8787)
    assert ch.redirect_uri == "http://127.0.0.1:8787/callback"  # never "localhost"
    assert ch.authorize_url.startswith("https://accounts.spotify.com/authorize?")
    assert "code_challenge_method=S256" in ch.authorize_url
    assert "client_id=cid" in ch.authorize_url


def test_parse_redirect_response_full_url() -> None:
    code, state = auth._parse_redirect_response("http://127.0.0.1:8787/callback?code=AC&state=ST")
    assert (code, state) == ("AC", "ST")


def test_parse_redirect_response_bare_code_has_no_state() -> None:
    code, state = auth._parse_redirect_response("  AC123  ")
    assert code == "AC123"
    assert state is None


def test_parse_redirect_response_empty() -> None:
    assert auth._parse_redirect_response("   ") == (None, None)


def test_complete_login_rejects_state_mismatch(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid", redirect_port=8787)
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no exchange")))
    try:
        auth.complete_login(ch, "http://127.0.0.1:8787/callback?code=AC&state=WRONG", client_id="cid")
        raise AssertionError("expected SpotifyAuthError")
    except auth.SpotifyAuthError as err:
        assert "state mismatch" in str(err)


def test_complete_login_exchanges_on_valid_state(monkeypatch) -> None:
    ch = auth.begin_login(client_id="cid", redirect_port=8787)

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}

    captured: dict = {}

    def _post(url, data=None, **k):
        captured.update(data or {})
        return _Resp()

    monkeypatch.setattr(auth.httpx, "post", _post)
    sess = auth.complete_login(ch, f"http://127.0.0.1:8787/callback?code=AC&state={ch.state}", client_id="cid")
    assert sess.access_token == "at"
    assert captured["code"] == "AC"
    assert captured["code_verifier"] == ch.verifier  # PKCE verifier reaches the exchange
    assert captured["redirect_uri"] == "http://127.0.0.1:8787/callback"


def test_complete_login_no_code_raises() -> None:
    ch = auth.begin_login(client_id="cid", redirect_port=8787)
    try:
        auth.complete_login(ch, "http://127.0.0.1:8787/callback?error=access_denied", client_id="cid")
        raise AssertionError("expected SpotifyAuthError")
    except auth.SpotifyAuthError as err:
        assert "authorization code" in str(err)
