"""`splice login --paste` — the manual GraphQL-session flow for a machine with
no browser of its own (or a different one than the CLI runs on). Pastes are
live-validated (a fake search) BEFORE saving, so a bad paste fails loudly
instead of silently clobbering a working session file.

SpliceClient is faked out — no real network. Mirrors test_mcp_login_manual.py's
conventions for its MCP-OAuth sibling.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.auth import AUTH_COOKIE, CLEARANCE_COOKIE, SessionExpired

runner = CliRunner()

_VALID_JWT = (
    "eyJhbGciOiJSUzI1NiJ9."
    "eyJleHAiOjk5OTk5OTk5OTl9."
    "fakesig"
)


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


class _FakeSpliceClient:
    """Stands in for SpliceClient — only .search() (the live-check probe)."""

    def __init__(self, sess, *, search_error=None):
        self.sess = sess
        self.search_error = search_error
        self.search_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def search(self, query, limit=1):
        self.search_calls += 1
        if self.search_error:
            raise self.search_error

        class _Result:
            total = 42

        return _Result()


def test_paste_happy_path_saves_session(monkeypatch, tmp_path):
    fake_holder = {}

    def _fake_splice_client(sess):
        fake = _FakeSpliceClient(sess)
        fake_holder["client"] = fake
        return fake

    monkeypatch.setattr(cli, "SpliceClient", _fake_splice_client)

    cookie_header = f"{AUTH_COOKIE}={_VALID_JWT}; {CLEARANCE_COOKIE}=clear-value"
    result = runner.invoke(
        cli.app, ["login", "--paste"],
        input=f"{cookie_header}\nMozilla/5.0 test-UA\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert fake_holder["client"].search_calls == 1
    saved = json.loads((tmp_path / "session.json").read_text())
    assert saved["cookies"][AUTH_COOKIE] == _VALID_JWT
    assert saved["user_agent"] == "Mozilla/5.0 test-UA"
    assert saved["source"] == "pasted"


def test_paste_missing_auth_cookie_exits_1_before_any_network(monkeypatch, tmp_path):
    def _boom(sess):
        raise AssertionError("must not construct a client for an invalid session")

    monkeypatch.setattr(cli, "SpliceClient", _boom)

    result = runner.invoke(
        cli.app, ["login", "--paste"],
        input=f"{CLEARANCE_COOKIE}=clear-value-only\nMozilla/5.0 test-UA\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 1, result.output
    assert not (tmp_path / "session.json").exists()


def test_paste_missing_clearance_cookie_warns_but_still_proceeds(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: _FakeSpliceClient(sess))

    result = runner.invoke(
        cli.app, ["login", "--paste"],
        input=f"{AUTH_COOKIE}={_VALID_JWT}\nMozilla/5.0 test-UA\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert f"No `{CLEARANCE_COOKIE}` cookie" in result.output  # short enough to survive rich's line-wrapping
    assert (tmp_path / "session.json").exists()


def test_paste_live_check_failure_exits_1_and_does_not_save(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "SpliceClient", lambda sess: _FakeSpliceClient(sess, search_error=SessionExpired("rejected"))
    )

    cookie_header = f"{AUTH_COOKIE}={_VALID_JWT}; {CLEARANCE_COOKIE}=clear-value"
    result = runner.invoke(
        cli.app, ["login", "--paste"],
        input=f"{cookie_header}\nMozilla/5.0 test-UA\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 1, result.output
    assert "rejected" in result.output
    assert not (tmp_path / "session.json").exists()


def test_paste_ignores_browser_flag_when_set(monkeypatch, tmp_path):
    """--paste must take precedence over the (irrelevant, default) --browser
    flag rather than attempting a local cookie-store read first."""

    def _boom_extract(*a, **kw):
        raise AssertionError("must not attempt to read a local browser's cookie store")

    monkeypatch.setattr(cli, "extract_session", _boom_extract)
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: _FakeSpliceClient(sess))

    cookie_header = f"{AUTH_COOKIE}={_VALID_JWT}; {CLEARANCE_COOKIE}=clear-value"
    result = runner.invoke(
        cli.app, ["login", "--paste", "--browser", "firefox"],
        input=f"{cookie_header}\nMozilla/5.0 test-UA\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
