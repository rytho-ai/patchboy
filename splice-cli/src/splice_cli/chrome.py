"""Chromium-family session source — the hard case.

Unlike Firefox, Chrome/Chromium/Brave/Edge encrypt cookie values on disk
(AES, key held in the OS keyring via Secret Service; a hardcoded "peanuts"
key if no keyring). We lean on `browser_cookie3` to do that decryption rather
than reimplement the keyring dance.

Caveat: reading the keyring may require your desktop login keyring to be
unlocked, and can prompt. If it fails, the fallback is to relaunch the browser
with --remote-debugging-port and lift via CDP (see docs/reconnaissance.md).
"""

from __future__ import annotations

import shutil
import subprocess

from .auth import CLEARANCE_COOKIE, AuthError, NoSessionError, Session

# browser name -> (browser_cookie3 loader attr, candidate binaries for UA)
_BROWSERS = {
    "chrome": ("chrome", ("google-chrome", "google-chrome-stable")),
    "chromium": ("chromium", ("chromium", "chromium-browser")),
    "brave": ("brave", ("brave-browser", "brave")),
    "edge": ("edge", ("microsoft-edge", "microsoft-edge-stable")),
}


def user_agent(browser: str = "chrome") -> str:
    """Construct the Chromium UA, matching cf_clearance's binding.

    Chrome freezes the UA's minor version to 0.0.0, so we only need the major.
    """
    _, binaries = _BROWSERS[browser]
    major = "126"  # sane fallback if the binary won't report a version
    for binary in binaries:
        if shutil.which(binary):
            try:
                out = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=10
                ).stdout
                digits = "".join(c if c.isdigit() else " " for c in out).split()
                if digits:
                    major = digits[0]
                    break
            except (subprocess.SubprocessError, OSError):
                continue
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def extract(browser: str = "chrome") -> Session:
    """Lift Splice cookies from a Chromium-family browser via browser_cookie3."""
    if browser not in _BROWSERS:
        raise AuthError(f"{browser!r} is not a Chromium-family browser.")
    try:
        import browser_cookie3
    except ImportError as exc:
        raise AuthError(
            "Chrome support needs browser_cookie3: pip install 'splice-cli[chrome]'"
        ) from exc

    loader = getattr(browser_cookie3, _BROWSERS[browser][0])
    try:
        jar = loader(domain_name="splice.com")
    except Exception as exc:  # browser_cookie3 raises its own error types
        raise NoSessionError(
            f"Could not read {browser} cookies ({exc}). Is the browser closed "
            "and your login keyring unlocked?"
        ) from exc

    cookies = {c.name: c.value for c in jar}
    clearance_expiry = next(
        (float(c.expires) for c in jar if c.name == CLEARANCE_COOKIE and c.expires),
        None,
    )
    return Session(
        cookies=cookies,
        user_agent=user_agent(browser),
        clearance_expiry=clearance_expiry,
        source=browser,
    )
