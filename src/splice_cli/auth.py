"""Auth — the browser-agnostic session type, errors, and the source dispatcher.

A Splice session is a lifted set of cookies plus the User-Agent they're bound
to (Cloudflare's `cf_clearance` is UA-bound). Where those cookies come from —
Firefox, Chrome, Chromium, Brave, Edge — is a detail behind `extract_session`.

Splice's login is Turnstile-gated, so there is no headless login here: a human
logs in once in their normal browser, and we lift the result. The typed errors
below are how the CLI knows when to hand back to the human.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

# The cookie that proves you're logged in. It's also an Auth0 JWT access
# token — sent as `Authorization: Bearer <value>` for anything user-scoped
# (licensed-sample checks, downloads). Cookie-only auth is enough for public
# search, but not for "who is this user" queries.
AUTH_COOKIE = "_splice_token_prod"
# The Cloudflare clearance cookie — UA-bound, and long-lived (~1 year).
CLEARANCE_COOKIE = "cf_clearance"


def _jwt_exp(token: str) -> float | None:
    """Decode a JWT's `exp` claim without verifying the signature.

    We only need the expiry to warn the user — Splice's own server verifies
    the signature on every request.
    """
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("exp")
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


class AuthError(RuntimeError):
    """Base for anything that should send the user back to their browser."""


class NoSessionError(AuthError):
    """No logged-in Splice session found in any browser."""


class SessionExpired(AuthError):
    """We had a session but Splice rejected it (cleared/expired cookies)."""


@dataclass(frozen=True)
class Session:
    cookies: dict[str, str]
    user_agent: str
    # Unix ts (seconds) when cf_clearance expires, if known — lasts ~1 year.
    clearance_expiry: float | None = None
    # Which browser this came from, for messaging.
    source: str = "unknown"
    # Unix ts (seconds) when the _splice_token_prod JWT expires — lasts only
    # ~1 hour. This, not clearance_expiry, is what gates user-scoped calls
    # (licensed-sample checks, download). Derived automatically below.
    access_token_expiry: float | None = field(default=None)

    def __post_init__(self) -> None:
        if AUTH_COOKIE not in self.cookies:
            raise NoSessionError(
                "Lifted cookies contain no Splice auth token — not logged in."
            )
        if self.access_token_expiry is None:
            exp = _jwt_exp(self.cookies[AUTH_COOKIE])
            if exp is not None:
                object.__setattr__(self, "access_token_expiry", exp)

    @property
    def auth_header(self) -> dict[str, str]:
        """Authorization header required for any user-scoped GraphQL call."""
        return {"authorization": f"Bearer {self.cookies[AUTH_COOKIE]}"}


# Browsers we know how to lift from, in auto-detect priority order.
SUPPORTED = ("firefox", "chrome", "chromium", "brave", "edge")


def extract_session(browser: str = "auto") -> Session:
    """Lift a logged-in Splice session from a browser.

    browser="auto" tries each supported browser and returns the first with a
    valid Splice login. A specific name targets just that browser.
    """
    from . import chrome, firefox

    def _try(name: str) -> Session | None:
        try:
            if name == "firefox":
                return firefox.extract()
            return chrome.extract(name)
        except AuthError:
            return None

    if browser != "auto":
        if browser not in SUPPORTED:
            raise AuthError(f"Unknown browser {browser!r}. Supported: {SUPPORTED}")
        sess = _try(browser)
        if sess is None:
            raise NoSessionError(
                f"No logged-in Splice session found in {browser}. "
                f"Log into splice.com in {browser} first."
            )
        return sess

    for name in SUPPORTED:
        sess = _try(name)
        if sess is not None:
            return sess
    raise NoSessionError(
        "No logged-in Splice session found in any browser "
        f"({', '.join(SUPPORTED)}). Log into splice.com in your browser first."
    )
