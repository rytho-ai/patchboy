"""MCP session persistence — save/load an OAuth token set to a caller-owned
path. Mirrors session.py's shape; kept separate because the two auth systems
(cookie-lifted GraphQL session vs OAuth MCP session) are independent — an
agent, the CLI, and tests each point at their own file for each.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .mcp_auth import McpSession


def save(session: McpSession, path: Path) -> None:
    """Write atomically (temp file + rename), same pattern as budget.py's
    save() — write-then-chmod left a world-readable window (and a corrupt
    file on a mid-write crash); OAuth tokens can't tolerate either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "client_id": session.client_id,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "access_expiry": session.access_expiry,
            }
        )
    )
    tmp.chmod(0o600)  # OAuth tokens — keep it user-only, set before the rename
    os.replace(tmp, path)  # atomic on POSIX — no partial-file/world-readable window


def load(path: Path) -> McpSession:
    data = json.loads(path.read_text())
    return McpSession(
        client_id=data["client_id"],
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        access_expiry=data["access_expiry"],
    )
