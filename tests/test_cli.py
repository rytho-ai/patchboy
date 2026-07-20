"""CLI exit-code contract — the part agents key off. No network."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from spotify_cli import auth as auth_module
from spotify_cli.auth import SpotifyAuthError
from spotify_cli.cli import app
from spotify_cli.config import Settings
from spotify_cli.models import Artist, Track

runner = CliRunner()


def _settings_no_session(tmp_path: Path) -> Settings:
    return Settings(client_id="cid", session_path=tmp_path / "session.json", redirect_port=8787)


def test_status_without_session_exits_1(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Not logged in" in result.output


def test_catalog_without_session_exits_2_human_handoff(monkeypatch, tmp_path) -> None:
    """No session → catalog commands must route to the exit-2 human handoff,
    not a generic error, so a driving agent knows to ask the human to log in."""
    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["saved"])
    assert result.exit_code == 2
    assert "Human step needed" in result.output


def test_login_without_client_id_exits_2_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "spotify_cli.cli._settings",
        lambda: Settings(client_id=None, session_path=tmp_path / "s.json", redirect_port=8787),
    )
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 2
    assert "SPOTIFY_CLIENT_ID" in result.output


def test_login_failure_is_clean_exit_1_not_a_raw_traceback(monkeypatch, tmp_path) -> None:
    """Regression: a real run hit 'Address already in use' (port collision with an
    unrelated process) and _await_callback's SpotifyAuthError escaped uncaught —
    `login`'s `with _handle():` re-raises SpotifyAuthError by design (for other
    commands to route to the exit-2 handoff), but `login` IS the human step
    already, so its own failures must be a clean exit 1, never an unhandled
    exception / raw traceback."""
    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))

    def _boom(**kwargs):
        raise SpotifyAuthError("Couldn't start the local OAuth callback server on port 8787: Address already in use.")

    monkeypatch.setattr(auth_module, "login", _boom)
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 1
    # a clean `typer.Exit(1)` (SystemExit) — not the raw SpotifyAuthError escaping uncaught
    assert isinstance(result.exception, SystemExit)
    # rich may wrap + hyphenate the line at the terminal width — collapse whitespace before matching
    assert "Address already in use" in " ".join(result.output.split())


def test_help_lists_both_tiers() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("search", "my-playlists", "playlist-create", "saved-add", "top"):
        assert cmd in result.output


# ---- top command ------------------------------------------------------------


def test_top_artists_renders(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def top_items(self, kind, *, time_range, limit):
            assert (kind, time_range, limit) == ("artists", "long", 5)
            return (Artist(id="a1", name="Radiohead", uri="spotify:artist:a1"),)

    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("spotify_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["top", "--type", "artists", "--range", "long", "--limit", "5"])
    assert result.exit_code == 0
    assert "Radiohead" in result.output


def test_top_tracks_renders(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def top_items(self, kind, *, time_range, limit):
            assert kind == "tracks"
            return (Track(id="t1", name="Idioteque", uri="spotify:track:t1", artists=("Radiohead",)),)

    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("spotify_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["top", "--type", "tracks"])
    assert result.exit_code == 0
    assert "Idioteque" in result.output


def test_top_defaults_to_artists_medium(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def top_items(self, kind, *, time_range, limit):
            assert (kind, time_range, limit) == ("artists", "medium", 20)
            return ()

    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("spotify_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["top"])
    assert result.exit_code == 0


def test_top_bad_type_exits_1_clean_message(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["top", "--type", "albums"])
    assert result.exit_code == 1
    assert "Unknown --type" in result.output


def test_top_without_session_exits_2_human_handoff(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("spotify_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["top"])
    assert result.exit_code == 2
    assert "Human step needed" in result.output


def test_top_help_lists_options() -> None:
    result = runner.invoke(app, ["top", "--help"])
    assert result.exit_code == 0
    for opt in ("--type", "--range", "--limit"):
        assert opt in result.output
