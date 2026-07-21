"""Session construction and JWT expiry parsing — no network needed."""

from __future__ import annotations

import base64
import json
import time

import pytest

from splice_cli.auth import (
    AUTH_COOKIE,
    CLEARANCE_COOKIE,
    NoSessionError,
    Session,
    _jwt_exp,
    _parse_cookie_header,
    parse_pasted_session,
)


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


# --- pasted-session parsing (the --paste manual login path) -----------------


def test_parse_cookie_header_splits_name_value_pairs():
    header = f"{AUTH_COOKIE}=tok; {CLEARANCE_COOKIE}=clear; __cf_bm=bm"
    assert _parse_cookie_header(header) == {AUTH_COOKIE: "tok", CLEARANCE_COOKIE: "clear", "__cf_bm": "bm"}


def test_parse_cookie_header_value_may_contain_equals_signs():
    # A JWT's base64 padding legitimately contains '=' — must split on the
    # FIRST '=' only, not treat every '=' as a new pair boundary.
    header = f"{AUTH_COOKIE}=abc==def=="
    assert _parse_cookie_header(header) == {AUTH_COOKIE: "abc==def=="}


def test_parse_cookie_header_tolerates_stray_whitespace_and_empty_segments():
    header = f"  {AUTH_COOKIE} = tok  ;; {CLEARANCE_COOKIE}=clear ; "
    assert _parse_cookie_header(header) == {AUTH_COOKIE: "tok", CLEARANCE_COOKIE: "clear"}


def test_parse_cookie_header_empty_string_yields_empty_dict():
    assert _parse_cookie_header("") == {}


def test_parse_pasted_session_builds_a_valid_session():
    header = f"{AUTH_COOKIE}={_fake_jwt(123)}; {CLEARANCE_COOKIE}=clear"
    sess = parse_pasted_session(header, "  Mozilla/5.0 test-UA  ")
    assert sess.cookies[AUTH_COOKIE] and sess.cookies[CLEARANCE_COOKIE] == "clear"
    assert sess.user_agent == "Mozilla/5.0 test-UA"  # stripped
    assert sess.source == "pasted"
    assert sess.access_token_expiry == 123  # derived via the normal __post_init__ path


def test_parse_pasted_session_without_auth_cookie_raises():
    with pytest.raises(NoSessionError):
        parse_pasted_session(f"{CLEARANCE_COOKIE}=clear", "ua")


def test_parse_pasted_session_clearance_expiry_stays_none():
    # A raw Cookie header carries no expiry metadata — unlike what
    # firefox.py/chrome.py read from the browser's actual cookie store.
    header = f"{AUTH_COOKIE}={_fake_jwt(123)}; {CLEARANCE_COOKIE}=clear"
    sess = parse_pasted_session(header, "ua")
    assert sess.clearance_expiry is None
