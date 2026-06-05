from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .commands import parse_command
from .executor import RunnerRuntime, execute
from .paths import state_root, workspace_root
from .storage import atomic_write_json


MAX_TELEGRAM_MESSAGE_CHARS = 3900
DEFAULT_TELEGRAM_RESERVED_USD = 0.05


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    allowed_chat_ids: set[str]
    api_base: str = "https://api.telegram.org"
    poll_timeout_seconds: int = 30
    reserved_usd: float = DEFAULT_TELEGRAM_RESERVED_USD

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        raw_ids = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        allowed = {item.strip() for item in raw_ids.split(",") if item.strip()}
        return cls(
            token=token,
            allowed_chat_ids=allowed,
            api_base=os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
            poll_timeout_seconds=int(os.environ.get("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")),
            reserved_usd=float(os.environ.get("TELEGRAM_RESERVED_USD", str(DEFAULT_TELEGRAM_RESERVED_USD))),
        )


class TelegramClient:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config

    def _url(self, method: str) -> str:
        return f"{self.config.api_base}/bot{self.config.token}/{method}"

    def call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 35) -> dict[str, Any]:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self._url(method),
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        response = request.urlopen(req, timeout=timeout)
        data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(f"telegram_{method}_failed: {data}")
        return data

    def get_updates(self, offset: int | None, timeout_seconds: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout_seconds, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return list(self.call("getUpdates", payload, timeout=timeout_seconds + 10).get("result", []))

    def send_message(self, chat_id: str, text: str) -> None:
        chunks = [text[i : i + MAX_TELEGRAM_MESSAGE_CHARS] for i in range(0, len(text), MAX_TELEGRAM_MESSAGE_CHARS)] or [""]
        for chunk in chunks:
            self.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )


class TelegramBot:
    def __init__(self, config: TelegramConfig, client: TelegramClient, runtime: RunnerRuntime, state: Path) -> None:
        self.config = config
        self.client = client
        self.runtime = runtime
        self.state = state
        self.offset_path = state / "telegram-offset.json"
        self.confirmations_path = state / "telegram-confirmations.json"
        self.state.mkdir(parents=True, exist_ok=True)

    def load_offset(self) -> int | None:
        if not self.offset_path.exists():
            return None
        try:
            return int(json.loads(self.offset_path.read_text(encoding="utf-8")).get("offset"))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def save_offset(self, offset: int) -> None:
        atomic_write_json(self.offset_path, {"offset": offset})

    def confirmations(self) -> dict[str, dict[str, Any]]:
        if not self.confirmations_path.exists():
            return {}
        try:
            raw = json.loads(self.confirmations_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        now = int(time.time())
        return {token: item for token, item in raw.items() if now - int(item.get("created_at", 0)) <= 600}

    def save_confirmations(self, data: dict[str, dict[str, Any]]) -> None:
        atomic_write_json(self.confirmations_path, data)

    def chat_allowed(self, chat_id: str) -> bool:
        return chat_id in self.config.allowed_chat_ids

    def pairing_hint(self, chat_id: str) -> str:
        return (
            "Telegram bot 已安装，但这个 chat 还没有配对。\n"
            f"chat_id: {chat_id}\n"
            "在 runner 机器上运行 pair-telegram.sh，把这个 chat_id 加入允许列表后再发送 /ai 状态。"
        )

    def normalize_text(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("/ai@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/ai@"):
                return "/ai " + tail.strip()
        if stripped == "/start":
            return "/ai 帮助"
        if stripped == "/help":
            return "/ai 帮助"
        if stripped == "/status":
            return "/ai 状态"
        return stripped

    def parsed_for_text(self, text: str) -> dict[str, Any]:
        parsed = parse_command(text, allow_bare=True)
        if parsed.get("status") == "rejected" and parsed.get("error") == "command_must_start_with_/ai" and not text.startswith("/"):
            return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": text}, "requires_confirmation": False}
        return parsed

    def response_text(self, response: dict[str, Any]) -> str:
        if response.get("status") == "needs_confirmation":
            token = response.get("data", {}).get("confirmation_token", "")
            return f"{response.get('message_zh', '需要确认')}\n发送：/ai 确认 {token}".strip()
        if response.get("error"):
            error_data = response["error"]
            return f"{response.get('message_zh', '执行失败')}: {error_data.get('detail') or error_data.get('code')}"
        data = response.get("data", {})
        output = data.get("output") if isinstance(data, dict) else None
        if output:
            return str(output)
        if isinstance(data, dict) and "items" in data:
            lines = [response.get("message_zh", "索引已生成")]
            for item in data.get("items", [])[:40]:
                lines.append(f"{item.get('usage')}: {item.get('description_zh')}")
            return "\n".join(lines)
        if data:
            detail = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            return f"{response.get('message_zh', response.get('status', 'OK'))}\n{detail}"
        return str(response.get("message_zh") or response.get("status") or "OK")

    def event_text(self, event: dict[str, Any]) -> str | None:
        phase = str(event.get("phase") or "")
        provider = str(event.get("provider") or "runner")
        message = str(event.get("public_message_zh") or "")
        if phase == "queued":
            return f"已收到，正在排队。provider={provider}"
        if phase == "calling_model":
            return f"正在调用 {provider}，请稍等。"
        if phase == "warning":
            return message or "上下文接近阈值。"
        if phase == "error":
            return f"{provider} 执行出错：{event.get('error') or message or 'unknown'}"
        return None

    def default_provider(self) -> str:
        path = self.runtime.state / "provider-selection.json"
        if path.exists():
            try:
                provider = json.loads(path.read_text(encoding="utf-8")).get("provider")
                if provider:
                    return str(provider)
            except json.JSONDecodeError:
                pass
        return "claude-code"

    def status_interval_seconds(self) -> float:
        raw = os.environ.get("TELEGRAM_STATUS_INTERVAL_SECONDS", "30")
        try:
            value = float(raw)
        except ValueError:
            return 30.0
        return max(0.0, value)

    def heartbeat_text(self, provider: str, started_at: float) -> str:
        elapsed = max(1, int(time.time() - started_at))
        return f"仍在运行，已等待 {elapsed}s。provider={provider}，可能是在模型排队、联网等待或生成中。"

    def execute_with_status(
        self,
        chat_id: str,
        parsed: dict[str, Any],
        envelope: dict[str, Any],
        runtime: RunnerRuntime,
        provider: str,
    ) -> dict[str, Any]:
        holder: dict[str, dict[str, Any]] = {}

        def run() -> None:
            try:
                holder["response"] = execute(parsed, envelope, runtime)
            except Exception as exc:  # pragma: no cover - defensive guard for daemon mode
                holder["response"] = {
                    "request_id": envelope.get("request_id"),
                    "status": "failed",
                    "error": {"code": "telegram_execute_failed", "detail": str(exc)},
                    "data": {},
                }

        started_at = time.time()
        worker = threading.Thread(target=run, name="telegram-runner-task", daemon=True)
        worker.start()
        interval = self.status_interval_seconds()
        if interval <= 0:
            worker.join()
            return holder.get("response") or {
                "request_id": envelope.get("request_id"),
                "status": "failed",
                "error": {"code": "telegram_execute_missing_response", "detail": "missing_response"},
                "data": {},
            }
        while worker.is_alive():
            worker.join(interval)
            if worker.is_alive():
                self.client.send_message(chat_id, self.heartbeat_text(provider, started_at))
        return holder.get("response") or {
            "request_id": envelope.get("request_id"),
            "status": "failed",
            "error": {"code": "telegram_execute_missing_response", "detail": "missing_response"},
            "data": {},
        }

    def task_runtime(self, chat_id: str) -> tuple[RunnerRuntime, str]:
        provider = self.default_provider()
        self.client.send_message(chat_id, f"已收到任务，准备调用 {provider}。")

        def notify(event: dict[str, Any]) -> None:
            text = self.event_text(event)
            if text:
                self.client.send_message(chat_id, text)

        return self.runtime.with_event_observer(notify), provider

    def execute_text(self, chat_id: str, message: dict[str, Any], text: str) -> dict[str, Any]:
        request_id = f"telegram:{chat_id}:{message.get('message_id') or uuid.uuid4()}"
        parsed = self.parsed_for_text(text)
        if parsed.get("canonical_action") == "confirm":
            token = " ".join(parsed.get("args", {}).get("tail", []))
            pending = self.confirmations()
            item = pending.pop(token, None)
            self.save_confirmations(pending)
            if not item:
                return {"request_id": request_id, "status": "rejected", "error": {"code": "confirmation_not_found", "detail": "confirmation_not_found"}, "data": {}}
            item["envelope"]["confirmed"] = True
            if item["parsed"].get("canonical_action") == "task.run":
                runtime, provider = self.task_runtime(chat_id)
                return self.execute_with_status(chat_id, item["parsed"], item["envelope"], runtime, provider)
            return execute(item["parsed"], item["envelope"], self.runtime)

        envelope = {
            "request_id": request_id,
            "platform": "telegram",
            "chat_id": chat_id,
            "sender_id": str(message.get("from", {}).get("id", "")),
            "sender_name": message.get("from", {}).get("username") or message.get("from", {}).get("first_name", ""),
            "raw_text": text,
            "reserved_usd": self.config.reserved_usd,
        }
        runtime = self.runtime
        if parsed.get("canonical_action") == "task.run":
            runtime, provider = self.task_runtime(chat_id)
            response = self.execute_with_status(chat_id, parsed, envelope, runtime, str(provider))
        else:
            response = execute(parsed, envelope, runtime)
        if response.get("status") == "needs_confirmation":
            token = response.get("data", {}).get("confirmation_token")
            if token:
                pending = self.confirmations()
                pending[token] = {"created_at": int(time.time()), "parsed": parsed, "envelope": envelope}
                self.save_confirmations(pending)
        return response

    def handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id_value = chat.get("id")
        text = message.get("text")
        if chat_id_value is None or not isinstance(text, str):
            return False
        chat_id = str(chat_id_value)
        if not self.chat_allowed(chat_id):
            self.client.send_message(chat_id, self.pairing_hint(chat_id))
            return True
        normalized = self.normalize_text(text)
        response = self.execute_text(chat_id, message, normalized)
        self.client.send_message(chat_id, self.response_text(response))
        return True

    def poll_forever(self) -> None:
        offset = self.load_offset()
        while True:
            try:
                updates = self.client.get_updates(offset, self.config.poll_timeout_seconds)
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    self.handle_update(update)
                    offset = max(offset or 0, update_id + 1)
                    self.save_offset(offset)
            except (error.URLError, TimeoutError, RuntimeError):
                time.sleep(5)


def serve() -> None:
    config = TelegramConfig.from_env()
    root = Path(os.environ.get("AI_REMOTE_STATE", str(state_root())))
    runtime = RunnerRuntime(root, Path(os.environ.get("AI_WORKSPACE_ROOT", str(workspace_root()))), os.environ.get("MATTERMOST_WEBHOOK_URL"))
    TelegramBot(config, TelegramClient(config), runtime, root).poll_forever()
