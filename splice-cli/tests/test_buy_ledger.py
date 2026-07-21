"""FIX 1 regression tests — the money ledger.

Invariant under test: `bud.record()` + `budget_store.save()` must happen as
soon as the credit-charging step succeeds (GraphQL: client.purchase();
MCP: McpClient.download_charge()), independent of whether the subsequent
file transfer succeeds. A transfer failure AFTER a successful charge must
still leave the spend recorded — otherwise a credit is spent on Splice's
side but silently dropped from our local budget ledger.

Exercises `buy()` end-to-end via CliRunner with SpliceClient/McpClient
faked out — no real network, no real money, no real session files.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splice_cli import cli
from splice_cli.models import Sample

runner = CliRunner()


def _sample(*, licensed=False, price=2.0, catalog_uuid="uuid-1", name="Pack/foo.wav") -> Sample:
    return Sample(
        uuid="hash-1",
        catalog_uuid=catalog_uuid,
        name=name,
        licensed=licensed,
        price=price,
        price_currency="Credits",
    )


def _write_budget(path: Path, *, allowance: float = 100.0, per_item_cap: float = 50.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "allowance": allowance,
                "spent": 0.0,
                "per_item_cap": per_item_cap,
                "log": [],
                "baseline_credits": None,
            }
        )
    )


def _env(tmp_path: Path) -> dict:
    return {
        "SPLICE_SESSION_PATH": str(tmp_path / "session.json"),
        "SPLICE_DOWNLOAD_DIR": str(tmp_path / "downloads"),
        "SPLICE_BUDGET_PATH": str(tmp_path / "budget.json"),
        "SPLICE_MCP_SESSION_PATH": str(tmp_path / "mcp-session.json"),
    }


class _FakeSpliceClient:
    """Stands in for SpliceClient — only the methods buy() actually calls."""

    def __init__(self, sample: Sample, *, purchase_error=None, download_error=None):
        self.sample = sample
        self.purchase_error = purchase_error
        self.download_error = download_error
        self.purchase_calls = 0
        self.download_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_sample(self, uuid):
        return self.sample

    def purchase(self, sample):
        self.purchase_calls += 1
        if self.purchase_error:
            raise self.purchase_error

    def download(self, sample, dest: Path):
        self.download_calls += 1
        if self.download_error:
            raise self.download_error
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-audio-bytes")
        return dest


class _FakeMcpClient:
    """Stands in for McpClient — mirrors the real download_charge/
    stream_to_disk split so buy()'s between-steps record can be exercised.
    """

    def __init__(self, *, charge=("https://example.com/file.wav", "file.wav", 42), stream_error=None):
        self._charge = charge
        self.stream_error = stream_error
        self.charge_calls = 0
        self.stream_calls = 0
        self.last_remaining_downloads = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def download_charge(self, sample):
        self.charge_calls += 1
        _, _, remaining = self._charge
        self.last_remaining_downloads = remaining
        return self._charge

    def stream_to_disk(self, url, filename, dest: Path):
        self.stream_calls += 1
        if self.stream_error:
            raise self.stream_error
        target = dest if dest.suffix else dest / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-audio-bytes")
        return target


def _patch_graphql(monkeypatch, fake_client: _FakeSpliceClient) -> None:
    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: fake_client)


def _patch_mcp(monkeypatch, fake_client: _FakeMcpClient) -> None:
    monkeypatch.setattr(cli, "_mcp_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "McpClient", lambda sess: fake_client)


def _budget_spent(tmp_path: Path) -> float:
    return json.loads((tmp_path / "budget.json").read_text())["spent"]


# --- GraphQL backend ---------------------------------------------------


def test_graphql_download_failure_after_charge_still_records_spend(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake = _FakeSpliceClient(_sample(), download_error=RuntimeError("disk full"))
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["buy", "uuid-1", "--backend", "graphql"], env=_env(tmp_path)
    )

    assert fake.purchase_calls == 1
    # HARDENING 8: this must be a CLEAN exit 1 (not a raw traceback, and not
    # exit 2 — no human auth step is needed since the purchase already
    # succeeded) with a message pointing at the free re-download path.
    assert result.exit_code == 1, result.output
    assert result.exc_info is None or not isinstance(result.exc_info[1], RuntimeError)
    assert "disk full" in result.output
    assert "splice download uuid-1" in result.output
    assert _budget_spent(tmp_path) == 2.0


def test_graphql_happy_path_records_exactly_once(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake = _FakeSpliceClient(_sample())
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["buy", "uuid-1", "--backend", "graphql"], env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert fake.purchase_calls == 1
    assert fake.download_calls == 1
    budget = json.loads((tmp_path / "budget.json").read_text())
    assert budget["spent"] == 2.0
    assert len(budget["log"]) == 1


def test_graphql_already_licensed_records_nothing(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake = _FakeSpliceClient(_sample(licensed=True))
    _patch_graphql(monkeypatch, fake)

    result = runner.invoke(
        cli.app, ["buy", "uuid-1", "--backend", "graphql"], env=_env(tmp_path)
    )

    assert result.exit_code == 0, result.output
    assert fake.purchase_calls == 0
    assert _budget_spent(tmp_path) == 0.0


# --- MCP backend (default) ----------------------------------------------


def test_mcp_stream_failure_after_charge_still_records_spend(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake_graphql = _FakeSpliceClient(_sample())
    fake_mcp = _FakeMcpClient(stream_error=RuntimeError("S3 connection reset"))
    _patch_graphql(monkeypatch, fake_graphql)
    _patch_mcp(monkeypatch, fake_mcp)

    result = runner.invoke(cli.app, ["buy", "uuid-1"], env=_env(tmp_path))

    assert fake_mcp.charge_calls == 1
    # HARDENING 8: clean exit 1, not a raw traceback or an exit-2 handoff.
    assert result.exit_code == 1, result.output
    assert result.exc_info is None or not isinstance(result.exc_info[1], RuntimeError)
    assert "S3 connection reset" in result.output
    assert "splice download uuid-1" in result.output
    assert _budget_spent(tmp_path) == 2.0


def test_mcp_stream_failure_without_a_fresh_charge_is_not_wrapped(monkeypatch, tmp_path):
    """An already-licensed sample's free re-download never spent anything,
    so a transfer failure here is an ordinary failure, not the "you already
    paid" case — it must NOT get the post-charge clean-handoff message."""
    _write_budget(tmp_path / "budget.json")
    fake_graphql = _FakeSpliceClient(_sample(licensed=True))
    fake_mcp = _FakeMcpClient(stream_error=RuntimeError("S3 connection reset"))
    _patch_graphql(monkeypatch, fake_graphql)
    _patch_mcp(monkeypatch, fake_mcp)

    result = runner.invoke(cli.app, ["buy", "uuid-1"], env=_env(tmp_path))

    assert result.exit_code != 0
    assert "already spent" not in result.output
    assert _budget_spent(tmp_path) == 0.0


def test_mcp_happy_path_records_exactly_once(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake_graphql = _FakeSpliceClient(_sample())
    fake_mcp = _FakeMcpClient()
    _patch_graphql(monkeypatch, fake_graphql)
    _patch_mcp(monkeypatch, fake_mcp)

    result = runner.invoke(cli.app, ["buy", "uuid-1"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert fake_mcp.charge_calls == 1
    assert fake_mcp.stream_calls == 1
    budget = json.loads((tmp_path / "budget.json").read_text())
    assert budget["spent"] == 2.0
    assert len(budget["log"]) == 1


def test_mcp_already_licensed_fallthrough_records_nothing(monkeypatch, tmp_path):
    _write_budget(tmp_path / "budget.json")
    fake_graphql = _FakeSpliceClient(_sample(licensed=True))
    fake_mcp = _FakeMcpClient()
    _patch_graphql(monkeypatch, fake_graphql)
    _patch_mcp(monkeypatch, fake_mcp)

    result = runner.invoke(cli.app, ["buy", "uuid-1"], env=_env(tmp_path))

    assert result.exit_code == 0, result.output
    assert fake_mcp.charge_calls == 1  # still fetches the file — just free
    assert _budget_spent(tmp_path) == 0.0
