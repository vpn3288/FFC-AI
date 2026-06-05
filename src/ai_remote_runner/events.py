from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def status_event(run_id: str, phase: str, message_zh: str, provider: str = "runner") -> dict[str, Any]:
    return {
        "event_id": f"{run_id}:{phase}:{int(time.time() * 1000)}",
        "time": int(time.time()),
        "run_id": run_id,
        "provider": provider,
        "phase": phase,
        "public_message_zh": message_zh,
        "token_free": True,
        "redaction_applied": True,
    }


class EventSink:
    def __init__(self, path: Path, webhook_url: str | None = None, observer: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.path = path
        self.webhook_url = webhook_url
        self.observer = observer
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        if self.observer:
            try:
                self.observer(event)
            except Exception as exc:  # pragma: no cover - observer failures are best-effort status fanout.
                failure = {"event": event, "observer_error": str(exc), "time": int(time.time())}
                with self.path.with_suffix(".observer-failures.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(failure, ensure_ascii=False, sort_keys=True) + "\n")
        if self.webhook_url:
            self._post_mattermost(event)

    def _post_mattermost(self, event: dict[str, Any]) -> None:
        body = json.dumps(
            {
                "text": f"[{event.get('phase')}] {event.get('public_message_zh', '')}",
                "props": {"run_id": event.get("run_id"), "provider": event.get("provider")},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        for attempt in range(3):
            try:
                urllib.request.urlopen(request, timeout=10).read()
                return
            except (urllib.error.URLError, TimeoutError):
                if attempt < 2:
                    time.sleep(2**attempt)
        with self.path.with_suffix(".post-failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
