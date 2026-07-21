"""Settings discovery — especially the home-dir .env fallback that makes a
globally-installed `threads` work from any directory.

Note: `config.load_dotenv()` with no path uses python-dotenv's `find_dotenv`,
which walks up from the *source file* location (not the CWD), so it finds this
repo's own `.env` during tests no matter where we chdir. That's correct in
production (installed config.py has no `.env` near it), but pollutes these
tests. So we neutralize the no-arg (project-.env) call and let only the
explicit home-.env path load — which is exactly the fallback under test.
(Same pattern as sibling spotify-cli's test_config.py.)
"""

from __future__ import annotations

from threads_cli import config
from threads_cli.config import Settings


def _only_explicit_dotenv(monkeypatch):
    real = config.load_dotenv
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: real(*a, **k) if a else False)


def test_home_env_fallback_for_global_use(monkeypatch, tmp_path) -> None:
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.delenv("THREADS_APP_ID", raising=False)
    monkeypatch.delenv("THREADS_APP_SECRET", raising=False)
    home_env = tmp_path / ".threads-cli" / ".env"
    home_env.parent.mkdir(parents=True)
    home_env.write_text("THREADS_APP_ID=from_home_env\nTHREADS_APP_SECRET=shh\n")
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(home_env))

    settings = Settings.load()
    assert settings.app_id == "from_home_env"
    assert settings.app_secret == "shh"


def test_real_env_var_beats_home_env(monkeypatch, tmp_path) -> None:
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.setenv("THREADS_APP_ID", "from_shell")
    home_env = tmp_path / ".threads-cli" / ".env"
    home_env.parent.mkdir(parents=True)
    home_env.write_text("THREADS_APP_ID=from_home_env\n")
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(home_env))

    assert Settings.load().app_id == "from_shell"


def test_missing_app_id_and_secret_are_none(monkeypatch, tmp_path) -> None:
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.delenv("THREADS_APP_ID", raising=False)
    monkeypatch.delenv("THREADS_APP_SECRET", raising=False)
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(tmp_path / "nope.env"))

    settings = Settings.load()
    assert settings.app_id is None
    assert settings.app_secret is None


def test_defaults_for_redirect_uri_and_session_path(monkeypatch, tmp_path) -> None:
    _only_explicit_dotenv(monkeypatch)
    monkeypatch.delenv("THREADS_REDIRECT_URI", raising=False)
    monkeypatch.delenv("THREADS_SESSION_PATH", raising=False)
    monkeypatch.setattr(config, "_GLOBAL_ENV", str(tmp_path / "nope.env"))

    settings = Settings.load()
    assert settings.redirect_uri == "https://localhost/"
    assert str(settings.session_path).endswith(".threads-cli/session.json")
