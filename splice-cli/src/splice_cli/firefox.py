"""Firefox session source — the easy case.

On Linux, Firefox stores cookies unencrypted in a SQLite file, so lifting a
session is a plain read: no keyring, no decryption, no browser automation.
Returns a Session (cookies + the matching Firefox UA + cf_clearance expiry).
"""

from __future__ import annotations

import configparser
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .auth import CLEARANCE_COOKIE, NoSessionError, Session

_PROFILE_ROOTS = [
    Path.home() / "snap/firefox/common/.mozilla/firefox",
    Path.home() / ".mozilla/firefox",
]


def find_profile() -> Path:
    """Locate the default Firefox profile that has a cookies DB."""
    for root in _PROFILE_ROOTS:
        if not root.exists():
            continue
        ini = root / "profiles.ini"
        if ini.exists():
            cp = configparser.ConfigParser()
            cp.read(ini)
            for section in cp.sections():
                path = cp[section].get("Path")
                is_default = cp[section].get("Default") == "1"
                if path and is_default:
                    cand = (root / path) if not path.startswith("/") else Path(path)
                    if (cand / "cookies.sqlite").exists():
                        return cand
        for cand in sorted(root.glob("*.default*")):
            if (cand / "cookies.sqlite").exists():
                return cand
    raise NoSessionError("No Firefox profile with cookies.sqlite found.")


def user_agent() -> str:
    """Build the UA Firefox sends, matching cf_clearance's binding."""
    for ini in Path("/snap/firefox").glob("**/application.ini"):
        cp = configparser.ConfigParser()
        cp.read(ini)
        version = cp.get("App", "Version", fallback=None)
        if version:
            major = version.split(".")[0]
            return (
                f"Mozilla/5.0 (X11; Linux x86_64; rv:{major}.0) "
                f"Gecko/20100101 Firefox/{major}.0"
            )
    return "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def extract(profile: Path | None = None) -> Session:
    """Read Splice cookies + UA from a Firefox profile."""
    profile = profile or find_profile()
    db = profile / "cookies.sqlite"
    if not db.exists():
        raise NoSessionError(f"No cookies.sqlite in {profile}")

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
                "SELECT name, value, expiry FROM moz_cookies WHERE host LIKE '%splice%'"
            ).fetchall()
        finally:
            con.close()

    # A cookie name can appear on several Splice subdomains, some stale. Keep
    # the freshest (max expiry) value per name. Firefox's expiry is in seconds,
    # but this profile stores milliseconds — normalize anything implausibly
    # large (> year 5138) down to seconds.
    def _secs(raw: float) -> float:
        return raw / 1000 if raw > 1e11 else raw

    freshest: dict[str, tuple[str, float]] = {}
    for name, value, exp in rows:
        exp_s = _secs(float(exp)) if exp else 0.0
        if name not in freshest or exp_s > freshest[name][1]:
            freshest[name] = (value, exp_s)
    cookies = {name: value for name, (value, _) in freshest.items()}
    clearance_expiry = (
        freshest[CLEARANCE_COOKIE][1] if CLEARANCE_COOKIE in freshest else None
    )
    # Session.__post_init__ raises NoSessionError if the auth cookie is missing.
    return Session(
        cookies=cookies,
        user_agent=user_agent(),
        clearance_expiry=clearance_expiry,
        source="firefox",
    )
