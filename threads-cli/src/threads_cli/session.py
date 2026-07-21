"""Session persistence — save/load the OAuth token set to a caller-owned path.

Atomic write (temp file + chmod 0600 *before* rename) via patchboy_core's
atomic_write_text: write-then-chmod leaves a world-readable window, and a
mid-write crash leaves a corrupt file — OAuth tokens tolerate neither. Same
shape as sibling spotify-cli/applemusic-cli's session.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from patchboy_core.atomicio import atomic_write_text

from .auth import ThreadsSession


def save(session: ThreadsSession, path: Path) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {
                "access_token": session.access_token,
                "user_id": session.user_id,
                "access_expiry": session.access_expiry,
            }
        ),
    )


def load(path: Path) -> ThreadsSession:
    data = json.loads(path.read_text())
    return ThreadsSession(
        access_token=data["access_token"],
        user_id=data["user_id"],
        access_expiry=data["access_expiry"],
    )
