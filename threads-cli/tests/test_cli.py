"""CLI exit-code contract — the part agents key off. No network."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from threads_cli import auth as auth_module
from threads_cli.auth import ThreadsAuthError
from threads_cli.cli import app
from threads_cli.config import Settings
from threads_cli.models import Post, Profile

runner = CliRunner()


def _settings_no_session(tmp_path: Path) -> Settings:
    return Settings(
        app_id="app-id",
        app_secret="app-secret",
        redirect_uri="https://localhost/",
        session_path=tmp_path / "session.json",
    )


def test_setup_prints_walkthrough_and_exits_0() -> None:
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "THREADS_APP_ID" in result.output
    assert "Threads Tester" in result.output


def test_status_without_session_exits_1(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Not logged in" in result.output


def test_me_without_session_exits_2_human_handoff(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["me"])
    assert result.exit_code == 2
    assert "Human step needed" in result.output


def test_posts_without_session_exits_2_human_handoff(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    result = runner.invoke(app, ["posts"])
    assert result.exit_code == 2


def test_login_without_app_id_exits_2_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "threads_cli.cli._settings",
        lambda: Settings(app_id=None, app_secret=None, redirect_uri="https://localhost/", session_path=tmp_path / "s.json"),
    )
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 2
    assert "THREADS_APP_ID" in result.output


def test_login_without_app_secret_exits_2_setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "threads_cli.cli._settings",
        lambda: Settings(app_id="x", app_secret=None, redirect_uri="https://localhost/", session_path=tmp_path / "s.json"),
    )
    result = runner.invoke(app, ["login"])
    assert result.exit_code == 2


def test_login_flow_prompts_and_saves_session(monkeypatch, tmp_path) -> None:
    """Manual paste-back: begin_login prints a URL, the pasted redirect is fed
    via CliRunner's stdin, complete_login is exercised for real (no network,
    everything below it is monkeypatched)."""
    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))

    ch = auth_module.begin_login(client_id="app-id", redirect_uri="https://localhost/")
    monkeypatch.setattr(auth_module, "begin_login", lambda **kw: ch)

    from threads_cli.auth import ThreadsSession

    def _fake_complete_login(challenge, pasted, *, client_id, client_secret):
        assert client_id == "app-id"
        assert client_secret == "app-secret"
        assert "code=AC" in pasted
        return ThreadsSession(access_token="tok", user_id="99", access_expiry=2**40)

    monkeypatch.setattr(auth_module, "complete_login", _fake_complete_login)

    result = runner.invoke(app, ["login"], input=f"https://localhost/?code=AC&state={ch.state}\n")
    assert result.exit_code == 0
    assert "Logged in" in result.output
    assert (tmp_path / "session.json").exists()


def test_login_failure_is_clean_exit_1_not_a_raw_traceback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))

    ch = auth_module.begin_login(client_id="app-id")
    monkeypatch.setattr(auth_module, "begin_login", lambda **kw: ch)

    def _boom(*a, **k):
        raise ThreadsAuthError("Couldn't find an authorization code in what you pasted.")

    monkeypatch.setattr(auth_module, "complete_login", _boom)
    result = runner.invoke(app, ["login"], input="garbage\n")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "authorization code" in " ".join(result.output.split())


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("setup", "login", "status", "me", "posts", "post", "replies", "insights"):
        assert cmd in result.output


# ---- account commands with a faked live client ------------------------------


def test_me_renders_profile(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def me(self):
            return Profile(id="1", username="agentuser", name="Agent User")

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["me"])
    assert result.exit_code == 0
    assert "agentuser" in result.output


def test_posts_renders_table(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def my_posts(self, limit):
            assert limit == 5
            return (Post(id="p1", text="hello", media_type="TEXT"),)

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["posts", "--limit", "5"])
    assert result.exit_code == 0
    assert "hello" in result.output


def test_post_publishes_and_renders(monkeypatch, tmp_path) -> None:
    class _FakeClient:
        def publish_text(self, text):
            assert text == "hi from an agent"
            return Post(id="p9", text=text, media_type="TEXT")

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["post", "hi from an agent"])
    assert result.exit_code == 0
    assert "Published" in result.output
    assert "p9" in result.output


def test_insights_no_id_uses_user_insights(monkeypatch, tmp_path) -> None:
    from threads_cli.models import InsightMetric, Insights

    class _FakeClient:
        def user_insights(self):
            return Insights(metrics=(InsightMetric(name="followers_count", value=10, title="Followers"),))

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "Followers" in result.output


def test_insights_with_id_uses_post_insights(monkeypatch, tmp_path) -> None:
    from threads_cli.models import InsightMetric, Insights

    class _FakeClient:
        def post_insights(self, post_id):
            assert post_id == "p1"
            return Insights(metrics=(InsightMetric(name="views", value=100, title="Views"),))

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["insights", "p1"])
    assert result.exit_code == 0
    assert "Views" in result.output


def test_replies_uses_replies_endpoint_by_default(monkeypatch, tmp_path) -> None:
    from threads_cli.models import Reply

    class _FakeClient:
        def replies(self, post_id):
            assert post_id == "p1"
            return (Reply(id="r1", text="nice one", username="bob"),)

        def conversation(self, post_id):
            raise AssertionError("should not be called without --conversation")

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["replies", "p1"])
    assert result.exit_code == 0
    assert "bob" in result.output


def test_replies_conversation_flag_uses_conversation_endpoint(monkeypatch, tmp_path) -> None:
    from threads_cli.models import Reply

    class _FakeClient:
        def replies(self, post_id):
            raise AssertionError("should not be called with --conversation")

        def conversation(self, post_id):
            return (Reply(id="r2", text="thread reply", username="alice"),)

    monkeypatch.setattr("threads_cli.cli._settings", lambda: _settings_no_session(tmp_path))
    monkeypatch.setattr("threads_cli.cli._live_client", lambda settings: _FakeClient())
    result = runner.invoke(app, ["replies", "p1", "--conversation"])
    assert result.exit_code == 0
    assert "alice" in result.output
