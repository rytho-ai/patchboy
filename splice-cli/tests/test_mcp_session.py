"""MCP session persistence — save/load round-trip and file permissions."""

from __future__ import annotations

import stat
from pathlib import Path

from splice_cli import mcp_session
from splice_cli.mcp_auth import McpSession


def test_save_load_round_trip(tmp_path: Path):
    path = tmp_path / "mcp-session.json"
    original = McpSession(
        client_id="client-abc",
        access_token="access-xyz",
        refresh_token="refresh-123",
        access_expiry=1234567890.0,
    )
    mcp_session.save(original, path)
    loaded = mcp_session.load(path)

    assert loaded.client_id == original.client_id
    assert loaded.access_token == original.access_token
    assert loaded.refresh_token == original.refresh_token
    assert loaded.access_expiry == original.access_expiry


def test_save_sets_owner_only_permissions(tmp_path: Path):
    path = tmp_path / "mcp-session.json"
    mcp_session.save(
        McpSession(client_id="c", access_token="a", refresh_token="r", access_expiry=0.0),
        path,
    )
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_save_is_atomic_no_leftover_temp_file(tmp_path: Path):
    # FIX 4 regression: the old write_text()-then-chmod() pattern left a
    # world-readable window (0644 under umask 022) between the two calls,
    # and a crash mid-write could leave corrupt JSON. save() now mirrors
    # budget.py's write-to-tmp -> chmod(tmp) -> os.replace(tmp, path)
    # pattern — verify no ".tmp" sibling lingers once save() returns, and
    # the final file is still exactly 0600.
    path = tmp_path / "mcp-session.json"
    mcp_session.save(
        McpSession(client_id="c", access_token="a", refresh_token="r", access_expiry=0.0),
        path,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_overwrites_existing_file_atomically(tmp_path: Path):
    path = tmp_path / "mcp-session.json"
    mcp_session.save(
        McpSession(client_id="old", access_token="a", refresh_token="r", access_expiry=0.0),
        path,
    )
    mcp_session.save(
        McpSession(client_id="new", access_token="b", refresh_token="s", access_expiry=1.0),
        path,
    )
    loaded = mcp_session.load(path)
    assert loaded.client_id == "new"
    assert loaded.access_token == "b"
