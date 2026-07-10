"""FEATURE GAP 3 regression tests — `status` reports MCP session health
alongside the existing GraphQL probe, using a local-only expiry check (no
network call) so it can never spend a credit or trip the rate limit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from splice_cli import cli

runner = CliRunner()


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def _write_mcp_session(path: Path, *, access_expiry: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "client_id": "c",
                "access_token": "a",
                "refresh_token": "r",
                "access_expiry": access_expiry,
            }
        )
    )


def test_no_graphql_session_still_reports_mcp_section_before_human_handoff(tmp_path):
    # No GraphQL session at all — status exits 2, but must still print the
    # MCP section first rather than short-circuiting silently.
    _write_mcp_session(tmp_path / "mcp-session.json", access_expiry=time.time() + 3600)
    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))
    assert result.exit_code == 2
    assert "MCP" in result.output


def test_no_mcp_session_reports_login_mcp_hint(tmp_path):
    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))
    assert "No MCP session" in result.output
    assert "splice login --mcp" in result.output


def test_mcp_session_present_reports_no_network_call_label(tmp_path):
    _write_mcp_session(tmp_path / "mcp-session.json", access_expiry=time.time() + 3600)
    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))
    assert "no network call" in result.output.lower()


def test_expired_mcp_token_is_flagged(tmp_path):
    _write_mcp_session(tmp_path / "mcp-session.json", access_expiry=time.time() - 10)
    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))
    assert "expired" in result.output.lower()


def test_missing_graphql_session_still_exits_2_not_0(tmp_path):
    """A missing MCP session must NOT change the exit code driven by the
    GraphQL probe — GraphQL-only usage is valid, but a genuinely missing
    GraphQL session is still a human-step handoff."""
    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))
    assert result.exit_code == 2


def test_working_graphql_session_with_no_mcp_session_still_exits_0(monkeypatch, tmp_path):
    """A GraphQL-only user (no MCP session at all) is a fully valid setup —
    missing MCP alone must never fail `status`."""
    from splice_cli.models import SearchResult

    class _FakeSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, limit=1):
            return SearchResult(query=query, samples=[], total=5, page=1)

    session_path = tmp_path / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps({"cookies": {}, "user_agent": "ua", "source": "test"}))

    class _FakeSess:
        source = "test"
        access_token_expiry = None
        clearance_expiry = None

    monkeypatch.setattr(cli.session_store, "load", lambda path: _FakeSess())
    monkeypatch.setattr(cli, "SpliceClient", _FakeSpliceClient)

    result = runner.invoke(cli.app, ["status"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "No MCP session" in result.output
