"""CAGED — lift the media-user-token cookie from a logged-in browser.

The "log in yourself" path, mirroring splice-cli's firefox.py/chrome.py: you
sign in to music.apple.com in your NORMAL browser (your Apple ID, as usual), and
this reads the `media-user-token` cookie straight from that browser's cookie
store. No DevTools copy-paste, no browser automation, no MusicKit loopback (which
the borrowed, origin-locked developer token can't drive).

Two cases, same as splice:
  - Firefox — cookies live in an UNENCRYPTED sqlite; a plain read, zero deps.
  - Chromium family (Chrome/Chromium/Brave/Edge) — values are AES-encrypted with
    a key in the OS keyring; `browser_cookie3` does that decryption (optional
    dependency: `pip install 'apple-music-cli[chrome]'`).

Verified live 2026-07-21: the token IS a real cookie on `.music.apple.com`
(len ~246), liftable this way; a lifted token authed a real `/me/storefront`
call. (The account still needs an active Apple Music subscription for library
DATA — that's an account tier, not an auth problem; see client.py.)
"""

from __future__ import annotations

import configparser
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .auth import AppleMusicAuthError

_COOKIE = "media-user-token"
# Prefer the music.apple.com copy (the same value is also set on
# podcasts/tv/apps.apple.com; music is the canonical one for this API).
_PREFERRED_HOST = "music.apple.com"

_FIREFOX_ROOTS = [
    Path.home() / "snap/firefox/common/.mozilla/firefox",
    Path.home() / ".mozilla/firefox",
]
# browser -> browser_cookie3 loader attribute (Chromium family).
_CHROMIUM = {"chrome": "chrome", "chromium": "chromium", "brave": "brave", "edge": "edge"}
# Auto-detect order: Firefox first (no keyring prompt), then Chromium family.
SUPPORTED = ("firefox", "chrome", "chromium", "brave", "edge")


class NoBrowserSessionError(AppleMusicAuthError):
    """No logged-in music.apple.com session found in the requested browser."""


def extract_media_user_token(browser: str = "auto") -> str:
    """Lift the `media-user-token` from a logged-in browser.

    `browser="auto"` tries each supported browser and returns the first hit; a
    specific name targets just that one. Raises NoBrowserSessionError if none has
    a logged-in music.apple.com session (→ the CLI tells you to log in there)."""
    if browser == "auto":
        for name in SUPPORTED:
            try:
                return extract_media_user_token(name)
            except AppleMusicAuthError:
                continue
        raise NoBrowserSessionError(
            "No logged-in music.apple.com session found in any browser "
            f"({', '.join(SUPPORTED)}). Sign in to music.apple.com in your browser first."
        )
    if browser == "firefox":
        return _from_firefox()
    if browser in _CHROMIUM:
        return _from_chromium(browser)
    raise AppleMusicAuthError(f"Unknown browser {browser!r}. Supported: {', '.join(SUPPORTED)}.")


# ---- Firefox: plain unencrypted sqlite read --------------------------------


def _find_firefox_profile() -> Path:
    """Locate a Firefox profile that has a cookies.sqlite (default preferred)."""
    for root in _FIREFOX_ROOTS:
        if not root.exists():
            continue
        ini = root / "profiles.ini"
        if ini.exists():
            cp = configparser.ConfigParser()
            cp.read(ini)
            for section in cp.sections():
                path = cp[section].get("Path")
                if path and cp[section].get("Default") == "1":
                    cand = Path(path) if path.startswith("/") else root / path
                    if (cand / "cookies.sqlite").exists():
                        return cand
        for cand in sorted(root.glob("*.default*")):
            if (cand / "cookies.sqlite").exists():
                return cand
    raise NoBrowserSessionError("No Firefox profile with a cookies.sqlite was found.")


def _from_firefox() -> str:
    db = _find_firefox_profile() / "cookies.sqlite"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copy2(db, copy)  # copy so a running Firefox's lock doesn't block us
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                shutil.copy2(side, copy.with_name(copy.name + suffix))
        con = sqlite3.connect(copy)
        try:
            rows = con.execute(
                "SELECT value, host, expiry FROM moz_cookies WHERE name = ? AND host LIKE '%apple%'",
                (_COOKIE,),
            ).fetchall()
        finally:
            con.close()
    return _pick(rows) or _raise("firefox")


def _pick(rows: list[tuple]) -> str | None:
    """Choose the token: prefer the music.apple.com host, else the freshest expiry."""
    if not rows:
        return None
    music = [r for r in rows if _PREFERRED_HOST in (r[1] or "")]
    pool = music or rows
    value, _host, _exp = max(pool, key=lambda r: r[2] or 0)
    return value or None


# ---- Chromium family: browser_cookie3 handles the keyring decryption -------


def _from_chromium(browser: str) -> str:
    try:
        import browser_cookie3
    except ImportError as exc:
        raise AppleMusicAuthError(
            f"--browser {browser} needs browser_cookie3: pip install 'apple-music-cli[chrome]' "
            "(Firefox needs no extra dependency)."
        ) from exc
    loader = getattr(browser_cookie3, _CHROMIUM[browser])
    try:
        jar = loader(domain_name="apple.com")
    except Exception as exc:  # browser_cookie3 raises its own error types
        raise NoBrowserSessionError(
            f"Couldn't read {browser} cookies ({exc}). Is your login keyring unlocked?"
        ) from exc
    rows = [(c.value, c.domain, c.expires or 0) for c in jar if c.name == _COOKIE]
    return _pick(rows) or _raise(browser)


def _raise(browser: str) -> str:  # -> NoReturn, but typed str for the `or` chains above
    raise NoBrowserSessionError(
        f"No `{_COOKIE}` cookie found in {browser}. Sign in to music.apple.com in {browser} "
        "(and start playing something so MusicKit sets the cookie), then try again."
    )
