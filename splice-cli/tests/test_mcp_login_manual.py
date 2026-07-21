"""`splice login --mcp --no-server` — the manual/paste-back flow, for a
headless machine or a browser that isn't on this process's machine.

Covers two layers:
- mcp_auth.begin_login/complete_login/_parse_redirect_response — pure PKCE/
  parsing logic, no network (the actual token POST is monkeypatched via httpx).
- The CLI wiring (`login --mcp --no-server`) via CliRunner, with
  mcp_auth.begin_login/complete_login faked out — no real OAuth flow runs.

Same faking conventions as test_mcp_login.py (the loopback-server sibling).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from splice_cli import cli, mcp_auth
from splice_cli.mcp_auth import LoginChallenge, McpAuthError, McpSession

runner = CliRunner()


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def _challenge(**overrides) -> LoginChallenge:
    defaults = dict(
        authorize_url="https://mcp.splice.com/oauth/authorize?client_id=c",
        verifier="verifier-abc",
        state="state-xyz",
        redirect_uri="http://localhost:8765/callback",
        client_id="client-abc",
    )
    defaults.update(overrides)
    return LoginChallenge(**defaults)


# --- mcp_auth._parse_redirect_response (pure) -------------------------------


def test_parse_redirect_response_full_url():
    code, state = mcp_auth._parse_redirect_response(
        "http://localhost:8765/callback?code=abc123&state=state-xyz"
    )
    assert code == "abc123"
    assert state == "state-xyz"


def test_parse_redirect_response_bare_code_has_no_state():
    code, state = mcp_auth._parse_redirect_response("abc123")
    assert code == "abc123"
    assert state is None


def test_parse_redirect_response_empty_is_none():
    assert mcp_auth._parse_redirect_response("") == (None, None)
    assert mcp_auth._parse_redirect_response("   ") == (None, None)


# --- mcp_auth.begin_login / complete_login ----------------------------------


def test_begin_login_reuses_given_client_id(monkeypatch):
    monkeypatch.setattr(
        mcp_auth, "register_client", lambda **kw: (_ for _ in ()).throw(AssertionError("must not register"))
    )
    ch = mcp_auth.begin_login(client_id="existing-id", redirect_port=8765)
    assert ch.client_id == "existing-id"
    assert "client_id=existing-id" in ch.authorize_url


def test_begin_login_registers_when_no_client_id(monkeypatch):
    monkeypatch.setattr(mcp_auth, "register_client", lambda **kw: "freshly-registered")
    ch = mcp_auth.begin_login(client_id=None, redirect_port=8765)
    assert ch.client_id == "freshly-registered"


def test_complete_login_exchanges_code_via_token_endpoint(monkeypatch):
    captured = {}

    def _fake_post(url, *, data, timeout):
        captured["url"] = url
        captured["data"] = data
        return httpx.Response(
            200,
            json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(mcp_auth.httpx, "post", _fake_post)
    ch = _challenge()
    sess = mcp_auth.complete_login(ch, "http://localhost:8765/callback?code=c1&state=state-xyz")

    assert isinstance(sess, McpSession)
    assert sess.access_token == "at"
    assert sess.client_id == ch.client_id
    assert captured["data"]["code"] == "c1"
    assert captured["data"]["code_verifier"] == ch.verifier


def test_complete_login_state_mismatch_raises():
    ch = _challenge()
    with pytest.raises(McpAuthError):
        mcp_auth.complete_login(ch, "http://localhost:8765/callback?code=c1&state=WRONG")


def test_complete_login_no_code_raises():
    ch = _challenge()
    with pytest.raises(McpAuthError):
        mcp_auth.complete_login(ch, "http://localhost:8765/callback?state=state-xyz")


def test_complete_login_bare_code_skips_state_check(monkeypatch):
    monkeypatch.setattr(
        mcp_auth.httpx, "post",
        lambda url, **kw: httpx.Response(
            200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            request=httpx.Request("POST", url),
        ),
    )
    ch = _challenge()
    sess = mcp_auth.complete_login(ch, "just-a-bare-code")  # no state at all
    assert sess.access_token == "at"


def test_complete_login_token_endpoint_error_raises(monkeypatch):
    monkeypatch.setattr(
        mcp_auth.httpx, "post",
        lambda url, **kw: httpx.Response(400, text="bad request", request=httpx.Request("POST", url)),
    )
    ch = _challenge()
    with pytest.raises(McpAuthError):
        mcp_auth.complete_login(ch, "http://localhost:8765/callback?code=c1&state=state-xyz")


# --- CLI: `login --mcp --no-server` -----------------------------------------


def test_cli_manual_login_prompts_and_saves(monkeypatch, tmp_path):
    calls = {}

    def _fake_begin(*, client_id=None, redirect_port=None):
        calls["begin_client_id"] = client_id
        calls["begin_port"] = redirect_port
        return _challenge()

    def _fake_complete(challenge, pasted):
        calls["pasted"] = pasted
        calls["challenge"] = challenge
        return McpSession(client_id="c", access_token="at", refresh_token="rt", access_expiry=9e12)

    monkeypatch.setattr(cli.mcp_auth, "begin_login", _fake_begin)
    monkeypatch.setattr(cli.mcp_auth, "complete_login", _fake_complete)

    result = runner.invoke(
        cli.app, ["login", "--mcp", "--no-server"],
        input="http://localhost:8765/callback?code=abc&state=xyz\n",
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert calls["begin_client_id"] is None  # no prior session in this tmp_path
    assert calls["pasted"] == "http://localhost:8765/callback?code=abc&state=xyz"
    assert (tmp_path / "mcp-session.json").exists()


def test_cli_manual_login_reuses_existing_client_id(monkeypatch, tmp_path):
    import json

    path = tmp_path / "mcp-session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"client_id": "cached-id", "access_token": "old", "refresh_token": "old", "access_expiry": 0}
        )
    )
    calls = {}

    def _fake_begin(*, client_id=None, redirect_port=None):
        calls["begin_client_id"] = client_id
        return _challenge(client_id=client_id or "unexpected")

    monkeypatch.setattr(cli.mcp_auth, "begin_login", _fake_begin)
    monkeypatch.setattr(
        cli.mcp_auth, "complete_login",
        lambda challenge, pasted: McpSession(
            client_id=challenge.client_id, access_token="at", refresh_token="rt", access_expiry=9e12
        ),
    )

    result = runner.invoke(
        cli.app, ["login", "--mcp", "--no-server"], input="bare-code\n", env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert calls["begin_client_id"] == "cached-id"


def test_cli_manual_login_auth_error_exits_1(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.mcp_auth, "begin_login", lambda **kw: _challenge())
    monkeypatch.setattr(
        cli.mcp_auth, "complete_login",
        lambda challenge, pasted: (_ for _ in ()).throw(McpAuthError("bad paste")),
    )

    result = runner.invoke(
        cli.app, ["login", "--mcp", "--no-server"], input="garbage\n", env=_env(tmp_path)
    )

    assert result.exit_code == 1, result.output
    assert "bad paste" in result.output


def test_cli_default_login_mcp_still_uses_loopback_server(monkeypatch, tmp_path):
    """--mcp without --no-server must still take the login() (loopback) path,
    not begin_login/complete_login — pins that manual mode is opt-in only."""
    called = {"login": False, "begin": False}
    monkeypatch.setattr(
        cli.mcp_auth, "login",
        lambda **kw: (called.__setitem__("login", True), McpSession(
            client_id="c", access_token="at", refresh_token="rt", access_expiry=9e12
        ))[1],
    )
    monkeypatch.setattr(
        cli.mcp_auth, "begin_login", lambda **kw: called.__setitem__("begin", True)
    )

    result = runner.invoke(cli.app, ["login", "--mcp"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert called["login"] is True
    assert called["begin"] is False
