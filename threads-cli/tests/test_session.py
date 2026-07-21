"""Session persistence — atomic round-trip and 0600 permissions."""

from __future__ import annotations

import stat

from threads_cli import session
from threads_cli.auth import ThreadsSession


def test_save_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "nested" / "session.json"
    sess = ThreadsSession(access_token="acc", user_id="u1", access_expiry=1234.5)
    session.save(sess, path)
    loaded = session.load(path)
    assert loaded == sess


def test_saved_file_is_user_only_0600(tmp_path) -> None:
    path = tmp_path / "session.json"
    session.save(ThreadsSession(access_token="a", user_id="u1", access_expiry=0), path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_no_tmp_file_left_behind(tmp_path) -> None:
    path = tmp_path / "session.json"
    session.save(ThreadsSession(access_token="a", user_id="u1", access_expiry=0), path)
    assert not (path.with_suffix(path.suffix + ".tmp")).exists()
