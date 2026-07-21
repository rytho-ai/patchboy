"""Session persistence — save/load the OAuth token set to a caller-owned path.

Atomic write (temp file + chmod 0600 *before* rename), copied verbatim from
sibling splice-cli's mcp_session.py: write-then-chmod leaves a world-readable
window, and a mid-write crash leaves a corrupt file — OAuth tokens tolerate
neither. Unlike splice we don't persist client_id (it's a static config value,
not per-registration), so the stored payload is just the token triple.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .auth import SpotifySession


def save(session: SpotifySession, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "access_expiry": session.access_expiry,
            }
        )
    )
    tmp.chmod(0o600)  # OAuth tokens — user-only, set before the rename
    os.replace(tmp, path)  # atomic on POSIX — no partial-file/world-readable window


def load(path: Path) -> SpotifySession:
    data = json.loads(path.read_text())
    return SpotifySession(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        access_expiry=data["access_expiry"],
    )
