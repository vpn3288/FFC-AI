from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import estimate_tokens


class ContextStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, conversation_id: str) -> Path:
        return self.root / f"{conversation_id}.json"

    def load(self, conversation_id: str, provider: str = "claude-code", limit: int = 200000) -> dict[str, Any]:
        path = self.path(conversation_id)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {
            "conversation_id": conversation_id,
            "provider": provider,
            "context_limit_tokens": limit,
            "context_used_tokens": 0,
            "measurement": "estimated",
            "auto_compact_threshold_percent": 80,
            "hard_stop_threshold_percent": 95,
        }

    def add_exchange(self, conversation_id: str, provider: str, *texts: str) -> dict[str, Any]:
        state = self.load(conversation_id, provider)
        state["context_used_tokens"] += estimate_tokens(*texts)
        limit = max(1, int(state["context_limit_tokens"]))
        state["context_used_percent"] = int((state["context_used_tokens"] / limit) * 100)
        self.path(conversation_id).write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return state
