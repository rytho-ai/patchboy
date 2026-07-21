"""FIX 5 regression tests — mode-specific search flags must not be silently
ignored in the wrong backend. --type/--licensed only mean anything to the
GraphQL --keyword path; --sample-type only to the default MCP path; --limit
is always a no-op on the default MCP path (describe_a_sound has no limit
param). Getting the wrong combination must be loud, not silently wrong.

All clients are faked out — no real network.
"""

from __future__ import annotations

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.models import SearchResult

runner = CliRunner()


def _env(tmp_path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


class _FakeSpliceClient:
    def __init__(self, **_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def search(self, query, **kwargs):
        raise AssertionError("GraphQL client must not be reached in these tests")


class _FakeMcpClient:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def search(self, query, **kwargs):
        return SearchResult(query=query, samples=[], total=0, page=1)


def _patch_mcp_never_reached(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("MCP session must not be reached — should fail before any client call")

    monkeypatch.setattr(cli, "_mcp_session", _boom)


def _patch_graphql_never_reached(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("GraphQL session must not be reached — should fail before any client call")

    monkeypatch.setattr(cli, "_session", _boom)


def test_licensed_without_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_mcp_never_reached(monkeypatch)
    result = runner.invoke(cli.app, ["search", "x", "--licensed"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "--keyword" in result.output


def test_type_without_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_mcp_never_reached(monkeypatch)
    result = runner.invoke(cli.app, ["search", "x", "--type", "preset"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "--keyword" in result.output


def test_sample_type_with_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_graphql_never_reached(monkeypatch)
    result = runner.invoke(
        cli.app, ["search", "x", "--keyword", "--sample-type", "loop"], env=_env(tmp_path)
    )
    assert result.exit_code != 0
    assert "--sample-type" in result.output


def test_limit_on_mcp_path_prints_ignored_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_mcp_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "McpClient", lambda sess: _FakeMcpClient())

    result = runner.invoke(cli.app, ["search", "x", "-n", "5"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert "ignored" in result.output.lower()
    assert "--limit" in result.output or "-n" in result.output


def test_key_without_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_mcp_never_reached(monkeypatch)
    result = runner.invoke(cli.app, ["search", "x", "--key", "c minor"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "--keyword" in result.output


def test_sort_without_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_mcp_never_reached(monkeypatch)
    result = runner.invoke(cli.app, ["search", "x", "--sort", "relevance"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "--keyword" in result.output


def test_order_without_keyword_hard_errors(monkeypatch, tmp_path):
    _patch_mcp_never_reached(monkeypatch)
    result = runner.invoke(cli.app, ["search", "x", "--order", "ASC"], env=_env(tmp_path))
    assert result.exit_code != 0
    assert "--keyword" in result.output


def test_bpm_min_max_reach_mcp_client_on_default_path(monkeypatch, tmp_path):
    """FEATURE GAP 1 — --bpm-min/--bpm-max work on BOTH backends; on the
    default (MCP) path they must reach McpClient.search as bpm_min/bpm_max
    kwargs, without needing --keyword."""
    seen = {}

    class _RecordingMcpClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(query=query, samples=[], total=0, page=1)

    monkeypatch.setattr(cli, "_mcp_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "McpClient", lambda sess: _RecordingMcpClient())

    result = runner.invoke(
        cli.app, ["search", "x", "--bpm-min", "80", "--bpm-max", "100"], env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert seen["bpm_min"] == 80
    assert seen["bpm_max"] == 100


def test_bpm_min_max_reach_graphql_client_with_keyword(monkeypatch, tmp_path):
    """On --keyword (GraphQL), --bpm-min/--bpm-max map to min_bpm/max_bpm —
    the GraphQL variable names — not bpm_min/bpm_max."""
    seen = {}

    class _RecordingSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(query=query, samples=[], total=0, page=1)

    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", _RecordingSpliceClient)

    result = runner.invoke(
        cli.app,
        ["search", "x", "--keyword", "--bpm-min", "80", "--bpm-max", "100", "--key", "c minor",
         "--sort", "relevance", "--order", "ASC"],
        env=_env(tmp_path),
    )

    assert result.exit_code == 0, result.output
    assert seen["min_bpm"] == 80
    assert seen["max_bpm"] == 100
    assert seen["key"] == "c minor"
    assert seen["sort"] == "relevance"
    assert seen["order"] == "ASC"
    assert "bpm_min" not in seen
    assert "bpm_max" not in seen


def test_preview_type_flag_reaches_client_search(monkeypatch, tmp_path):
    """FEATURE GAP 4 — `preview` gained a --type/-t flag mirroring `audition`
    (previously it always searched the "sample" default with no override)."""
    from splice_cli.models import Sample

    seen = {}

    class _RecordingSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(
                query=query,
                samples=[Sample(uuid="u", name="foo.wav", preview_url="https://example/x.mp3")],
                total=1, page=1,
            )

        def download_preview(self, sample, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fake")
            return target

    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", _RecordingSpliceClient)

    result = runner.invoke(cli.app, ["preview", "x", "--type", "preset"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert seen["asset_type"] == "preset"


def test_audition_bpm_range_reaches_graphql_as_min_max_bpm(monkeypatch, tmp_path):
    """Follow-up — `audition` (always GraphQL) gained --bpm-min/--bpm-max,
    which map to GraphQL's min_bpm/max_bpm and are omitted when unset."""
    seen = {}

    class _RecordingSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(query=query, samples=[], total=0, page=1)

    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", _RecordingSpliceClient)

    # No previewable results → exit 1 by design, but the search kwargs are
    # already captured by then, which is what this test asserts.
    runner.invoke(
        cli.app, ["audition", "x", "--bpm-min", "90", "--bpm-max", "120"], env=_env(tmp_path)
    )

    assert seen["min_bpm"] == 90
    assert seen["max_bpm"] == 120
    assert "bpm_min" not in seen


def test_preview_without_bpm_range_omits_the_kwargs(monkeypatch, tmp_path):
    """The BPM range must be omitted entirely when unset — a default of
    "no filter" must not become "filter on None"."""
    seen = {}

    class _RecordingSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(query=query, samples=[], total=0, page=1)

    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", _RecordingSpliceClient)

    runner.invoke(cli.app, ["preview", "x"], env=_env(tmp_path))

    assert "min_bpm" not in seen
    assert "max_bpm" not in seen


def test_keyword_search_with_bare_type_still_defaults_to_sample(monkeypatch, tmp_path):
    """--type's default changed from "sample" to None (FIX 5) so explicit-
    vs-default is detectable — but plain `--keyword` with no --type at all
    must still search asset_type="sample", same as before."""
    seen = {}

    class _RecordingSpliceClient:
        def __init__(self, sess):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return SearchResult(query=query, samples=[], total=0, page=1)

    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", _RecordingSpliceClient)

    result = runner.invoke(cli.app, ["search", "x", "--keyword"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert seen["asset_type"] == "sample"
