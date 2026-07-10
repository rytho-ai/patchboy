"""Session construction and JWT expiry parsing — no network needed."""

from __future__ import annotations

import base64
import json
import time

import pytest

from splice_cli.auth import AUTH_COOKIE, NoSessionError, Session, _jwt_exp


def _fake_jwt(exp: int | None) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload_obj = {"exp": exp} if exp is not None else {}
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).decode().rstrip("=")
    return f"{header}.{payload}.fakesig"


def test_jwt_exp_reads_expiry_claim():
    token = _fake_jwt(exp=1234567890)
    assert _jwt_exp(token) == 1234567890


def test_jwt_exp_handles_malformed_token():
    assert _jwt_exp("not-a-jwt") is None
    assert _jwt_exp("only.two") is None


def test_session_requires_auth_cookie():
    with pytest.raises(NoSessionError):
        Session(cookies={"other_cookie": "x"}, user_agent="ua")


def test_session_derives_access_token_expiry_from_jwt():
    exp = int(time.time()) + 3600
    sess = Session(cookies={AUTH_COOKIE: _fake_jwt(exp)}, user_agent="ua")
    assert sess.access_token_expiry == exp


def test_session_respects_explicit_access_token_expiry_override():
    # Session.load() from a saved file always leaves this at the default
    # (None) so it's re-derived fresh from the JWT — but if a caller passes
    # an explicit value, __post_init__ must not clobber it.
    sess = Session(
        cookies={AUTH_COOKIE: _fake_jwt(999)},
        user_agent="ua",
        access_token_expiry=42,
    )
    assert sess.access_token_expiry == 42


def test_session_auth_header_uses_the_token_cookie():
    token = _fake_jwt(123)
    sess = Session(cookies={AUTH_COOKIE: token}, user_agent="ua")
    assert sess.auth_header == {"authorization": f"Bearer {token}"}
