"""Threads OAuth — Authorization Code, MANUAL paste-back only (no PKCE, no
loopback server).

Adapted from sibling splice-cli's mcp_auth.py / spotify-cli's auth.py shape
(`LoginChallenge` + `begin_login()` / `complete_login()` +
`_parse_redirect_response()`), but Threads changes two load-bearing facts
that shape every delta from those siblings:

  1. Meta does NOT support a localhost/loopback redirect URI for a Threads
     app — only registered https URIs. So there is no `login()` blocking
     function and no `_await_callback` loopback HTTPServer at all here (unlike
     spotify-cli's default server mode). The MANUAL flow (`begin_login` +
     `complete_login`) is the ONLY flow: the user approves in a browser, the
     browser is redirected to the registered https URI (which will typically
     fail to load — that page doesn't exist — or 404; that's expected), and
     they paste the full resulting URL back.
  2. Threads uses a plain Authorization Code exchange with a `client_secret`
     — there is no PKCE (`code_verifier`/`code_challenge`). `client_id` +
     `client_secret` are both required, registered-app config values (the
     Threads-specific App ID/Secret from the Meta App Dashboard's "Threads"
     use case — distinct from the parent Facebook app's).

Three hops turn an authorization `code` into something durable enough to
avoid a human step on every run:

  code --(exchange)--> short-lived access_token (~1h, has user_id)
       --(th_exchange_token)--> long-lived access_token (~60 days)

`refresh()` (grant_type=th_refresh_token) extends a long-lived token for
another ~60 days, but per Meta's docs only works on a token that is already
≥24h old and not yet expired — an expired token cannot be refreshed, only
re-logged-in. `_live_client` in cli.py respects that: it never tries to
refresh an already-expired session.

CAVEAT: the long-lived-exchange and refresh endpoints' exact param shapes
were given as the "standard Meta pattern" but not independently re-confirmed
against the live long-lived-tokens doc — both are marked `# verify-live`
below. The token-exchange response field names (`access_token`, `user_id`,
`expires_in`) are also unverified against a real Threads app response and
carry the same marker.
"""

from __future__ import annotations

import secrets
import time
import urllib.parse
from dataclasses import dataclass

import httpx

_AUTHORIZE_EP = "https://threads.net/oauth/authorize"
_TOKEN_EP = "https://graph.threads.net/oauth/access_token"
_LONG_LIVED_EP = "https://graph.threads.net/access_token"  # verify-live
_REFRESH_EP = "https://graph.threads.net/refresh_access_token"  # verify-live

# Confirmed on the Threads get-started docs page. threads_basic is required
# for everything else; the rest are additive per-capability scopes.
DEFAULT_SCOPES = (
    "threads_basic",
    "threads_content_publish",
    "threads_read_replies",
    "threads_manage_replies",
    "threads_manage_insights",
)

# Meta rejects a localhost/loopback redirect URI outright — only a registered
# https URI works. "https://localhost/" is a placeholder a user can register
# without standing up real infra (the redirected-to page need not exist; the
# code arrives in the URL regardless of whether the page loads).
DEFAULT_REDIRECT_URI = "https://localhost/"

# A long-lived token lives ~60 days; treat it as "near expiry" inside the
# last week so `status`/`_live_client` can flag it before it goes stale.
NEAR_EXPIRY_WINDOW = 7 * 24 * 3600


class ThreadsAuthError(RuntimeError):
    """Base for anything that should send the user back to a browser login."""


@dataclass(frozen=True)
class ThreadsSession:
    access_token: str
    user_id: str
    access_expiry: float  # unix ts — the LONG-LIVED token's expiry (~60 days out)

    def expired(self) -> bool:
        return time.time() >= self.access_expiry

    def near_expiry(self, *, window: float = NEAR_EXPIRY_WINDOW) -> bool:
        return not self.expired() and (self.access_expiry - time.time()) <= window

    @property
    def params(self) -> dict[str, str]:
        """Threads/Graph API auth convention: access_token travels as a query
        param, not an Authorization header. Caged here so client.py never has
        to know that."""
        return {"access_token": self.access_token}


@dataclass(frozen=True)
class LoginChallenge:
    """The per-attempt state needed to finish a login: the URL the user opens,
    the CSRF `state` we verify the redirect against, and the exact
    `redirect_uri` used in the authorize URL (must match byte-for-byte in the
    token exchange). No PKCE verifier — Threads doesn't use it."""

    authorize_url: str
    state: str
    redirect_uri: str


def begin_login(
    *,
    client_id: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> LoginChallenge:
    """Build the authorize URL + CSRF state for one login attempt. Doesn't
    block on anything — the caller prints the URL, the user approves in their
    own browser, and `complete_login` finishes it from what they paste back."""
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(scopes),
        "response_type": "code",
        "state": state,
    }
    return LoginChallenge(
        authorize_url=f"{_AUTHORIZE_EP}?{urllib.parse.urlencode(params)}",
        state=state,
        redirect_uri=redirect_uri,
    )


def complete_login(
    challenge: LoginChallenge,
    redirect_response: str,
    *,
    client_id: str,
    client_secret: str,
) -> ThreadsSession:
    """Finish the manual (paste-back) login from what the user pasted back.

    Accepts either the FULL redirect URL from the browser's address bar
    (preferred — carries `state`, so we can verify it) or a bare `code`
    (works, but the CSRF `state` check is skipped). Performs both hops: code
    -> short-lived token -> long-lived token, and returns a session already
    holding the long-lived one.
    """
    code, returned_state = _parse_redirect_response(redirect_response)
    if not code:
        raise ThreadsAuthError(
            "Couldn't find an authorization code in what you pasted. Paste the full "
            "redirect URL from the browser's address bar (it has ?code=...&state=...) — "
            "even if that page failed to load, the URL itself is what matters."
        )
    if returned_state is not None and returned_state != challenge.state:
        raise ThreadsAuthError("OAuth state mismatch — aborting (possible tampering).")

    short_token, user_id = _exchange_code(client_id, client_secret, code, challenge.redirect_uri)
    long_token, expires_in = _exchange_long_lived(client_secret, short_token)
    return ThreadsSession(
        access_token=long_token,
        user_id=user_id,
        access_expiry=time.time() + expires_in,
    )


def _parse_redirect_response(text: str) -> tuple[str | None, str | None]:
    """Pull (code, state) from either a full redirect URL/query string or a
    bare code. Returns (None, None) if no code is present; state is None for
    a bare code."""
    text = text.strip()
    if not text:
        return None, None
    if "code=" in text or "://" in text or text.startswith("/") or text.startswith("?"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        return qs.get("code", [None])[0], qs.get("state", [None])[0]
    return text, None  # a bare code was pasted — no state to check


def _exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> tuple[str, str]:
    """POST the auth code for a short-lived access_token. Returns (access_token, user_id)."""
    resp = httpx.post(
        _TOKEN_EP,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise ThreadsAuthError(f"Token exchange failed (status {resp.status_code}): {resp.text}")
    data = resp.json()
    try:
        return data["access_token"], str(data["user_id"])
    except KeyError as err:
        raise ThreadsAuthError(f"Token exchange response missing expected field: {data}") from err


def _exchange_long_lived(client_secret: str, short_lived_token: str) -> tuple[str, int]:
    """GET the short-lived token exchanged for a long-lived one (~60 days).
    verify-live: param names (`grant_type=th_exchange_token`) follow the
    standard Meta long-lived-token pattern but weren't re-confirmed live."""
    resp = httpx.get(
        _LONG_LIVED_EP,
        params={
            "grant_type": "th_exchange_token",
            "client_secret": client_secret,
            "access_token": short_lived_token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise ThreadsAuthError(f"Long-lived token exchange failed (status {resp.status_code}): {resp.text}")
    data = resp.json()
    try:
        return data["access_token"], int(data.get("expires_in", 60 * 24 * 3600))
    except KeyError as err:
        raise ThreadsAuthError(f"Long-lived exchange response missing expected field: {data}") from err


def refresh(session: ThreadsSession) -> ThreadsSession:
    """Extend a long-lived token for another ~60 days. Per Meta's docs this
    only works on a token that is ≥24h old and NOT YET expired — an expired
    token can't be refreshed, only re-logged-in (the caller is responsible for
    not calling this on an expired session; see cli.py's `_live_client`).
    verify-live: param names (`grant_type=th_refresh_token`) follow the
    standard Meta pattern but weren't re-confirmed live."""
    resp = httpx.get(
        _REFRESH_EP,
        params={"grant_type": "th_refresh_token", "access_token": session.access_token},
        timeout=15,
    )
    if resp.status_code != 200:
        raise ThreadsAuthError(
            f"Refresh failed (status {resp.status_code}): {resp.text}. Run `threads login` again."
        )
    data = resp.json()
    try:
        new_token = data["access_token"]
        expires_in = int(data.get("expires_in", 60 * 24 * 3600))
    except KeyError as err:
        raise ThreadsAuthError(f"Refresh response missing expected field: {data}") from err
    return ThreadsSession(
        access_token=new_token,
        user_id=session.user_id,
        access_expiry=time.time() + expires_in,
    )
