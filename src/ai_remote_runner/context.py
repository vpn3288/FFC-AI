from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextState:
    conversation_id: str
    provider: str
    context_limit_tokens: int
    context_used_tokens: int
    measurement: str = "estimated"
    auto_compact_threshold_percent: int = 80
    hard_stop_threshold_percent: int = 95

    @property
    def context_used_percent(self) -> int:
        if self.context_limit_tokens <= 0:
            return 0
        return int((self.context_used_tokens / self.context_limit_tokens) * 100)

    @property
    def needs_warning(self) -> bool:
        return self.context_used_percent >= self.auto_compact_threshold_percent

    @property
    def hard_stop(self) -> bool:
        return self.context_used_percent >= self.hard_stop_threshold_percent


def estimate_tokens(*texts: str) -> int:
    total_bytes = sum(len(text.encode("utf-8")) for text in texts)
    return int(((total_bytes + 3) // 4) * 1.20)
