"""Settings discovery — especially the home-dir .env fallback that makes a
globally-installed `spotify` work from any directory.

Note: `config.load_dotenv()` with no path uses python-dotenv's `find_dotenv`,
which walks up from the *source file* location (not the CWD), so it finds this
repo's own `.env` during tests no matter where we chdir. That's correct in
production (installed config.py has no `.env` near it), but pollutes these tests.
So we neutralize the no-arg (project-.env) call and let only the explicit
home-.env path load — which is exactly the fallback under test.
"""

from __future__ import annotations

from spotify_cli import config
from spotify_cli.config import Settings


def _only_explicit_dotenv(monkeypatch):
    """Make load_dotenv a no-op for the no-arg project-.env lookup, real for an
    explicit path (the home-.env fallback)."""
    real = config.load_dotenv
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: real(*a, **k) if a else False)


def test_home_env_fallback_for_global_use(monkeypatch, tmp_path) -> None:
    """A `uv tool install`d `spotify` run from a dir with no project .env still
    finds SPOTIFY_CLIENT_ID via ~/.spotify-cli/.env."""
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    home_env = tmp_path / ".spotify-cli" / ".env"
    home_env.parent.mkdir(parents=True)
    home_env.write_text("SPOTIFY_CLIENT_ID=from_home_env\n")
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(home_env))

    assert Settings.load().client_id == "from_home_env"


def test_real_env_var_beats_home_env(monkeypatch, tmp_path) -> None:
    """A real exported env var wins over the home .env (override=False semantics)."""
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from_shell")
    home_env = tmp_path / ".spotify-cli" / ".env"
    home_env.parent.mkdir(parents=True)
    home_env.write_text("SPOTIFY_CLIENT_ID=from_home_env\n")
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(home_env))

    assert Settings.load().client_id == "from_shell"


def test_missing_client_id_is_none(monkeypatch, tmp_path) -> None:
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(tmp_path / "nope.env"))

    assert Settings.load().client_id is None
