"""Budget — a standing, human-granted spending pool for real purchases.

Design (settled with the user):
- Standing pool, not a recurring allowance: the user tops it up manually via
  `splice budget set`; it never auto-resets. Running dry is a hard stop, not
  a monthly surprise.
- Once granted, purchases within the pool need no per-item confirmation —
  the grant itself IS the standing consent. Every purchase is logged to an
  append-only audit trail so the user can review what was bought after the
  fact without having been asked each time.
- A configurable per-item cap guards against one expensive asset silently
  draining the whole pool in one shot (defaults on; tunable, since "it
  depends on the person" was the explicit answer when asked how strict this
  should be).

Only a human runs `splice budget set` — the skill must never call it on the
user's behalf. That boundary is what keeps this a bounded grant instead of an
open checkbook.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


DEFAULT_PER_ITEM_CAP = 20  # credits; tune via `splice budget set --max-item`


@dataclass
class Purchase:
    name: str
    catalog_uuid: str
    price: float
    currency: str


@dataclass
class Budget:
    allowance: float = 0.0
    spent: float = 0.0
    per_item_cap: float = DEFAULT_PER_ITEM_CAP
    # Append-only audit trail: every purchase made against this budget.
    log: list[dict[str, Any]] = field(default_factory=list)
    # Splice's live credit balance the first time `budget set` ever ran.
    # `reconcile` compares (baseline - live_now) against `spent` to catch
    # drift — set once, never overwritten by later top-ups, since `spent`
    # is itself an all-time counter.
    baseline_credits: float | None = None

    @property
    def remaining(self) -> float:
        return self.allowance - self.spent

    def can_afford(self, price: float) -> tuple[bool, str | None]:
        """Check a purchase against both the per-item cap and remaining pool.

        Returns (ok, reason) — reason is set only when ok is False, for a
        clear message back to the human/agent.
        """
        if price > self.per_item_cap:
            return False, (
                f"Price ({price:g} credits) exceeds the per-item cap "
                f"({self.per_item_cap:g}). Raise it with `splice budget set "
                "--max-item` if this is expected, then retry."
            )
        if price > self.remaining:
            return False, (
                f"Price ({price:g} credits) exceeds remaining budget "
                f"({self.remaining:g}). Top up with `splice budget set`."
            )
        return True, None

    def record(self, purchase: Purchase) -> None:
        self.spent += purchase.price
        self.log.append(
            {
                "name": purchase.name,
                "catalog_uuid": purchase.catalog_uuid,
                "price": purchase.price,
                "currency": purchase.currency,
                "remaining_after": self.remaining,
            }
        )

    def reconcile(self, live_credits: float) -> tuple[float, float]:
        """Compare our local `spent` against Splice's actual credit balance.

        Returns (expected_spent, drift). `expected_spent` is what the live
        balance implies we've spent since the baseline was captured; `drift`
        is expected_spent - self.spent (nonzero means the local ledger and
        Splice's own records disagree — e.g. a purchase made outside this
        tool, or a bug in our accounting).
        """
        if self.baseline_credits is None:
            return 0.0, 0.0  # nothing to compare against yet
        expected_spent = self.baseline_credits - live_credits
        return expected_spent, expected_spent - self.spent


def load(path: Path) -> Budget:
    if not path.exists():
        return Budget()
    data = json.loads(path.read_text())
    return Budget(
        allowance=data.get("allowance", 0.0),
        spent=data.get("spent", 0.0),
        per_item_cap=data.get("per_item_cap", DEFAULT_PER_ITEM_CAP),
        log=data.get("log", []),
        baseline_credits=data.get("baseline_credits"),
    )


def save(budget: Budget, path: Path) -> None:
    """Write atomically (temp file + rename) — this is a money ledger; a
    write that dies halfway must never leave a corrupt/partial file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "allowance": budget.allowance,
                "spent": budget.spent,
                "per_item_cap": budget.per_item_cap,
                "log": budget.log,
                "baseline_credits": budget.baseline_credits,
            },
            indent=1,
        )
    )
    tmp.chmod(0o600)
    os.replace(tmp, path)  # atomic on POSIX — no partial-file window
