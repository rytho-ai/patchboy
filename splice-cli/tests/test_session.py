"""GraphQL session persistence — save/load round-trip and file permissions.

Mirrors test_mcp_session.py: session.py's save() was patched (FIX 4) to the
same atomic write-to-tmp -> chmod(tmp) -> os.replace pattern already used by
budget.py, replacing a write_text()-then-chmod() sequence that left the file
briefly (or, on a mid-write crash, permanently) world-readable.
"""

from __future__ import annotations

import stat
from pathlib import Path

from splice_cli import session as session_store
from splice_cli.auth import Session

# auth.py's Session.__post_init__ requires the Splice auth cookie to be
# present (else it raises NoSessionError) — any placeholder value is fine,
# it's only decoded best-effort for an expiry hint.
_COOKIES = {"_splice_token_prod": "not-a-real-jwt"}


def test_save_load_round_trip(tmp_path: Path):
    path = tmp_path / "session.json"
    original = Session(
        cookies=_COOKIES,
        user_agent="test-agent/1.0",
        clearance_expiry=1234567890.0,
        source="firefox",
    )
    session_store.save(original, path)
    loaded = session_store.load(path)

    assert loaded.cookies == original.cookies
    assert loaded.user_agent == original.user_agent
    assert loaded.clearance_expiry == original.clearance_expiry
    assert loaded.source == original.source


def test_save_sets_owner_only_permissions(tmp_path: Path):
    path = tmp_path / "session.json"
    session_store.save(Session(cookies=_COOKIES, user_agent="ua"), path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_save_is_atomic_no_leftover_temp_file(tmp_path: Path):
    path = tmp_path / "session.json"
    session_store.save(Session(cookies=_COOKIES, user_agent="ua"), path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    assert not tmp.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_overwrites_existing_file_atomically(tmp_path: Path):
    path = tmp_path / "session.json"
    session_store.save(Session(cookies=_COOKIES, user_agent="ua-old", source="firefox"), path)
    session_store.save(Session(cookies=_COOKIES, user_agent="ua-new", source="chrome"), path)
    loaded = session_store.load(path)
    assert loaded.user_agent == "ua-new"
    assert loaded.source == "chrome"
