"""Session persistence — save/load the token set to a caller-owned path.

Atomic write (temp file + chmod 0600 *before* rename), same discipline as
sibling spotify-cli: write-then-chmod leaves a world-readable window, and a
mid-write crash leaves a corrupt file — tokens tolerate neither. The stored
payload is the two tokens + their metadata (dev-token expiry, storefront); the
media-user-token may be absent (catalog-only, never logged in).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .auth import AppleMusicSession


def save(session: AppleMusicSession, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "developer_token": session.developer_token,
                "developer_token_expiry": session.developer_token_expiry,
                "storefront": session.storefront,
                "media_user_token": session.media_user_token,
            }
        )
    )
    tmp.chmod(0o600)  # tokens — user-only, set before the rename
    os.replace(tmp, path)  # atomic on POSIX — no partial-file/world-readable window


def load(path: Path) -> AppleMusicSession:
    data = json.loads(path.read_text())
    return AppleMusicSession(
        developer_token=data["developer_token"],
        developer_token_expiry=data["developer_token_expiry"],
        storefront=data.get("storefront", "us"),
        media_user_token=data.get("media_user_token"),
    )
