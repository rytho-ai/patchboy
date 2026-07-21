"""HARDENING 7 regression tests — `splice login --mcp`:

- Caches and reuses an existing MCP session's `client_id` on re-login
  instead of calling `register_client()` (i.e. dynamic client registration)
  again every time, which sprawls a new server-side OAuth client per login.
- `--port` is plumbed through to `mcp_auth.login(redirect_port=...)`.

All network/browser interaction is faked out via monkeypatching
`mcp_auth.login` directly — no real OAuth flow runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.mcp_auth import McpSession

runner = CliRunner()


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def _write_mcp_session(path: Path, *, client_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "access_expiry": 0,
            }
        )
    )


def _fake_session(client_id: str) -> McpSession:
    return McpSession(
        client_id=client_id, access_token="new-access", refresh_token="new-refresh",
        access_expiry=9e12,
    )


def test_relogin_reuses_existing_client_id(monkeypatch, tmp_path):
    _write_mcp_session(tmp_path / "mcp-session.json", client_id="cached-client-id")
    calls = {}

    def _fake_login(*, client_id=None, redirect_port=None, on_url=None, **_):
        calls["client_id"] = client_id
        calls["redirect_port"] = redirect_port
        return _fake_session(client_id or "freshly-registered")

    monkeypatch.setattr(cli.mcp_auth, "login", _fake_login)

    result = runner.invoke(cli.app, ["login", "--mcp"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert calls["client_id"] == "cached-client-id"


def test_first_login_has_no_client_id_to_reuse(monkeypatch, tmp_path):
    calls = {}

    def _fake_login(*, client_id=None, redirect_port=None, on_url=None, **_):
        calls["client_id"] = client_id
        return _fake_session("brand-new-client-id")

    monkeypatch.setattr(cli.mcp_auth, "login", _fake_login)

    result = runner.invoke(cli.app, ["login", "--mcp"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert calls["client_id"] is None


def test_port_option_is_plumbed_to_mcp_auth_login(monkeypatch, tmp_path):
    calls = {}

    def _fake_login(*, client_id=None, redirect_port=None, on_url=None, **_):
        calls["redirect_port"] = redirect_port
        return _fake_session("c")

    monkeypatch.setattr(cli.mcp_auth, "login", _fake_login)

    result = runner.invoke(
        cli.app, ["login", "--mcp", "--port", "9999"], env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert calls["redirect_port"] == 9999


def test_corrupt_existing_session_falls_back_to_fresh_registration(monkeypatch, tmp_path):
    path = tmp_path / "mcp-session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json")
    calls = {}

    def _fake_login(*, client_id=None, redirect_port=None, on_url=None, **_):
        calls["client_id"] = client_id
        return _fake_session("recovered")

    monkeypatch.setattr(cli.mcp_auth, "login", _fake_login)

    result = runner.invoke(cli.app, ["login", "--mcp"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert calls["client_id"] is None
