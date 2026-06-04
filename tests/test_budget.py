from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_remote_runner.budget import BudgetLedger


class BudgetTests(unittest.TestCase):
    def test_reserve_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            run = ledger.reserve("run-1", "claude-code", 0.5)
            self.assertEqual(run["status"], "reserved")
            done = ledger.complete("run-1", 0.25)
            self.assertEqual(done["actual_usd"], 0.25)
            self.assertEqual(done["status"], "completed")
            self.assertEqual(ledger.load()["daily_used_usd_estimate"], 0.25)

    def test_overspend_is_tracked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            ledger.reserve("run-1", "claude-code", 0.5)
            done = ledger.complete("run-1", 0.75)
            self.assertTrue(done["overspent"])
            self.assertAlmostEqual(ledger.load()["overspend_usd"], 0.25)

    def test_budget_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            ok, reason = ledger.can_reserve(1000)
            self.assertFalse(ok)
            self.assertEqual(reason, "daily_budget_exceeded")


if __name__ == "__main__":
    unittest.main()
