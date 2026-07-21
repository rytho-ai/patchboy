"""session.py — atomic 0600 save/load round-trip."""

from __future__ import annotations

import stat

from apple_music_cli import session
from apple_music_cli.auth import AppleMusicSession


def test_save_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "session.json"
    s = AppleMusicSession(
        developer_token="dev", developer_token_expiry=123.0, storefront="tw", media_user_token="mut"
    )
    session.save(s, path)
    assert session.load(path) == s


def test_save_is_0600(tmp_path) -> None:
    path = tmp_path / "session.json"
    session.save(AppleMusicSession(developer_token="d", developer_token_expiry=1.0), path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_load_defaults_missing_optional_fields(tmp_path) -> None:
    path = tmp_path / "session.json"
    path.write_text('{"developer_token": "d", "developer_token_expiry": 1.0}')
    loaded = session.load(path)
    assert loaded.storefront == "us"
    assert loaded.media_user_token is None
