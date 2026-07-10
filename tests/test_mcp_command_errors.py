"""FIX 3 regression tests — MCP command branches must not crash with a raw
traceback on an ordinary tool error or a rate limit; only McpAuthError was
being caught before, but McpClient._call_tool also raises a plain
RuntimeError for a non-rate-limit tool error (e.g. create_stack rejecting a
one-shot uuid), and McpRateLimitError is not a subclass of McpAuthError.

Covers `search`'s MCP branch and all four `stack` subcommands via the shared
`_mcp_call` helper — exercised through CliRunner with McpClient faked out.
"""

from __future__ import annotations

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.mcp_client import McpRateLimitError

runner = CliRunner()


def _env(tmp_path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


class _RaisingMcpClient:
    """Every tool-shaped method raises the same configured error — enough
    to exercise `_mcp_call`'s translation regardless of which command uses
    it (search / stack from / stack prompt / stack update / stack share)."""

    def __init__(self, error: Exception):
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        def _raise(*a, **kw):
            raise self._error

        return _raise


def _patch(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(cli, "_mcp_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "McpClient", lambda sess: _RaisingMcpClient(error))


def test_stack_from_plain_runtime_error_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch(monkeypatch, RuntimeError("MCP tool 'create_stack' reported an error: one-shots can't seed a stack"))
    result = runner.invoke(cli.app, ["stack", "from", "one-shot-uuid"], env=_env(tmp_path))

    assert result.exc_info is None or not isinstance(result.exc_info[1], RuntimeError)
    assert result.exit_code == 1
    assert "one-shots can't seed a stack" in result.output


def test_stack_from_rate_limit_is_a_clean_exit_2(monkeypatch, tmp_path):
    _patch(monkeypatch, McpRateLimitError("MCP's download limit appears to be hit"))
    result = runner.invoke(cli.app, ["stack", "from", "some-uuid"], env=_env(tmp_path))

    assert result.exit_code == 2
    assert "download limit" in result.output


def test_search_mcp_branch_plain_runtime_error_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch(monkeypatch, RuntimeError("MCP error calling 'describe_a_sound': boom"))
    result = runner.invoke(cli.app, ["search", "x"], env=_env(tmp_path))

    assert result.exit_code == 1
    assert "boom" in result.output


def test_stack_prompt_runtime_error_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch(monkeypatch, RuntimeError("MCP tool 'prompt_to_stack' reported an error: bad prompt"))
    result = runner.invoke(cli.app, ["stack", "prompt", "some text"], env=_env(tmp_path))

    assert result.exit_code == 1
    assert "bad prompt" in result.output


def test_stack_share_runtime_error_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch(monkeypatch, RuntimeError("MCP tool 'share_stack' reported an error: not found"))
    result = runner.invoke(cli.app, ["stack", "share", "stack-uuid"], env=_env(tmp_path))

    assert result.exit_code == 1
    assert "not found" in result.output


def test_stack_update_runtime_error_is_a_clean_exit_1(monkeypatch, tmp_path):
    _patch(monkeypatch, RuntimeError("MCP tool 'update_stack' reported an error: max 8 layers"))
    result = runner.invoke(
        cli.app, ["stack", "update", "stack-uuid", "--name", "New Name"], env=_env(tmp_path)
    )

    assert result.exit_code == 1
    assert "max 8 layers" in result.output
