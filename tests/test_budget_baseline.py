"""HARDENING 7 regression test — `budget set`'s lazy baseline capture.

Bug: if a purchase already happened (recorded in `bud.spent`) before the
live-credit baseline was ever successfully captured, using the raw live
balance as the baseline made `reconcile` see that already-ledgered spend
as unexplained "drift" a second time. The fix: capture
`baseline_credits = live_credits + bud.spent` so the baseline represents
the pre-spend state, consistent with the ledger.
"""

from __future__ import annotations

import json
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


def _write_budget_with_prior_spend(path: Path, *, spent: float) -> None:
    """Simulate a purchase that already happened (and was ledgered) before
    any `budget set` call ever reached Splice to capture a baseline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "allowance": 100.0,
                "spent": spent,
                "per_item_cap": 50.0,
                "log": [
                    {"name": "foo.wav", "catalog_uuid": "u1", "price": spent, "currency": "Credits", "remaining_after": 100 - spent}
                ],
                "baseline_credits": None,
            }
        )
    )


class _FakeSpliceClient:
    def __init__(self, live_credits: float):
        self.live_credits = live_credits

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_credits(self):
        return self.live_credits


def test_baseline_captured_after_a_spend_yields_zero_drift(monkeypatch, tmp_path):
    # A purchase of 3 credits already happened and is in the ledger; the
    # account's live balance (997) already reflects that spend out of an
    # original 1000.
    _write_budget_with_prior_spend(tmp_path / "budget.json", spent=3.0)
    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: _FakeSpliceClient(live_credits=997.0))

    set_result = runner.invoke(cli.app, ["budget", "set", "100"], env=_env(tmp_path))
    assert set_result.exit_code == 0, set_result.output

    budget_after_set = json.loads((tmp_path / "budget.json").read_text())
    # baseline should be live (997) + already-spent (3) = 1000, not bare 997.
    assert budget_after_set["baseline_credits"] == 1000.0

    reconcile_result = runner.invoke(cli.app, ["budget", "reconcile"], env=_env(tmp_path))
    assert reconcile_result.exit_code == 0, reconcile_result.output
    assert "Ledger matches" in reconcile_result.output
    assert "Drift" not in reconcile_result.output


def test_baseline_captured_with_no_prior_spend_is_unaffected(monkeypatch, tmp_path):
    _write_budget_with_prior_spend(tmp_path / "budget.json", spent=0.0)
    monkeypatch.setattr(cli, "_session", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "SpliceClient", lambda sess: _FakeSpliceClient(live_credits=1000.0))

    runner.invoke(cli.app, ["budget", "set", "100"], env=_env(tmp_path))

    budget_after_set = json.loads((tmp_path / "budget.json").read_text())
    assert budget_after_set["baseline_credits"] == 1000.0
