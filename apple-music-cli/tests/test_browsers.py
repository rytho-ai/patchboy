"""browsers.py — the media-user-token cookie lift. Firefox uses a real (temp)
sqlite so the query path is exercised for real; no live browser needed."""

from __future__ import annotations

import sqlite3

import pytest

from apple_music_cli import browsers
from apple_music_cli.browsers import NoBrowserSessionError


def _make_firefox_db(path, rows: list[tuple]) -> None:
    """rows: (name, value, host, expiry)."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_cookies (name TEXT, value TEXT, host TEXT, expiry INTEGER)")
    con.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def test_firefox_lift_prefers_music_host(monkeypatch, tmp_path) -> None:
    prof = tmp_path / "abc.default"
    prof.mkdir()
    _make_firefox_db(
        prof / "cookies.sqlite",
        [
            ("media-user-token", "MUSIC_TOKEN", ".music.apple.com", 1800153935863),
            ("media-user-token", "TV_TOKEN", ".tv.apple.com", 1800153937381),
            ("other", "x", ".music.apple.com", 1),
        ],
    )
    monkeypatch.setattr(browsers, "_find_firefox_profile", lambda: prof)
    assert browsers.extract_media_user_token("firefox") == "MUSIC_TOKEN"


def test_firefox_lift_raises_when_absent(monkeypatch, tmp_path) -> None:
    prof = tmp_path / "abc.default"
    prof.mkdir()
    _make_firefox_db(prof / "cookies.sqlite", [("some-other", "v", ".music.apple.com", 1)])
    monkeypatch.setattr(browsers, "_find_firefox_profile", lambda: prof)
    with pytest.raises(NoBrowserSessionError):
        browsers.extract_media_user_token("firefox")


def test_unknown_browser_raises() -> None:
    with pytest.raises(browsers.AppleMusicAuthError):
        browsers.extract_media_user_token("safari")


def test_auto_falls_through_to_error(monkeypatch) -> None:
    # Every source raises → auto surfaces the "log in somewhere" error.
    monkeypatch.setattr(browsers, "_from_firefox", lambda: (_ for _ in ()).throw(NoBrowserSessionError("x")))
    monkeypatch.setattr(
        browsers, "_from_chromium", lambda b: (_ for _ in ()).throw(NoBrowserSessionError("x"))
    )
    with pytest.raises(NoBrowserSessionError):
        browsers.extract_media_user_token("auto")


def test_pick_falls_back_to_freshest_when_no_music_host() -> None:
    rows = [("OLD", ".apps.apple.com", 100), ("NEW", ".tv.apple.com", 200)]
    assert browsers._pick(rows) == "NEW"
