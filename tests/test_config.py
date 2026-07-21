"""config.py — Settings load + precedence."""

from __future__ import annotations

from pathlib import Path

from apple_music_cli.config import Settings


def test_defaults(monkeypatch) -> None:
    for k in ("APPLE_MUSIC_STOREFRONT", "APPLE_MUSIC_USER_TOKEN", "APPLE_MUSIC_SESSION_PATH"):
        monkeypatch.delenv(k, raising=False)
    # Point env_file at a nonexistent path so no ambient .env leaks in.
    s = Settings.load(env_file=Path("/nonexistent/.env"))
    assert s.storefront == "us"
    assert s.media_user_token is None
    assert s.session_path.name == "session.json"


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_MUSIC_STOREFRONT", "tw")
    monkeypatch.setenv("APPLE_MUSIC_USER_TOKEN", "mut")
    s = Settings.load(env_file=Path("/nonexistent/.env"))
    assert s.storefront == "tw"
    assert s.media_user_token == "mut"


def test_empty_user_token_env_is_none(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_MUSIC_USER_TOKEN", "")
    s = Settings.load(env_file=Path("/nonexistent/.env"))
    assert s.media_user_token is None
