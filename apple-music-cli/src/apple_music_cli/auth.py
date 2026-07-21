"""CAGED auth — the borrowed-token flow that stands in for OAuth.

Apple Music has NO PKCE/loopback flow (that's Path A: a paid Apple Developer
membership signs a JWT with a .p8 key, then MusicKit JS issues a user token).
This module is Path B — the splice-cli reverse-engineering lineage — and it
needs no membership. Two tokens reach `api.music.apple.com`, obtained two ways:

  1. DEVELOPER TOKEN (`Authorization: Bearer …`). Required on EVERY request,
     catalog included. We do NOT sign one — we scrape the *public* token the
     music.apple.com web player already carries in its JS bundle. It's Apple's
     own web-app JWT (not tied to any account), valid ~6 months; `exp` is read
     straight from the JWT so the client knows when to re-scrape. No login, no
     user action — `fetch_developer_token()` just works.
  2. MEDIA-USER-TOKEN (`Media-User-Token: …`). Required only for `/v1/me/*`
     (the user's library). This one IS per-user: it's the cookie Apple sets when
     you sign in to music.apple.com. There is no way to mint it headlessly — the
     user logs in ONCE in their own browser, copies the cookie, and pastes it
     (the single human-in-the-loop gate, mirroring spotify-cli's login).

CAVEAT (verify-live): the scrape below targets the current music.apple.com
bundle layout, which Apple reshapes without notice. If `fetch_developer_token`
starts returning nothing, the bundle URL/regex is what moved — fix it here; no
caller above this module knows the token has a source at all.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass

import httpx

# Where the web player lives; the landing page references the JS bundle that
# embeds the developer token. `/us/browse` is stable and light.
_WEB_PLAYER = "https://music.apple.com/us/browse"
_WEB_ORIGIN = "https://music.apple.com"

# Verified live 2026-07-21: the borrowed web-app developer token is bound to the
# web player's origin — api.music.apple.com returns 401 for ANY request (catalog
# included) that omits this header, and 200 with it. Every request must carry it;
# it travels with the token, so it lives on the session's header properties.
_ORIGIN_HEADER = {"origin": _WEB_ORIGIN}

# A JWT is three base64url segments split by dots, and Apple's start "eyJ"
# (base64 of `{"`). Used to pluck the token out of a minified bundle.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
# Script bundles the landing page pulls in; the token lives in one of them.
_ASSET_RE = re.compile(r'/assets/[^"\']+?\.js')

# A borrowed developer token is long-lived (~6 months). Re-scrape this many
# seconds BEFORE the JWT's own `exp`, so we never race an expiry mid-command.
_DEV_TOKEN_SKEW = 60 * 60 * 24  # one day


class AppleMusicAuthError(RuntimeError):
    """Base for anything that should send the user back to a browser step —
    either 'couldn't obtain a developer token' or 'media-user-token dead/absent'."""


@dataclass(frozen=True)
class AppleMusicSession:
    """The two tokens plus the storefront, persisted by session.py.

    `media_user_token` is optional: catalog commands work without it; only
    `/me/*` (library) needs it. `developer_token_expiry` is the JWT's own `exp`
    (unix seconds) so a stale token is re-scraped, not blindly reused."""

    developer_token: str
    developer_token_expiry: float
    storefront: str = "us"
    media_user_token: str | None = None

    def dev_token_expired(self) -> bool:
        return time.time() >= (self.developer_token_expiry - _DEV_TOKEN_SKEW)

    @property
    def catalog_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.developer_token}", **_ORIGIN_HEADER}

    @property
    def user_headers(self) -> dict[str, str]:
        """Headers for a `/me/*` call. Raises if there's no media-user-token —
        the caller (client) turns that into the exit-2 login handoff."""
        if not self.media_user_token:
            raise AppleMusicAuthError(
                "This is a personal-library request but you're not logged in "
                "(no media-user-token). Run `apple-music login`."
            )
        return {
            "authorization": f"Bearer {self.developer_token}",
            "media-user-token": self.media_user_token,
            **_ORIGIN_HEADER,
        }


def _jwt_exp(token: str) -> float:
    """Read the `exp` claim from a JWT without verifying its signature (we don't
    hold the key — it's Apple's). Base64url-decode the middle segment. Falls back
    to 'expires in ~90 days' if the claim is missing/unparseable, so a scrape that
    yields a usable token isn't discarded over a metadata quirk."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:
        pass
    return time.time() + 60 * 60 * 24 * 90


def fetch_developer_token(*, timeout: int = 15) -> tuple[str, float]:
    """Scrape the public developer token from the music.apple.com web player.

    Returns (token, exp_unix). The landing page names one or more `/assets/*.js`
    bundles; one of them embeds the JWT. We fetch the page, then the bundles in
    order, returning the first JWT found. Raises AppleMusicAuthError if none of
    them yields one (→ the bundle layout moved; fix the regexes above).
    """
    with httpx.Client(timeout=timeout, follow_redirects=True) as http:
        try:
            page = http.get(_WEB_PLAYER)
        except httpx.HTTPError as err:
            raise AppleMusicAuthError(
                f"Couldn't reach the Apple Music web player to obtain a developer token: {err}"
            ) from err

        # Fast path: sometimes the token is inlined in the page itself.
        inline = _JWT_RE.search(page.text)
        if inline:
            tok = inline.group(0)
            return tok, _jwt_exp(tok)

        # De-dupe asset paths while preserving order; the index/main bundle is
        # usually first, so the token is typically found on the first fetch.
        seen: set[str] = set()
        ordered: list[str] = []
        for path in _ASSET_RE.findall(page.text):
            if path not in seen:
                seen.add(path)
                ordered.append(path)
        for path in ordered:
            try:
                bundle = http.get(_WEB_ORIGIN + path)
            except httpx.HTTPError:
                continue
            found = _JWT_RE.search(bundle.text)
            if found:
                tok = found.group(0)
                return tok, _jwt_exp(tok)

    raise AppleMusicAuthError(
        "Couldn't find a developer token in the music.apple.com web player. "
        "Apple likely reshaped its JS bundle — the scrape in auth.py needs updating "
        "(this is the marked verify-live risk of the borrowed-token approach)."
    )
