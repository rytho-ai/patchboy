"""MCP session persistence — save/load an OAuth token set to a caller-owned
path. Mirrors session.py's shape; kept separate because the two auth systems
(cookie-lifted GraphQL session vs OAuth MCP session) are independent — an
agent, the CLI, and tests each point at their own file for each.
"""

from __future__ import annotations

import json
from pathlib import Path

from patchboy_core.atomicio import atomic_write_text

from .mcp_auth import McpSession


def save(session: McpSession, path: Path) -> None:
    """Write atomically (temp file + rename) via patchboy_core's
    atomic_write_text, same pattern as budget.py's save() — write-then-chmod
    left a world-readable window (and a corrupt file on a mid-write crash);
    OAuth tokens can't tolerate either.
    """
    atomic_write_text(
        path,
        json.dumps(
            {
                "client_id": session.client_id,
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "access_expiry": session.access_expiry,
            }
        ),
    )


def load(path: Path) -> McpSession:
    data = json.loads(path.read_text())
    return McpSession(
        client_id=data["client_id"],
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        access_expiry=data["access_expiry"],
    )
