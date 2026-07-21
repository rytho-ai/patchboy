"""Atomic, permission-safe file writes — the one mechanic genuinely shared
across the workspace's session/budget persistence.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write `text` to `path` atomically, with `mode` set BEFORE the rename.

    Creates parent dirs, writes to a temp file, chmods it (0600 default — for
    secrets/tokens the file must never have a world-readable window), then
    os.replace()s it into place (atomic on POSIX — no partial-file window).
    Extracted verbatim from the pattern duplicated across spotify/applemusic/
    splice session + budget persistence.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    os.replace(tmp, path)
