"""MCP auth — OAuth 2.1 (dynamic client registration + PKCE loopback).

Splice's official MCP server (https://mcp.splice.com/mcp) is the sanctioned
channel this project should default to (see docs/mcp-integration-proposal.md
and docs/mcp-implementation-plan.md — every endpoint/shape below was verified
live against the real server on 2026-07-10, twice).

This is a genuinely better auth story than firefox.py/chrome.py's cookie
lifting: standard OAuth, self-service dynamic client registration (no
developer-account application), and a refresh token that's independent of
the browser's own web session — refreshing it never risks logging the user
out of splice.com the way replaying the GraphQL path's web-JWT refresh does.

The one constraint carried over from the cookie-based flow: Splice's login
itself still needs a human to click through in a real browser — OAuth
doesn't remove that, it just gives us a clean, stable channel to reach it
through instead of scraping cookies.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

import httpx

_BASE = "https://mcp.splice.com"
_REGISTER_EP = f"{_BASE}/oauth/register"
_AUTHORIZE_EP = f"{_BASE}/oauth/authorize"
_TOKEN_EP = f"{_BASE}/oauth/token"
_SCOPE = "openid profile email offline_access"

DEFAULT_REDIRECT_PORT = 8765
# Users are slow to read instructions and click through a login+consent
# screen — recon's first attempt at 300s timed out mid-login. Give it room.
DEFAULT_LOGIN_TIMEOUT = 1200


class McpAuthError(RuntimeError):
    """Base for anything that should send the user back to a browser login."""


@dataclass(frozen=True)
class McpSession:
    client_id: str
    access_token: str
    refresh_token: str
    access_expiry: float  # unix ts; access_token lives ~1h

    def expired(self) -> bool:
        return time.time() >= self.access_expiry

    @property
    def auth_header(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.access_token}"}


def register_client(client_name: str = "splice-cli", redirect_port: int = DEFAULT_REDIRECT_PORT) -> str:
    """Dynamic client registration (RFC 7591) — self-service, no manual
    developer-account application. Call once; cache the returned client_id.
    """
    resp = httpx.post(
        _REGISTER_EP,
        json={
            "client_name": client_name,
            "redirect_uris": [f"http://localhost:{redirect_port}/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=15,
    )
    if resp.status_code != 201:
        raise McpAuthError(f"Client registration failed (status {resp.status_code}): {resp.text}")
    return resp.json()["client_id"]


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _await_callback(port: int, timeout: int) -> tuple[str | None, str | None]:
    """Run a one-shot localhost HTTP server to catch the OAuth redirect.

    Constraint worth surfacing to the user: this only works if the browser
    completing the login is on the *same machine* as this process — the
    redirect is a loopback URL, not reachable from a different machine or a
    remote/headless session.
    """
    result: dict[str, str | None] = {"code": None, "state": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = qs.get("code", [None])[0]
            result["state"] = qs.get("state", [None])[0]
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab and return to the terminal.")

        def log_message(self, *_args: object) -> None:  # silence stdlib access log
            pass

    try:
        server = http.server.HTTPServer(("localhost", port), Handler)
    except OSError as err:
        # A raw traceback here (bind failures raise uncaught OSError) would
        # be a confusing way to learn the port's busy — wrap it in the same
        # McpAuthError vocabulary as every other login failure.
        raise McpAuthError(
            f"Couldn't start the local OAuth callback server on port {port}: {err}. "
            "The port is likely already in use — maybe another `splice login "
            "--mcp` is still running, or another program is bound to it. Pass "
            "`--port <n>` to `splice login --mcp` to pick a different one."
        ) from err
    server.timeout = timeout
    server.handle_request()
    return result["code"], result["state"]


def login(
    *,
    client_id: str | None = None,
    redirect_port: int = DEFAULT_REDIRECT_PORT,
    timeout: int = DEFAULT_LOGIN_TIMEOUT,
    on_url: Callable[[str], None] = print,
) -> McpSession:
    """Full PKCE authorization-code flow via a localhost loopback redirect.

    `on_url` receives the authorize URL to open (defaults to printing it) —
    the CLI overrides this to render it however it likes. Blocks until the
    browser redirects back or `timeout` elapses.
    """
    client_id = client_id or register_client(redirect_port=redirect_port)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://localhost:{redirect_port}/callback"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    on_url(f"{_AUTHORIZE_EP}?{urllib.parse.urlencode(params)}")

    code, returned_state = _await_callback(redirect_port, timeout)
    if not code:
        raise McpAuthError(
            "No authorization code received — the browser login didn't "
            "complete in time, or was cancelled. Try again."
        )
    if returned_state != state:
        raise McpAuthError("OAuth state mismatch — aborting (possible tampering).")

    resp = httpx.post(
        _TOKEN_EP,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise McpAuthError(f"Token exchange failed (status {resp.status_code}): {resp.text}")
    tok = resp.json()
    return _session_from_token_response(client_id, tok)


def refresh(session: McpSession) -> McpSession:
    """Exchange the refresh token for a fresh access token.

    Unlike the GraphQL path's web-JWT refresh (which rotates a token shared
    with the browser's own session and can log the user out — see
    reconnaissance.md), this refresh token is scoped to this OAuth client
    only. Refreshing it has no effect on the user's actual splice.com login.
    """
    resp = httpx.post(
        _TOKEN_EP,
        data={
            "grant_type": "refresh_token",
            "client_id": session.client_id,
            "refresh_token": session.refresh_token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise McpAuthError(
            f"Refresh failed (status {resp.status_code}): {resp.text}. "
            "Run `splice login --mcp` again."
        )
    tok = resp.json()
    # The server may rotate the refresh token on every use, or may not send
    # one back at all — fall back to the current one rather than losing it.
    if not tok.get("refresh_token"):
        tok["refresh_token"] = session.refresh_token
    return _session_from_token_response(session.client_id, tok)


def _session_from_token_response(client_id: str, tok: dict) -> McpSession:
    return McpSession(
        client_id=client_id,
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        access_expiry=time.time() + tok.get("expires_in", 3600),
    )
