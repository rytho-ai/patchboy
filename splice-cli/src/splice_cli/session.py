"""Session persistence — save/load a lifted session to a caller-owned path.

Kept tiny and separate so the CLI, an agent, and tests each point at their
own file. A session is just cookies + the UA they're bound to (+ metadata).
"""

from __future__ import annotations

import json
from pathlib import Path

from patchboy_core.atomicio import atomic_write_text

from .auth import Session


def save(session: Session, path: Path) -> None:
    """Write atomically (temp file + rename) via patchboy_core's
    atomic_write_text, same pattern as budget.py's save() — session cookies
    are as sensitive as money tokens, so this must never leave a
    world-readable window (write-then-chmod had one) or a corrupt file behind
    if the process dies mid-write.
    """
    atomic_write_text(
        path,
        json.dumps(
            {
                "cookies": session.cookies,
                "user_agent": session.user_agent,
                "clearance_expiry": session.clearance_expiry,
                "source": session.source,
                # access_token_expiry isn't stored: __post_init__ re-derives it
                # from the cookie's JWT on load, so it's never stale.
            }
        ),
    )


def load(path: Path) -> Session:
    data = json.loads(path.read_text())
    return Session(
        cookies=data["cookies"],
        user_agent=data["user_agent"],
        clearance_expiry=data.get("clearance_expiry"),
        source=data.get("source", "unknown"),
    )
