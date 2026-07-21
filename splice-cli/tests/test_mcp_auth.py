"""PKCE correctness and McpSession expiry/header logic — no network needed."""

from __future__ import annotations

import base64
import hashlib
import socket
import time

import pytest

from splice_cli.mcp_auth import McpAuthError, McpSession, _await_callback, _pkce_pair


def test_pkce_challenge_matches_verifier():
    verifier, challenge = _pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected


def test_pkce_pair_is_random_each_call():
    v1, c1 = _pkce_pair()
    v2, c2 = _pkce_pair()
    assert v1 != v2
    assert c1 != c2


def test_pkce_challenge_has_no_padding():
    _, challenge = _pkce_pair()
    assert "=" not in challenge


def test_session_expired_in_the_past():
    sess = McpSession(client_id="c", access_token="a", refresh_token="r", access_expiry=time.time() - 10)
    assert sess.expired()


def test_session_not_expired_in_the_future():
    sess = McpSession(client_id="c", access_token="a", refresh_token="r", access_expiry=time.time() + 3600)
    assert not sess.expired()


def test_session_auth_header():
    sess = McpSession(client_id="c", access_token="tok123", refresh_token="r", access_expiry=0)
    assert sess.auth_header == {"authorization": "Bearer tok123"}


class TestAwaitCallbackPortBusy:
    """HARDENING 7 — a busy loopback port used to raise a raw OSError out of
    `login()` (via `http.server.HTTPServer`'s constructor); it must instead
    surface as a clear McpAuthError mentioning the port.
    """

    def test_port_already_in_use_raises_mcp_auth_error_not_os_error(self):
        # Occupy the port ourselves so the real bind attempt collides.
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("localhost", 0))
        port = blocker.getsockname()[1]
        blocker.listen(1)
        try:
            with pytest.raises(McpAuthError) as exc_info:
                _await_callback(port, timeout=1)
            assert str(port) in str(exc_info.value)
            assert "--port" in str(exc_info.value)
        finally:
            blocker.close()
