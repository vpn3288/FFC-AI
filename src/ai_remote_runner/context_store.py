from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .context import estimate_tokens
from .storage import atomic_write_json


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
            "exchanges": [],
        }

    def add_exchange(self, conversation_id: str, provider: str, *texts: str) -> dict[str, Any]:
        state = self.load(conversation_id, provider)
        state["context_used_tokens"] += estimate_tokens(*texts)
        limit = max(1, int(state["context_limit_tokens"]))
        state["context_used_percent"] = int((state["context_used_tokens"] / limit) * 100)
        state.setdefault("exchanges", []).append({"time": int(time.time()), "texts": list(texts)})
        state["exchanges"] = state["exchanges"][-20:]
        atomic_write_json(self.path(conversation_id), state)
        return state

    def compact(self, conversation_id: str, provider: str = "claude-code") -> dict[str, Any]:
        old = self.load(conversation_id, provider)
        new_id = str(uuid.uuid4())
        summary_path = self.root / f"{conversation_id}-summary-{int(time.time())}.md"
        lines = [
            "# Context Summary",
            "",
            f"old_conversation_id: {conversation_id}",
            f"new_conversation_id: {new_id}",
            f"provider: {provider}",
            "",
            "## Recent Exchanges",
            "",
        ]
        for index, exchange in enumerate(old.get("exchanges", [])[-10:], 1):
            lines.append(f"### Exchange {index}")
            for text in exchange.get("texts", []):
                preview = text.strip()[:4000]
                if preview:
                    lines.append(preview)
                    lines.append("")
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        new_state = self.load(new_id, provider)
        summary_text = summary_path.read_text(encoding="utf-8")
        new_state["context_used_tokens"] = estimate_tokens(summary_text)
        new_state["context_used_percent"] = int((new_state["context_used_tokens"] / max(1, int(new_state["context_limit_tokens"]))) * 100)
        new_state["summary_artifact"] = str(summary_path)
        atomic_write_json(self.path(new_id), new_state)
        return {"old_conversation_id": conversation_id, "new_conversation_id": new_id, "summary_artifact": str(summary_path)}
