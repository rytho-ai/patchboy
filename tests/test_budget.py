"""Budget is the one place real money moves — test its boundaries directly."""

from __future__ import annotations

from pathlib import Path

from splice_cli import budget


def test_can_afford_within_both_limits():
    b = budget.Budget(allowance=10, spent=0, per_item_cap=5)
    ok, reason = b.can_afford(3)
    assert ok
    assert reason is None


def test_can_afford_exact_boundary_is_allowed():
    # Boundary semantics: price == cap or price == remaining should PASS
    # (the check is `price > limit`, not `>=`) — an agent shouldn't be
    # surprised that spending exactly the stated cap is refused.
    b = budget.Budget(allowance=10, spent=0, per_item_cap=5)
    ok, _ = b.can_afford(5)  # exactly the per-item cap
    assert ok

    b2 = budget.Budget(allowance=10, spent=7, per_item_cap=20)
    ok2, _ = b2.can_afford(3)  # exactly the remaining pool (10 - 7)
    assert ok2


def test_can_afford_rejects_over_per_item_cap():
    b = budget.Budget(allowance=100, spent=0, per_item_cap=5)
    ok, reason = b.can_afford(5.01)
    assert not ok
    assert "per-item cap" in reason


def test_can_afford_rejects_over_remaining_even_under_cap():
    b = budget.Budget(allowance=10, spent=9, per_item_cap=20)
    ok, reason = b.can_afford(2)  # under the cap, but only 1 remains
    assert not ok
    assert "remaining budget" in reason


def test_record_updates_spent_and_appends_log():
    b = budget.Budget(allowance=10, spent=0)
    b.record(budget.Purchase(name="foo.wav", catalog_uuid="u1", price=1, currency="Credits"))
    assert b.spent == 1
    assert b.remaining == 9
    assert len(b.log) == 1
    assert b.log[0]["name"] == "foo.wav"
    assert b.log[0]["remaining_after"] == 9


def test_reconcile_no_baseline_reports_nothing():
    b = budget.Budget(allowance=10, spent=3, baseline_credits=None)
    expected_spent, drift = b.reconcile(live_credits=999)
    assert expected_spent == 0.0
    assert drift == 0.0


def test_reconcile_matches_when_ledger_is_correct():
    b = budget.Budget(allowance=10, spent=2, baseline_credits=100)
    expected_spent, drift = b.reconcile(live_credits=98)  # spent exactly 2
    assert expected_spent == 2
    assert drift == 0


def test_reconcile_flags_drift_when_ledger_disagrees():
    b = budget.Budget(allowance=10, spent=0, baseline_credits=100)
    expected_spent, drift = b.reconcile(live_credits=95)  # something spent 5 we didn't record
    assert expected_spent == 5
    assert drift == 5  # expected_spent - spent(0)


def test_save_load_round_trip(tmp_path: Path):
    path = tmp_path / "budget.json"
    original = budget.Budget(allowance=15, spent=3, per_item_cap=8, baseline_credits=200)
    original.record(budget.Purchase(name="x.wav", catalog_uuid="u1", price=3, currency="Credits"))
    budget.save(original, path)

    loaded = budget.load(path)
    assert loaded.allowance == 15
    assert loaded.per_item_cap == 8
    assert loaded.baseline_credits == 200
    assert len(loaded.log) == 1
    assert loaded.log[0]["catalog_uuid"] == "u1"


def test_save_is_atomic_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "budget.json"
    budget.save(budget.Budget(allowance=5), path)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_load_missing_file_returns_empty_budget(tmp_path: Path):
    b = budget.load(tmp_path / "does-not-exist.json")
    assert b.allowance == 0
    assert b.spent == 0
    assert b.remaining == 0
