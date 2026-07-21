"""Session persistence — save/load the token set to a caller-owned path.

Atomic write (temp file + chmod 0600 *before* rename) via patchboy_core's
atomic_write_text: write-then-chmod leaves a world-readable window, and a
mid-write crash leaves a corrupt file — tokens tolerate neither. The stored
payload is the two tokens + their metadata (dev-token expiry, storefront); the
media-user-token may be absent (catalog-only, never logged in).
"""

from __future__ import annotations

import json
from pathlib import Path

from patchboy_core.atomicio import atomic_write_text

from .auth import AppleMusicSession


def save(session: AppleMusicSession, path: Path) -> None:
    atomic_write_text(
        path,
        json.dumps(
            {
                "developer_token": session.developer_token,
                "developer_token_expiry": session.developer_token_expiry,
                "storefront": session.storefront,
                "media_user_token": session.media_user_token,
            }
        ),
    )


def load(path: Path) -> AppleMusicSession:
    data = json.loads(path.read_text())
    return AppleMusicSession(
        developer_token=data["developer_token"],
        developer_token_expiry=data["developer_token_expiry"],
        storefront=data.get("storefront", "us"),
        media_user_token=data.get("media_user_token"),
    )
