from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from .commands import parse_command
from .executor import RunnerRuntime, execute
from .paths import state_root, workspace_root
from .phone_render import render_event_text, render_response_text
from .storage import atomic_write_json


MAX_TELEGRAM_MESSAGE_CHARS = 3900
DEFAULT_TELEGRAM_RESERVED_USD = 0.20


def load_config_env(root: Path | None = None) -> None:
    path = (root or state_root()) / "config.env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    allowed_chat_ids: set[str]
    api_base: str = "https://api.telegram.org"
    poll_timeout_seconds: int = 30
    reserved_usd: float = DEFAULT_TELEGRAM_RESERVED_USD
    status_interval_seconds: float = 5.0
    task_ttl_seconds: int = 21600
    clear_webhook_on_startup: bool = True

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
            status_interval_seconds=max(0.0, float(os.environ.get("TELEGRAM_STATUS_INTERVAL_SECONDS", "5"))),
            task_ttl_seconds=max(60, int(os.environ.get("TELEGRAM_TASK_TTL_SECONDS", "21600"))),
            clear_webhook_on_startup=os.environ.get("TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP", "1").lower() not in {"0", "false", "no"},
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

    def get_me(self) -> dict[str, Any]:
        result = self.call("getMe", {}, timeout=15).get("result")
        if not isinstance(result, dict):
            raise RuntimeError("telegram_getMe_missing_result")
        return result

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        return bool(self.call("deleteWebhook", {"drop_pending_updates": drop_pending_updates}, timeout=15).get("result"))

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        last_result: dict[str, Any] = {}
        chunks = [text[i : i + MAX_TELEGRAM_MESSAGE_CHARS] for i in range(0, len(text), MAX_TELEGRAM_MESSAGE_CHARS)] or [""]
        for chunk in chunks:
            result = self.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            ).get("result")
            if isinstance(result, dict):
                last_result = result
        return last_result

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        self.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:MAX_TELEGRAM_MESSAGE_CHARS],
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return True

    def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        self.call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)


@dataclass
class TelegramTask:
    task_id: str
    chat_id: str
    message_id: int | None
    started_at: float
    provider: str
    status_message_id: int | None = None
    last_status_text: str = ""
    done: bool = False
    response: dict[str, Any] | None = None


class TelegramBot:
    def __init__(self, config: TelegramConfig, client: TelegramClient, runtime: RunnerRuntime, state: Path) -> None:
        self.config = config
        self.client = client
        self.runtime = runtime
        self.state = state
        self.offset_path = state / "telegram-offset.json"
        self.confirmations_path = state / "telegram-confirmations.json"
        self.tasks_path = state / "telegram-tasks.json"
        self._tasks: dict[str, TelegramTask] = {}
        self._tasks_lock = threading.Lock()
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

    def save_task_status(self, task: TelegramTask) -> None:
        with self._tasks_lock:
            data: dict[str, Any] = {}
            if self.tasks_path.exists():
                try:
                    loaded = json.loads(self.tasks_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except json.JSONDecodeError:
                    data = {}
            data[task.task_id] = {
                "chat_id": task.chat_id,
                "message_id": task.message_id,
                "status_message_id": task.status_message_id,
                "started_at": int(task.started_at),
                "provider": task.provider,
                "done": task.done,
                "last_status_text": task.last_status_text,
                "response_status": (task.response or {}).get("status"),
            }
            cutoff = int(time.time()) - self.config.task_ttl_seconds
            data = {key: value for key, value in data.items() if int(value.get("started_at", 0)) >= cutoff or not value.get("done")}
            atomic_write_json(self.tasks_path, data)

    def chat_allowed(self, chat_id: str) -> bool:
        return chat_id in self.config.allowed_chat_ids

    def safe_send_message(self, chat_id: str, text: str) -> dict[str, Any] | None:
        try:
            return self.client.send_message(chat_id, text)
        except Exception as exc:  # pragma: no cover - depends on Telegram/network failures
            self.state.mkdir(parents=True, exist_ok=True)
            with (self.state / "telegram-send-failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "chat_id": chat_id,
                            "text_chars": len(text),
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            return None

    def safe_edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        try:
            return self.client.edit_message_text(chat_id, message_id, text[:MAX_TELEGRAM_MESSAGE_CHARS])
        except Exception as exc:  # pragma: no cover - depends on Telegram/network failures
            with (self.state / "telegram-send-failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "edit_text_chars": len(text),
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            return False

    def safe_send_chat_action(self, chat_id: str, action: str = "typing") -> bool:
        try:
            self.client.send_chat_action(chat_id, action)
            return True
        except Exception as exc:  # pragma: no cover - depends on Telegram/network failures
            with (self.state / "telegram-send-failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "chat_id": chat_id,
                            "chat_action": action,
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            return False

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
            if not self.ai_providers_configured():
                return {"status": "rejected", "error": "ai_provider_not_configured"}
            return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": text}, "requires_confirmation": False}
        return parsed

    def response_text(self, response: dict[str, Any]) -> str:
        return render_response_text(response, platform="telegram")

    def event_text(self, event: dict[str, Any]) -> str | None:
        return render_event_text(event)

    def default_provider(self) -> str:
        configured = self.configured_providers()
        if len(configured) == 1:
            return configured[0]
        path = self.runtime.state / "provider-selection.json"
        if path.exists():
            try:
                provider = json.loads(path.read_text(encoding="utf-8")).get("provider")
                if provider and (not configured or provider in configured):
                    return str(provider)
            except json.JSONDecodeError:
                pass
        return configured[0] if configured else "none"

    def configured_providers(self) -> list[str]:
        raw = os.environ.get("AI_RUNNER_PROVIDERS")
        if raw is None:
            return ["claude-code", "codex"]
        providers: list[str] = []
        for item in raw.split(","):
            provider = item.strip()
            if provider:
                providers.append("claude-code" if provider == "claude" else provider)
        return providers

    def ai_providers_configured(self) -> bool:
        return bool(self.configured_providers())

    def status_interval_seconds(self) -> float:
        raw = os.environ.get("TELEGRAM_STATUS_INTERVAL_SECONDS", "30")
        try:
            value = float(raw)
        except ValueError:
            return 30.0
        return max(0.0, value)

    def heartbeat_text(self, provider: str, started_at: float) -> str:
        elapsed = max(1, int(time.time() - started_at))
        return f"仍在运行，已等待 {elapsed}s。provider={provider}，状态可能是模型思考、工具执行、联网等待或生成中；不是卡死。"

    def status_text(self, task: TelegramTask, message: str | None = None) -> str:
        elapsed = max(0, int(time.time() - task.started_at))
        detail = message or "模型思考、工具执行、联网等待或生成中。"
        return "\n".join(
            [
                "AI 正在运行",
                f"provider: {task.provider}",
                f"task: {task.task_id}",
                f"elapsed: {elapsed}s",
                f"status: {detail}",
            ]
        )

    def start_status_message(self, chat_id: str, provider: str, message_id: int | None) -> TelegramTask:
        task = TelegramTask(task_id=str(uuid.uuid4()), chat_id=chat_id, message_id=message_id, started_at=time.time(), provider=provider)
        text = self.status_text(task, "排队中。")
        result = self.safe_send_message(chat_id, text)
        if result and isinstance(result.get("message_id"), int):
            task.status_message_id = int(result["message_id"])
        task.last_status_text = text
        self.save_task_status(task)
        return task

    def update_status_message(self, task: TelegramTask, message: str | None = None, force: bool = False) -> None:
        text = self.status_text(task, message)
        if not force and text == task.last_status_text:
            return
        task.last_status_text = text
        self.safe_send_chat_action(task.chat_id, "typing")
        if task.status_message_id is not None and self.safe_edit_message_text(task.chat_id, task.status_message_id, text):
            self.save_task_status(task)
            return
        result = self.safe_send_message(task.chat_id, text)
        if result and isinstance(result.get("message_id"), int):
            task.status_message_id = int(result["message_id"])
        self.save_task_status(task)

    def task_event_observer(self, task: TelegramTask) -> Callable[[dict[str, Any]], None]:
        def notify(event: dict[str, Any]) -> None:
            text = self.event_text(event)
            if text:
                self.update_status_message(task, text)

        return notify

    def execute_with_status(
        self,
        task: TelegramTask,
        parsed: dict[str, Any],
        envelope: dict[str, Any],
        runtime: RunnerRuntime,
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

        worker = threading.Thread(target=run, name="telegram-runner-task", daemon=True)
        worker.start()
        interval = self.config.status_interval_seconds
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
                self.update_status_message(task, self.heartbeat_text(task.provider, task.started_at), force=True)
        return holder.get("response") or {
            "request_id": envelope.get("request_id"),
            "status": "failed",
            "error": {"code": "telegram_execute_missing_response", "detail": "missing_response"},
            "data": {},
        }

    def task_runtime(self, task: TelegramTask) -> RunnerRuntime:
        provider = self.default_provider()
        if provider == "none":
            raise RuntimeError("ai_provider_not_configured")
        task.provider = provider
        self.update_status_message(task, "排队中。", force=True)
        return self.runtime.with_event_observer(self.task_event_observer(task))

    def execute_text(self, chat_id: str, message: dict[str, Any], text: str, task: TelegramTask | None = None) -> dict[str, Any]:
        request_id = f"telegram:{chat_id}:{message.get('message_id') or uuid.uuid4()}"
        parsed = self.parsed_for_text(text)
        if parsed.get("status") == "rejected" and parsed.get("error") == "ai_provider_not_configured":
            return {
                "request_id": request_id,
                "status": "error",
                "run_id": None,
                "message_zh": "执行失败",
                "data": {"configured_providers": self.configured_providers()},
                "error": {
                    "code": "ai_provider_not_configured",
                    "detail": "这台机器没有配置 Claude Code 或 Codex；它只能响应 /ai 状态、/ai 帮助、/ai 功能 等管理命令。请把 AI 对话发给安装了对应 AI 的 Telegram bot。",
                },
            }
        if parsed.get("canonical_action") == "confirm":
            token = " ".join(parsed.get("args", {}).get("tail", []))
            pending = self.confirmations()
            item = pending.pop(token, None)
            self.save_confirmations(pending)
            if not item:
                return {"request_id": request_id, "status": "rejected", "error": {"code": "confirmation_not_found", "detail": "confirmation_not_found"}, "data": {}}
            item["envelope"]["confirmed"] = True
            if item["parsed"].get("canonical_action") == "task.run":
                if task is None:
                    task = self.start_status_message(chat_id, self.default_provider(), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)
                runtime = self.task_runtime(task)
                return self.execute_with_status(task, item["parsed"], item["envelope"], runtime)
            return execute(item["parsed"], item["envelope"], self.runtime)

        envelope = {
            "request_id": request_id,
            "platform": "telegram",
            "chat_id": chat_id,
            "sender_id": str(message.get("from", {}).get("id", "")),
            "sender_name": message.get("from", {}).get("username") or message.get("from", {}).get("first_name", ""),
            "raw_text": text,
            "reserved_usd": float(os.environ.get("TELEGRAM_RESERVED_USD", str(self.config.reserved_usd))),
        }
        runtime = self.runtime
        if parsed.get("canonical_action") == "task.run":
            if task is None:
                task = self.start_status_message(chat_id, self.default_provider(), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)
            runtime = self.task_runtime(task)
            response = self.execute_with_status(task, parsed, envelope, runtime)
        else:
            response = execute(parsed, envelope, runtime)
        if response.get("status") == "needs_confirmation":
            token = response.get("data", {}).get("confirmation_token")
            if token:
                pending = self.confirmations()
                pending[token] = {"created_at": int(time.time()), "parsed": parsed, "envelope": envelope}
                self.save_confirmations(pending)
        return response

    def run_task_background(self, chat_id: str, message: dict[str, Any], text: str) -> TelegramTask:
        task = self.start_status_message(chat_id, self.default_provider(), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)

        def run() -> None:
            try:
                response = self.execute_text(chat_id, message, text, task)
            except Exception as exc:  # pragma: no cover - daemon safety net.
                response = {
                    "request_id": f"telegram:{chat_id}:{message.get('message_id') or task.task_id}",
                    "status": "error",
                    "run_id": task.task_id,
                    "message_zh": "执行失败",
                    "data": {},
                    "error": {"code": "telegram_background_task_failed", "detail": str(exc)},
                }
            task.response = response
            task.done = True
            final_status = "已完成，正在发送最终回复。" if response.get("status") in {"accepted", "completed", "needs_confirmation"} else "执行失败，正在发送错误信息。"
            self.update_status_message(task, final_status, force=True)
            self.safe_send_message(chat_id, self.response_text(response))
            self.save_task_status(task)

        with self._tasks_lock:
            self._tasks[task.task_id] = task
        thread = threading.Thread(target=run, name=f"telegram-task-{task.task_id}", daemon=True)
        thread.start()
        return task

    def handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id_value = chat.get("id")
        text = message.get("text")
        if chat_id_value is None or not isinstance(text, str):
            return False
        chat_id = str(chat_id_value)
        if not self.chat_allowed(chat_id):
            self.safe_send_message(chat_id, self.pairing_hint(chat_id))
            return True
        normalized = self.normalize_text(text)
        parsed = self.parsed_for_text(normalized)
        if parsed.get("canonical_action") == "task.run":
            self.run_task_background(chat_id, message, normalized)
            return True
        response = self.execute_text(chat_id, message, normalized)
        self.safe_send_message(chat_id, self.response_text(response))
        return True

    def startup_check(self) -> None:
        me = self.client.get_me()
        if not me.get("is_bot"):
            raise RuntimeError("telegram_token_is_not_bot")
        if self.config.clear_webhook_on_startup:
            self.client.delete_webhook(drop_pending_updates=False)

    def poll_forever(self) -> None:
        self.startup_check()
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
    root = Path(os.environ.get("AI_REMOTE_STATE", str(state_root())))
    load_config_env(root)
    config = TelegramConfig.from_env()
    runtime = RunnerRuntime(root, Path(os.environ.get("AI_WORKSPACE_ROOT", str(workspace_root()))), os.environ.get("MATTERMOST_WEBHOOK_URL"))
    TelegramBot(config, TelegramClient(config), runtime, root).poll_forever()
