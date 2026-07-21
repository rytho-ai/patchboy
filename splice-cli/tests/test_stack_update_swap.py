"""FEATURE GAP 2 regression tests — `stack update`'s --swap/--add-compatible/
--swap-compatible options. McpClient is faked out — no real network.
"""

from __future__ import annotations

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.cli import _parse_swap_layers
from splice_cli.models import Stack

runner = CliRunner()


def _env(tmp_path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def test_parse_swap_layers_valid_pairs():
    result = _parse_swap_layers(["layer-1:asset-1", "layer-2:asset-2"])
    assert result == [
        {"layer_uuid": "layer-1", "asset_uuid": "asset-1"},
        {"layer_uuid": "layer-2", "asset_uuid": "asset-2"},
    ]


def test_parse_swap_layers_malformed_no_colon_exits_1(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise AssertionError("MCP must not be reached — should fail on the bad --swap value first")

    monkeypatch.setattr(cli, "_mcp_session", _boom)
    result = runner.invoke(
        cli.app, ["stack", "update", "stack-uuid", "--swap", "no-colon-here"], env=_env(tmp_path)
    )
    assert result.exit_code == 1
    assert "layer_uuid:asset_uuid" in result.output


def test_parse_swap_layers_malformed_too_many_colons_exits_1(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise AssertionError("MCP must not be reached")

    monkeypatch.setattr(cli, "_mcp_session", _boom)
    result = runner.invoke(
        cli.app, ["stack", "update", "stack-uuid", "--swap", "a:b:c"], env=_env(tmp_path)
    )
    assert result.exit_code == 1


def test_swap_and_compatible_flags_reach_update_stack(monkeypatch, tmp_path):
    seen = {}

    class _RecordingMcpClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def update_stack(self, stack_uuid, **kwargs):
            seen.update(kwargs)
            return Stack(stack_uuid=stack_uuid, name="n", layers=[])

    monkeypatch.setattr(cli, "_mcp_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "McpClient", lambda sess: _RecordingMcpClient())

    result = runner.invoke(
        cli.app,
        [
            "stack", "update", "stack-uuid",
            "--swap", "layer-1:asset-1",
            "--add-compatible",
            "--swap-compatible",
        ],
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert seen["swap_layers"] == [{"layer_uuid": "layer-1", "asset_uuid": "asset-1"}]
    assert seen["add_compatible_layers"] is True
    assert seen["swap_compatible_layers"] is True


def test_no_changes_at_all_still_hard_errors(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise AssertionError("MCP must not be reached")

    monkeypatch.setattr(cli, "_mcp_session", _boom)
    result = runner.invoke(cli.app, ["stack", "update", "stack-uuid"], env=_env(tmp_path))
    assert result.exit_code == 1
    assert "Nothing to update" in result.output
