"""cli.py — the exit-code contract (0 ok / 1 ordinary / 2 human step). The live
client is stubbed so nothing touches the network."""

from __future__ import annotations

from typer.testing import CliRunner

from applemusic_cli import cli
from applemusic_cli.auth import AppleMusicAuthError
from applemusic_cli.models import SearchResults, Song

runner = CliRunner()


class _FakeClient:
    def __init__(self, **behavior) -> None:
        self._behavior = behavior

    def search(self, query, types, limit=25):
        return self._behavior.get("search", SearchResults())

    def my_playlists(self, limit=25):
        raise self._behavior["my_playlists"]


def _stub(monkeypatch, **behavior) -> None:
    monkeypatch.setattr(cli, "_live_client", lambda settings: _FakeClient(**behavior))


def test_search_hit_exits_0(monkeypatch) -> None:
    results = SearchResults(songs=(Song(id="1", type="songs", name="Idioteque", artists=("Radiohead",)),))
    _stub(monkeypatch, search=results)
    res = runner.invoke(cli.app, ["search", "idioteque"])
    assert res.exit_code == 0
    assert "Idioteque" in res.stdout


def test_search_no_results_exits_1(monkeypatch) -> None:
    _stub(monkeypatch, search=SearchResults())
    res = runner.invoke(cli.app, ["search", "zzzz"])
    assert res.exit_code == 1
    assert "No results" in res.stdout


def test_library_without_login_exits_2(monkeypatch) -> None:
    # my_playlists raises the auth error the caged client would raise with no
    # media-user-token → the exit-2 human handoff.
    _stub(monkeypatch, my_playlists=AppleMusicAuthError("not logged in"))
    res = runner.invoke(cli.app, ["my-playlists"])
    assert res.exit_code == 2
    assert "Human step needed" in res.stdout


def test_status_without_session_exits_1(monkeypatch, tmp_path) -> None:
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(
        cli, "_settings", lambda: cli.Settings(storefront="us", session_path=missing, media_user_token=None)
    )
    res = runner.invoke(cli.app, ["status"])
    assert res.exit_code == 1
    assert "No session" in res.stdout
