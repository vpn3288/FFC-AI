from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


DEFAULT_STATE = {
    "daily_usd_limit": 10.0,
    "monthly_usd_limit": 100.0,
    "daily_used_usd_estimate": 0.0,
    "monthly_used_usd_estimate": 0.0,
    "freeze_on_exceed": True,
    "runs": {},
}


class BudgetLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(DEFAULT_STATE)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        merged.setdefault("runs", {})
        return merged

    def save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def can_reserve(self, amount: float) -> tuple[bool, str]:
        data = self.load()
        if data["daily_used_usd_estimate"] + amount > data["daily_usd_limit"]:
            return False, "daily_budget_exceeded"
        if data["monthly_used_usd_estimate"] + amount > data["monthly_usd_limit"]:
            return False, "monthly_budget_exceeded"
        return True, "ok"

    def reserve(
        self,
        run_id: str,
        provider: str,
        amount: float,
        timeout_seconds: int = 1800,
        max_output_bytes: int = 200000,
    ) -> dict[str, Any]:
        ok, reason = self.can_reserve(amount)
        if not ok:
            raise RuntimeError(reason)
        data = self.load()
        run = {
            "run_id": run_id,
            "provider": provider,
            "reserved_usd": amount,
            "actual_usd": None,
            "timeout_seconds": timeout_seconds,
            "max_output_bytes": max_output_bytes,
            "status": "reserved",
            "created_at": int(time.time()),
        }
        data["runs"][run_id] = run
        data["daily_used_usd_estimate"] += amount
        data["monthly_used_usd_estimate"] += amount
        self.save(data)
        return run

    def complete(self, run_id: str, actual_usd: float | None, status: str = "completed") -> dict[str, Any]:
        data = self.load()
        run = data["runs"][run_id]
        if actual_usd is not None and run.get("actual_usd") is None:
            delta = actual_usd - float(run["reserved_usd"])
            data["daily_used_usd_estimate"] = max(0.0, data["daily_used_usd_estimate"] + delta)
            data["monthly_used_usd_estimate"] = max(0.0, data["monthly_used_usd_estimate"] + delta)
        run["actual_usd"] = actual_usd
        run["status"] = status
        run["completed_at"] = int(time.time())
        self.save(data)
        return run
