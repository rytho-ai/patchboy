"""Spotify OAuth — Authorization Code + PKCE via a localhost loopback redirect.

Adapted from sibling splice-cli's mcp_auth.py. Spotify offers a *sanctioned*
Web API, so unlike Splice there is no cookie-lifting and no reverse-engineered
backend here — this is the whole auth story. The PKCE machinery (verifier /
challenge, the one-shot loopback callback catcher, the refresh fallback, atomic
0600 persistence in session.py) transfers verbatim; four Spotify-specific facts
shape the deltas from splice's version:

  1. No dynamic client registration. Splice self-registers a client (RFC 7591);
     Spotify does not. You register ONE app by hand in the Developer Dashboard
     and its client_id becomes a config value — so register_client() is gone and
     `client_id` is required, not optional-with-fallback.
  2. Redirect host MUST be 127.0.0.1, never "localhost". Spotify explicitly
     rejects a `localhost` redirect URI (INVALID_CLIENT). Both the HTTPServer
     bind and the redirect_uri string use the loopback IP literal. Register the
     PORTLESS form (http://127.0.0.1/callback) in the Dashboard so the runtime
     can still pick a free port.
  3. Two hosts: accounts.spotify.com for /authorize + /api/token (here),
     api.spotify.com for everything else (client.py) — kept separate on purpose.
  4. PKCE means no client_secret is ever sent (token_endpoint_auth_method:none).

Spotify's docs warn a refresh response "might not include a new refresh token"
— the same guarantee splice's refresh() already defends against; that fallback
is kept intact.
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

_AUTHORIZE_EP = "https://accounts.spotify.com/authorize"
_TOKEN_EP = "https://accounts.spotify.com/api/token"

# Scopes for "catalog + own account" — private/collaborative playlist read,
# playlist create/modify, saved-track read/write, and top-items read (most-
# played artists/tracks). Playback-control scopes are deliberately omitted
# (Premium + Web Playback SDK only; useless headless).
_SCOPE = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public "
    "user-library-read user-library-modify "
    "user-top-read"
)

# Pick a port unlikely to collide with splice-cli's 8765. Register the portless
# loopback form in the Dashboard so any free port works at runtime (--port).
DEFAULT_REDIRECT_PORT = 8787
# Spotify's consent is a single login+approve screen (no Turnstile), so it needs
# far less room than splice's 1200s — but keep it generous and configurable.
DEFAULT_LOGIN_TIMEOUT = 300


class SpotifyAuthError(RuntimeError):
    """Base for anything that should send the user back to a browser login."""


@dataclass(frozen=True)
class SpotifySession:
    access_token: str
    refresh_token: str
    access_expiry: float  # unix ts; Spotify access tokens live ~1h

    def expired(self) -> bool:
        return time.time() >= self.access_expiry

    @property
    def auth_header(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.access_token}"}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _await_callback(port: int, timeout: int) -> tuple[str | None, str | None]:
    """Run a one-shot loopback HTTP server to catch the OAuth redirect.

    Constraint worth surfacing: this only works if the browser completing the
    login is on the *same machine* as this process — the redirect is a loopback
    URL, not reachable from a different machine or a remote/headless session.
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
        # 127.0.0.1, NOT "localhost" — Spotify rejects a localhost redirect URI.
        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    except OSError as err:
        raise SpotifyAuthError(
            f"Couldn't start the local OAuth callback server on port {port}: {err}. "
            "The port is likely already in use — maybe another `spotify login` is "
            "still running, or another program is bound to it. Pass `--port <n>` "
            "to `spotify login` to pick a different one."
        ) from err
    server.timeout = timeout
    server.handle_request()
    return result["code"], result["state"]


@dataclass(frozen=True)
class LoginChallenge:
    """The per-attempt secrets needed to finish a login: the URL the user opens,
    plus the PKCE verifier and CSRF state we check the redirect against."""

    authorize_url: str
    verifier: str
    state: str
    redirect_uri: str


def begin_login(*, client_id: str, redirect_port: int = DEFAULT_REDIRECT_PORT) -> LoginChallenge:
    """Build the authorize URL + PKCE/state for one login attempt. Shared by both
    the loopback-server flow (`login`) and the manual paste flow (`complete_login`)."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{redirect_port}/callback"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _SCOPE,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    return LoginChallenge(
        authorize_url=f"{_AUTHORIZE_EP}?{urllib.parse.urlencode(params)}",
        verifier=verifier,
        state=state,
        redirect_uri=redirect_uri,
    )


def login(
    *,
    client_id: str,
    redirect_port: int = DEFAULT_REDIRECT_PORT,
    timeout: int = DEFAULT_LOGIN_TIMEOUT,
    on_url: Callable[[str], None] = print,
) -> SpotifySession:
    """Full PKCE authorization-code flow via a 127.0.0.1 loopback redirect.

    `client_id` is required — it's the Dashboard-registered app's id, a config
    value (Spotify has no self-service registration). `on_url` receives the
    authorize URL to open (defaults to printing it); the CLI overrides it to
    render a panel. Blocks until the browser redirects back or `timeout` elapses.

    Needs the browser on the SAME machine (or an SSH `-L` tunnel to this port).
    When neither is possible, use the manual flow (`begin_login` + `complete_login`).
    """
    ch = begin_login(client_id=client_id, redirect_port=redirect_port)
    on_url(ch.authorize_url)

    code, returned_state = _await_callback(redirect_port, timeout)
    if not code:
        raise SpotifyAuthError(
            "No authorization code received — the browser login didn't complete "
            "in time, or was cancelled. Try again."
        )
    if returned_state != ch.state:
        raise SpotifyAuthError("OAuth state mismatch — aborting (possible tampering).")
    return _exchange_code(client_id, code, ch.redirect_uri, ch.verifier)


def complete_login(challenge: LoginChallenge, redirect_response: str, *, client_id: str) -> SpotifySession:
    """Finish a manual (no-server) login from what the user pasted back.

    Accepts either the FULL redirect URL from the browser's address bar
    (preferred — carries `state`, so we can verify it) or a bare `code` (works,
    but the CSRF `state` check is skipped; PKCE still protects the exchange).
    """
    code, returned_state = _parse_redirect_response(redirect_response)
    if not code:
        raise SpotifyAuthError(
            "Couldn't find an authorization code in what you pasted. Paste the full "
            "redirect URL from the browser's address bar (the http://127.0.0.1/... one)."
        )
    if returned_state is not None and returned_state != challenge.state:
        raise SpotifyAuthError("OAuth state mismatch — aborting (possible tampering).")
    return _exchange_code(client_id, code, challenge.redirect_uri, challenge.verifier)


def _parse_redirect_response(text: str) -> tuple[str | None, str | None]:
    """Pull (code, state) from either a full redirect URL/query string or a bare
    code. Returns (None, None) if no code is present; state is None for a bare code."""
    text = text.strip()
    if not text:
        return None, None
    if "code=" in text or "://" in text or text.startswith("/") or text.startswith("?"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        return qs.get("code", [None])[0], qs.get("state", [None])[0]
    return text, None  # a bare code was pasted — no state to check


def _exchange_code(client_id: str, code: str, redirect_uri: str, verifier: str) -> SpotifySession:
    resp = httpx.post(
        _TOKEN_EP,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise SpotifyAuthError(f"Token exchange failed (status {resp.status_code}): {resp.text}")
    return _session_from_token_response(resp.json())


def refresh(session: SpotifySession, *, client_id: str) -> SpotifySession:
    """Exchange the refresh token for a fresh access token (headless, no browser)."""
    resp = httpx.post(
        _TOKEN_EP,
        data={
            "grant_type": "refresh_token",
            "refresh_token": session.refresh_token,
            "client_id": client_id,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise SpotifyAuthError(
            f"Refresh failed (status {resp.status_code}): {resp.text}. Run `spotify login` again."
        )
    tok = resp.json()
    # Spotify's docs: the response "might not include a new refresh token."
    # Fall back to the current one rather than losing it.
    if not tok.get("refresh_token"):
        tok["refresh_token"] = session.refresh_token
    return _session_from_token_response(tok)


def _session_from_token_response(tok: dict) -> SpotifySession:
    return SpotifySession(
        access_token=tok["access_token"],
        refresh_token=tok["refresh_token"],
        access_expiry=time.time() + tok.get("expires_in", 3600),
    )
