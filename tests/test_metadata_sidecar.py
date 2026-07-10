"""Metadata sidecar — the JSON file written alongside a downloaded sample."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from splice_cli.cli import _sidecar_path, _write_metadata_sidecar
from splice_cli.models import Sample


def _sample() -> Sample:
    return Sample(
        uuid="content-hash-abc",
        catalog_uuid="catalog-uuid-123",
        name="Packs/Dusty Drums/Kick_01.wav",
        asset_type="sample",
        pack_name="Dusty Drums",
        provider_name="Splice",
        bpm=92,
        key="C min",
        duration_seconds=1.25,
        tags=["drums", "kick", "lofi"],
        licensed=True,
        price=1.5,
        price_currency="Credits",
        preview_url="https://example.com/preview.mp3",
    )


def test_sidecar_path_naming(tmp_path: Path):
    target = tmp_path / "foo.wav"
    result = _sidecar_path(target)
    assert str(result).endswith("foo.wav.splice.json")


def test_write_metadata_sidecar_contains_sample_fields(tmp_path: Path):
    target = tmp_path / "Kick_01.wav"
    sample = _sample()
    path = _write_metadata_sidecar(target, sample, backend="mcp")

    assert path == _sidecar_path(target)
    payload = json.loads(path.read_text())

    assert payload["bpm"] == sample.bpm
    assert payload["key"] == sample.key
    assert payload["catalog_uuid"] == sample.catalog_uuid
    assert payload["name"] == sample.name
    assert payload["tags"] == sample.tags
    assert payload["licensed"] == sample.licensed
    assert payload["pack_name"] == sample.pack_name
    assert payload["price"] == sample.price
    assert payload["price_currency"] == sample.price_currency


def test_write_metadata_sidecar_backend_field(tmp_path: Path):
    target = tmp_path / "Kick_01.wav"
    path = _write_metadata_sidecar(target, _sample(), backend="graphql")
    payload = json.loads(path.read_text())
    assert payload["backend"] == "graphql"


def test_write_metadata_sidecar_downloaded_at_is_iso8601(tmp_path: Path):
    target = tmp_path / "Kick_01.wav"
    path = _write_metadata_sidecar(target, _sample(), backend="mcp")
    payload = json.loads(path.read_text())
    # Must not raise — confirms it's a valid ISO-8601 timestamp.
    _dt.datetime.fromisoformat(payload["downloaded_at"])


def test_write_metadata_sidecar_price_paid_present_when_given(tmp_path: Path):
    target = tmp_path / "Kick_01.wav"
    path = _write_metadata_sidecar(target, _sample(), backend="graphql", price=2.5)
    payload = json.loads(path.read_text())
    assert payload["price_paid"] == 2.5


def test_write_metadata_sidecar_price_paid_absent_when_none(tmp_path: Path):
    target = tmp_path / "Kick_01.wav"
    path = _write_metadata_sidecar(target, _sample(), backend="mcp")
    payload = json.loads(path.read_text())
    assert "price_paid" not in payload
