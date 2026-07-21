"""The listen-first gate: Sample.url construction (both backends) + the
--confirm/--no-confirm flow on `download`/`buy`.

Sample.url construction is deliberately NOT built from item["uuid"] — verified
live 2026-07-21 that GraphQL's `uuid` field means two different things
depending on which query populated it (assetsSearch: a content hash; AssetByUuid:
an echo of the queried catalog_uuid — building a URL from the latter 404s).
The content hash is instead pulled from the preview mp3's own filename, which
is reliably present on both query paths. These tests pin that behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.client import _sample_page_url, _to_sample
from splice_cli.mcp_client import _parse_search_markdown, _parse_stack_markdown
from splice_cli.models import Sample

runner = CliRunner()

_PREVIEW_URL = (
    "https://spliceproduction.s3.us-west-1.amazonaws.com/audio_samples/"
    "cae9e1dd8f102a062e087ba50c481c3e49502515adf91809061cb417402c7615-scrambled/"
    "cae9e1dd8f102a062e087ba50c481c3e49502515adf91809061cb417402c7615.mp3"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=21600"
)
_EXPECTED_URL = (
    "https://splice.com/sounds/sample/"
    "cae9e1dd8f102a062e087ba50c481c3e49502515adf91809061cb417402c7615/"
)


# --- _sample_page_url (pure function) ---------------------------------------


def test_sample_page_url_extracts_content_hash_from_preview():
    assert _sample_page_url(asset_type="sample", preview_url=_PREVIEW_URL) == _EXPECTED_URL


def test_sample_page_url_none_without_preview():
    assert _sample_page_url(asset_type="sample", preview_url=None) is None


def test_sample_page_url_none_for_non_sample_asset_types():
    # Only verified live for "sample" — other types' page-URL scheme is unknown,
    # so this must stay None rather than guess and hand out a maybe-404 link.
    assert _sample_page_url(asset_type="preset", preview_url=_PREVIEW_URL) is None
    assert _sample_page_url(asset_type="pack", preview_url=_PREVIEW_URL) is None


def test_to_sample_wires_url_via_search_shaped_item():
    # assetsSearch shape: item["uuid"] IS the content hash — but _to_sample
    # must still go through the preview-derived helper, not use it directly,
    # so this test would also catch a regression back to trusting item["uuid"].
    item = {
        "uuid": "cae9e1dd8f102a062e087ba50c481c3e49502515adf91809061cb417402c7615",
        "catalog_uuid": "7b75a1d7-834a-4d29-b40c-5ede92925ffd",
        "name": "Pack/foo.wav",
        "asset_type_slug": "sample",
        "files": [{"asset_file_type_slug": "preview_mp3", "url": _PREVIEW_URL}],
    }
    assert _to_sample(item).url == _EXPECTED_URL


def test_to_sample_from_asset_by_uuid_shaped_item_still_resolves_correctly():
    # AssetByUuid shape: item["uuid"] is just an echo of the queried
    # catalog_uuid (verified live) — a DIFFERENT value than the content hash
    # above. If _to_sample used item["uuid"] for the URL, this would build
    # .../sample/7b75a1d7-.../ instead, which 404s (verified live). The
    # preview-derived helper must ignore item["uuid"] entirely for this.
    item = {
        "uuid": "7b75a1d7-834a-4d29-b40c-5ede92925ffd",  # NOT the content hash
        "catalog_uuid": "7b75a1d7-834a-4d29-b40c-5ede92925ffd",
        "name": "Pack/foo.wav",
        "asset_type_slug": "sample",
        "files": [{"asset_file_type_slug": "preview_mp3", "url": _PREVIEW_URL}],
    }
    assert _to_sample(item).url == _EXPECTED_URL


# --- MCP markdown parsing: carries the literal **Link:** text through -------


def test_mcp_search_markdown_wires_url_from_link_field():
    text = (
        "Showing 1 of 1 total results\n"
        "### 1. OS_HS_70_Em_Soulful_Melody.wav\n"
        "BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop\n"
        "**Pack:** Origin Sound\n"
        "**Tags:** strings, soul\n"
        "**Link:** https://splice.com/sounds/sample/abc123/some-slug\n"
        "**Asset UUID:** ce27437e-1e67-436e-a1aa-fbd7058ba882\n"
    )
    samples, _ = _parse_search_markdown(text)
    assert samples[0].url == "https://splice.com/sounds/sample/abc123/some-slug"


def test_mcp_search_markdown_url_none_without_link_field():
    text = "Showing 1 of 1 total results\n### 1. foo.wav\nBPM: 70\n"
    samples, _ = _parse_search_markdown(text)
    assert samples[0].url is None


def test_mcp_stack_layer_wires_url_from_link_field():
    # Real captured shape (see test_mcp_client.py's STACK_MARKDOWN) — a
    # "### Layer N: Role" header, not the "### N. name" search-result header.
    text = (
        "## Stack: test-stack\n"
        "**BPM:** 70 | **Key:** e minor\n\n"
        "### Layer 1: Drums\n"
        "**Layer UUID:** 22222222-2222-2222-2222-222222222222\n"
        "**Sample:** foo.wav\n"
        "BPM: 70 | Key: e minor | Duration: 13.7s | Type: loop\n"
        "**Link:** https://splice.com/sounds/sample/def456/some-slug\n"
        "**Asset UUID:** 33333333-3333-3333-3333-333333333333\n\n"
        "**Stack UUID:** 11111111-1111-1111-1111-111111111111\n"
    )
    stack = _parse_stack_markdown(text)
    assert stack.layers[0].sample.url == "https://splice.com/sounds/sample/def456/some-slug"


# --- _confirm_or_exit (cli.py) ----------------------------------------------


def _settings(tmp_path: Path, *, confirm_default: bool = False):
    from splice_cli.config import Settings

    return Settings(
        session_path=tmp_path / "session.json",
        download_dir=tmp_path / "downloads",
        budget_path=tmp_path / "budget.json",
        mcp_session_path=tmp_path / "mcp-session.json",
        confirm_before_download=confirm_default,
    )


def _sample(url="https://splice.com/sounds/sample/abc/x") -> Sample:
    return Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", url=url)


def test_confirm_or_exit_noop_when_off(monkeypatch, tmp_path, capsys):
    called = False

    def _boom(*a, **kw):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli.typer, "confirm", _boom)
    cli._confirm_or_exit(_settings(tmp_path), _sample(), confirm=None)
    assert not called  # never even asked


def test_confirm_or_exit_env_default_turns_it_on(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: True)
    # confirm=None (not passed on the command line) falls back to the
    # settings default — this pins that SPLICE_CONFIRM_BEFORE_DOWNLOAD alone
    # (no --confirm flag) is enough to trigger the gate.
    cli._confirm_or_exit(_settings(tmp_path, confirm_default=True), _sample(), confirm=None)


def test_confirm_or_exit_explicit_no_confirm_overrides_env_default(monkeypatch, tmp_path):
    called = False

    def _boom(*a, **kw):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(cli.typer, "confirm", _boom)
    # Settings default is ON, but an explicit --no-confirm (confirm=False) must win.
    cli._confirm_or_exit(_settings(tmp_path, confirm_default=True), _sample(), confirm=False)
    assert not called


def test_confirm_or_exit_decline_raises_exit_1(monkeypatch, tmp_path):
    import typer

    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: False)
    try:
        cli._confirm_or_exit(_settings(tmp_path), _sample(), confirm=True)
        raise AssertionError("expected typer.Exit")
    except typer.Exit as exc:
        assert exc.exit_code == 1


def test_confirm_or_exit_prints_url_and_price(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: True)
    cli._confirm_or_exit(
        _settings(tmp_path), _sample(url="https://splice.com/sounds/sample/xyz/x"),
        confirm=True, price=3.5,
    )
    out = capsys.readouterr().out
    assert "splice.com/sounds/sample/xyz" in out
    assert "3.5" in out


def test_confirm_or_exit_no_url_shows_fallback_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: True)
    cli._confirm_or_exit(_settings(tmp_path), _sample(url=None), confirm=True)
    out = capsys.readouterr().out
    assert "no direct listen link" in out


# --- End-to-end via CliRunner (download/buy --confirm) ----------------------


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


def _write_budget(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"allowance": 100.0, "spent": 0.0, "per_item_cap": 50.0, "log": [], "baseline_credits": None})
    )


class _FakeSpliceClient:
    def __init__(self, sample: Sample):
        self.sample = sample
        self.download_calls = 0
        self.purchase_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_sample(self, uuid):
        return self.sample

    def purchase(self, sample):
        self.purchase_calls += 1

    def download(self, sample, dest: Path):
        self.download_calls += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-audio-bytes")
        return dest


def _patch_graphql(monkeypatch, fake_client: _FakeSpliceClient) -> None:
    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: fake_client)


def test_download_confirm_accept_proceeds(monkeypatch, tmp_path):
    fake = _FakeSpliceClient(Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", licensed=True))
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["download", "u", "--backend", "graphql", "--confirm"],
        input="y\n", env=_env(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert fake.download_calls == 1


def test_download_confirm_decline_aborts_without_downloading(monkeypatch, tmp_path):
    fake = _FakeSpliceClient(Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", licensed=True))
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["download", "u", "--backend", "graphql", "--confirm"],
        input="n\n", env=_env(tmp_path),
    )
    assert result.exit_code == 1, result.output
    assert fake.download_calls == 0
    assert "Cancelled" in result.output


def test_download_no_confirm_flag_never_prompts(monkeypatch, tmp_path):
    """Pins the default, agent-driven behavior: with no --confirm and no env
    default, download proceeds with zero stdin interaction needed."""
    fake = _FakeSpliceClient(Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", licensed=True))
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(cli.app, ["download", "u", "--backend", "graphql"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert fake.download_calls == 1


def test_buy_confirm_shows_price_and_decline_stops_before_purchase(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake = _FakeSpliceClient(
        Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", licensed=False, price=2.0, price_currency="Credits")
    )
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["buy", "u", "--backend", "graphql", "--confirm"],
        input="n\n", env=_env(tmp_path),
    )
    assert result.exit_code == 1, result.output
    assert fake.purchase_calls == 0  # declined BEFORE spending anything
    assert "2" in result.output  # the price was shown


def test_buy_confirm_env_default(monkeypatch, tmp_path):
    """SPLICE_CONFIRM_BEFORE_DOWNLOAD alone (no --confirm flag) triggers the gate."""
    _write_budget(tmp_path / "budget.json")
    fake = _FakeSpliceClient(
        Sample(uuid="h", catalog_uuid="u", name="Pack/foo.wav", licensed=False, price=2.0, price_currency="Credits")
    )
    _patch_graphql(monkeypatch, fake)

    env = _env(tmp_path)
    env["SPLICE_CONFIRM_BEFORE_DOWNLOAD"] = "true"
    result = runner.invoke(cli.app, ["buy", "u", "--backend", "graphql"], input="n\n", env=env)
    assert result.exit_code == 1, result.output
    assert fake.purchase_calls == 0
