"""HARDENING 7 regression tests — `download`/`buy --backend` only accepts
"mcp"/"graphql". A typo (e.g. `--backend grapql`) used to fall through
silently to the `mcp` path (the routing logic only ever checks
`backend == "graphql"`) — must instead be a clean, loud exit 1 before any
client is even touched.
"""

from __future__ import annotations

from typer.testing import CliRunner

from splice_cli import cli

runner = CliRunner()


def _env(tmp_path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def _patch_never_reached(monkeypatch) -> None:
    def _boom(*a, **kw):
        raise AssertionError("no client/session should be reached — must fail on the bad --backend first")

    monkeypatch.setattr(cli, "_session", _boom)
    monkeypatch.setattr(cli, "_mcp_session", _boom)


def test_download_invalid_backend_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch_never_reached(monkeypatch)
    result = runner.invoke(
        cli.app, ["download", "some-uuid", "--backend", "grapql"], env=_env(tmp_path)
    )
    assert result.exit_code == 1, result.output
    assert "--backend" in result.output
    assert "mcp" in result.output and "graphql" in result.output


def test_buy_invalid_backend_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch_never_reached(monkeypatch)
    result = runner.invoke(
        cli.app, ["buy", "some-uuid", "--backend", "bogus"], env=_env(tmp_path)
    )
    assert result.exit_code == 1, result.output
    assert "--backend" in result.output


def test_download_valid_backend_graphql_is_not_rejected(monkeypatch, tmp_path):
    """Sanity check the validator isn't overly strict — "graphql" itself
    must not be rejected (it would then hit the (expected) no-session
    handoff instead of the backend-validation error)."""
    result = runner.invoke(
        cli.app, ["download", "some-uuid", "--backend", "graphql"], env=_env(tmp_path)
    )
    assert "--backend" not in result.output or "isn't valid" not in result.output
