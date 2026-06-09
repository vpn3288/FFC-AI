from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from .commands import parse_command
from .executor import RunnerRuntime, execute, parse_reserved_usd
from .paths import state_root, workspace_root
from .phone_render import render_event_text, render_response_text
from .providers import configured_provider_names_from_env, normalize_provider_name
from .storage import atomic_write_json


MAX_TELEGRAM_MESSAGE_CHARS = 3900
DEFAULT_TELEGRAM_RESERVED_USD = 0.0


def _new_draft_id() -> int:
    return uuid.uuid4().int % 2_147_483_647 or 1


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
    status_min_update_seconds: float = 0.0
    task_ttl_seconds: int = 21600
    clear_webhook_on_startup: bool = True
    sync_commands_on_startup: bool = True
    allowed_updates: tuple[str, ...] = ("message", "edited_message", "callback_query")
    native_draft_progress: bool = False
    native_draft_allow_chat_ids: frozenset[str] = frozenset()
    group_mode: str = "mention"

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        raw_ids = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        allowed = {item.strip() for item in raw_ids.split(",") if item.strip()}
        raw_allowed_updates = os.environ.get("TELEGRAM_ALLOWED_UPDATES", "message,edited_message,callback_query")
        allowed_updates = tuple(item.strip() for item in raw_allowed_updates.split(",") if item.strip())
        raw_draft_allow = os.environ.get("TELEGRAM_NATIVE_DRAFT_ALLOW_CHAT_IDS", "")
        return cls(
            token=token,
            allowed_chat_ids=allowed,
            api_base=os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
            poll_timeout_seconds=int(os.environ.get("TELEGRAM_POLL_TIMEOUT_SECONDS", "30")),
            reserved_usd=parse_reserved_usd(os.environ.get("TELEGRAM_RESERVED_USD", str(DEFAULT_TELEGRAM_RESERVED_USD))),
            status_interval_seconds=max(0.0, float(os.environ.get("TELEGRAM_STATUS_INTERVAL_SECONDS", "5"))),
            status_min_update_seconds=max(0.0, float(os.environ.get("TELEGRAM_STATUS_MIN_UPDATE_SECONDS", "0.8"))),
            task_ttl_seconds=max(60, int(os.environ.get("TELEGRAM_TASK_TTL_SECONDS", "21600"))),
            clear_webhook_on_startup=os.environ.get("TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP", "1").lower() not in {"0", "false", "no"},
            sync_commands_on_startup=os.environ.get("TELEGRAM_SYNC_COMMANDS_ON_STARTUP", "1").lower() not in {"0", "false", "no"},
            allowed_updates=allowed_updates or ("message", "edited_message", "callback_query"),
            native_draft_progress=os.environ.get("TELEGRAM_NATIVE_DRAFT_PROGRESS", "0").lower() in {"1", "true", "yes"},
            native_draft_allow_chat_ids=frozenset(item.strip() for item in raw_draft_allow.split(",") if item.strip()),
            group_mode=os.environ.get("TELEGRAM_GROUP_MODE", "mention").strip().lower() or "mention",
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
        payload: dict[str, Any] = {"timeout": timeout_seconds, "allowed_updates": list(self.config.allowed_updates)}
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

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        return bool(self.call("setMyCommands", {"commands": commands}, timeout=15).get("result"))

    def send_message(self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
        last_result: dict[str, Any] = {}
        chunks = [text[i : i + MAX_TELEGRAM_MESSAGE_CHARS] for i in range(0, len(text), MAX_TELEGRAM_MESSAGE_CHARS)] or [""]
        for index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if reply_markup and index == 0:
                payload["reply_markup"] = reply_markup
            result = self.call(
                "sendMessage",
                payload,
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

    def send_message_draft(self, chat_id: str, draft_id: int, text: str) -> bool:
        payload = {"chat_id": int(chat_id), "draft_id": draft_id, "text": text[:4096]}
        return bool(self.call("sendMessageDraft", payload, timeout=10).get("result"))

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        return bool(self.call("answerCallbackQuery", payload, timeout=10).get("result"))


@dataclass
class TelegramTask:
    task_id: str
    chat_id: str
    message_id: int | None
    started_at: float
    provider: str
    draft_id: int = 1
    status_message_id: int | None = None
    last_status_text: str = ""
    last_status_update_at: float = 0.0
    done: bool = False
    response: dict[str, Any] | None = None
    last_status_detail: str = ""
    last_event_phase: str = ""
    last_event_at: float = 0.0
    last_run_id: str = ""


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
        self._task_threads: dict[str, threading.Thread] = {}
        self._tasks_lock = threading.Lock()
        self.bot_username = ""
        self.bot_user_id = ""
        self.state.mkdir(parents=True, exist_ok=True)

    def record_transport_failure(self, kind: str, exc: BaseException) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "telegram-poll-failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "kind": kind,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc)[:500],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )

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
                "task_id": task.task_id,
                "chat_id": task.chat_id,
                "message_id": task.message_id,
                "status_message_id": task.status_message_id,
                "started_at": int(task.started_at),
                "provider": task.provider,
                "done": task.done,
                "last_status_text": task.last_status_text,
                "last_status_detail": task.last_status_detail,
                "last_event_phase": task.last_event_phase,
                "last_event_at": int(task.last_event_at) if task.last_event_at else 0,
                "last_run_id": task.last_run_id,
                "response_status": (task.response or {}).get("status"),
            }
            cutoff = int(time.time()) - self.config.task_ttl_seconds
            data = {key: value for key, value in data.items() if int(value.get("started_at", 0)) >= cutoff or not value.get("done")}
            try:
                atomic_write_json(self.tasks_path, data)
            except FileNotFoundError:
                # Tests and local teardown can remove the transient state dir while a daemon
                # Telegram task is finishing; do not let that crash the background runner.
                return

    def load_auto_continue(self) -> dict[str, Any]:
        path = self.state / "telegram-auto-continue.json"
        if not path.exists():
            return {"chats": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"chats": {}}
        if not isinstance(data, dict):
            return {"chats": {}}
        if not isinstance(data.get("chats"), dict):
            data["chats"] = {}
        return data

    def save_auto_continue(self, data: dict[str, Any]) -> None:
        data.setdefault("chats", {})
        atomic_write_json(self.state / "telegram-auto-continue.json", data)

    def chat_has_running_task(self, chat_id: str) -> bool:
        now = time.time()
        with self._tasks_lock:
            if any(task.chat_id == chat_id and not task.done for task in self._tasks.values()):
                return True
        if not self.tasks_path.exists():
            return False
        try:
            data = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(data, dict):
            return False
        for item in data.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("chat_id") or "") != chat_id:
                continue
            if item.get("done"):
                continue
            try:
                started_at = float(item.get("started_at") or 0)
            except (TypeError, ValueError):
                started_at = 0.0
            if started_at <= 0 or now - started_at <= self.config.task_ttl_seconds:
                return True
        return False

    def run_due_auto_continue(self, now: float | None = None) -> list[TelegramTask]:
        current_time = int(now if now is not None else time.time())
        data = self.load_auto_continue()
        chats = data.setdefault("chats", {})
        started: list[TelegramTask] = []
        changed = False
        for chat_id, raw_schedule in list(chats.items()):
            if not isinstance(raw_schedule, dict):
                chats.pop(chat_id, None)
                changed = True
                continue
            schedule = raw_schedule
            if not schedule.get("enabled"):
                continue
            try:
                interval_seconds = int(schedule.get("interval_seconds") or 0)
                next_due_at = int(schedule.get("next_due_at") or 0)
            except (TypeError, ValueError):
                schedule["enabled"] = False
                schedule["last_error"] = "invalid_auto_continue_schedule"
                changed = True
                continue
            if interval_seconds <= 0:
                schedule["enabled"] = False
                schedule["last_error"] = "invalid_auto_continue_interval"
                changed = True
                continue
            if current_time < next_due_at:
                continue
            if not self.chat_allowed(str(chat_id)):
                schedule["last_skipped_at"] = current_time
                schedule["last_skip_reason"] = "chat_not_allowed"
                schedule["next_due_at"] = current_time + interval_seconds
                changed = True
                continue
            if self.chat_has_running_task(str(chat_id)):
                schedule["last_skipped_at"] = current_time
                schedule["last_skip_reason"] = "chat_has_running_task"
                schedule["next_due_at"] = current_time + interval_seconds
                changed = True
                continue
            prompt = str(schedule.get("prompt") or "继续").strip() or "继续"
            message = {
                "message_id": f"auto-continue-{current_time}",
                "chat": {"id": str(chat_id)},
                "from": {"id": "auto-continue", "username": "auto-continue"},
                "text": prompt,
                "auto_continue": True,
            }
            task = self.run_task_background(str(chat_id), message, prompt)
            started.append(task)
            schedule["last_triggered_at"] = current_time
            schedule["last_task_id"] = task.task_id
            schedule["next_due_at"] = current_time + interval_seconds
            changed = True
        if changed:
            self.save_auto_continue(data)
        return started

    def chat_allowed(self, chat_id: str) -> bool:
        return chat_id in self.config.allowed_chat_ids

    def safe_send_message(self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            return self.client.send_message(chat_id, text, reply_markup=reply_markup)
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

    def safe_send_message_draft(self, chat_id: str, draft_id: int, text: str) -> bool:
        if not self.config.native_draft_progress:
            return False
        if self.config.native_draft_allow_chat_ids and "*" not in self.config.native_draft_allow_chat_ids and chat_id not in self.config.native_draft_allow_chat_ids:
            return False
        if draft_id <= 0:
            return False
        try:
            return self.client.send_message_draft(chat_id, draft_id, text)
        except Exception as exc:  # pragma: no cover - depends on Telegram/network/API version failures
            with (self.state / "telegram-send-failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "chat_id": chat_id,
                            "draft_text_chars": len(text),
                            "error": str(exc)[:500],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            return False

    def safe_answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        try:
            return self.client.answer_callback_query(callback_query_id, text)
        except Exception as exc:  # pragma: no cover - depends on Telegram/network failures
            with (self.state / "telegram-send-failures.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "callback_query_id": callback_query_id,
                            "answer_text": text[:200],
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

    def telegram_menu_commands(self) -> list[dict[str, str]]:
        return [
            {"command": "ai", "description": "运行 AI 或管理 runner"},
            {"command": "status", "description": "查看 runner 和 Codex 状态"},
            {"command": "help", "description": "显示 /ai 命令帮助"},
            {"command": "features", "description": "显示可用功能和 provider"},
            {"command": "codex", "description": "切换到 Codex 或直接让 Codex 执行任务"},
            {"command": "vscode", "description": "切换到 VSCode 或直接让 VSCode 执行任务"},
            {"command": "claude", "description": "切换到 Claude Code 或直接让 Claude Code 执行任务"},
            {"command": "gptmodel", "description": "切换 GPT 模型"},
            {"command": "claudemodel", "description": "切换 Claude 模型"},
            {"command": "shell", "description": "执行本机 shell 命令"},
        ]

    def confirmation_reply_markup(self, response: dict[str, Any]) -> dict[str, Any] | None:
        if response.get("status") != "needs_confirmation":
            return None
        token = str(response.get("data", {}).get("confirmation_token") or "")
        if not token:
            return None
        return {
            "inline_keyboard": [
                [
                    {"text": "确认执行", "callback_data": f"confirm:{token}"},
                    {"text": "取消", "callback_data": f"cancel:{token}"},
                ]
            ]
        }

    def safe_send_response(self, chat_id: str, response: dict[str, Any]) -> dict[str, Any] | None:
        return self.safe_send_message(chat_id, self.response_text(response), reply_markup=self.confirmation_reply_markup(response))

    def normalize_text(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("/ai@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/ai@"):
                return "/ai " + tail.strip()
        if stripped.startswith("/codex@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/codex@"):
                tail = tail.strip()
                return "/codex " + tail if tail else "/ai 提供商 使用 codex"
        if stripped.startswith("/vscode@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/vscode@"):
                tail = tail.strip()
                return "/vscode " + tail if tail else "/ai 提供商 使用 vscode"
        if stripped.startswith("/claude@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/claude@"):
                tail = tail.strip()
                return "/claude " + tail if tail else "/ai 提供商 使用 claude-code"
        if stripped.startswith("/gptmodel@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/gptmodel@"):
                tail = tail.strip()
                return "/ai GPT模型 设置 " + tail if tail else "/ai 帮助"
        if stripped.startswith("/claudemodel@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/claudemodel@"):
                tail = tail.strip()
                return "/ai Claude模型 设置 " + tail if tail else "/ai 帮助"
        if stripped.startswith("/shell@"):
            head, _, tail = stripped.partition(" ")
            if head.startswith("/shell@"):
                return "/shell " + tail.strip()
        if stripped.startswith("/status@"):
            head, _, _tail = stripped.partition(" ")
            if head.startswith("/status@"):
                return "/ai 状态"
        if stripped.startswith("/features@"):
            head, _, _tail = stripped.partition(" ")
            if head.startswith("/features@"):
                return "/ai 功能"
        if stripped.startswith("/help@"):
            head, _, _tail = stripped.partition(" ")
            if head.startswith("/help@"):
                return "/ai 帮助"
        if stripped.startswith("/start@"):
            head, _, _tail = stripped.partition(" ")
            if head.startswith("/start@"):
                return "/ai 帮助"
        if stripped == "/start":
            return "/ai 帮助"
        if stripped == "/help":
            return "/ai 帮助"
        if stripped == "/status":
            return "/ai 状态"
        if stripped == "/features":
            return "/ai 功能"
        if stripped == "/codex":
            return "/ai 提供商 使用 codex"
        if stripped.startswith("/codex "):
            return stripped
        if stripped == "/vscode":
            return "/ai 提供商 使用 vscode"
        if stripped.startswith("/vscode "):
            return stripped
        if stripped == "/claude":
            return "/ai 提供商 使用 claude-code"
        if stripped.startswith("/claude "):
            return stripped
        if stripped == "/gptmodel":
            return "/ai 帮助"
        if stripped.startswith("/gptmodel "):
            return "/ai GPT模型 设置 " + stripped.removeprefix("/gptmodel ").strip()
        if stripped == "/claudemodel":
            return "/ai 帮助"
        if stripped.startswith("/claudemodel "):
            return "/ai Claude模型 设置 " + stripped.removeprefix("/claudemodel ").strip()
        if stripped.startswith("/shell "):
            return stripped
        return stripped

    def parsed_for_text(self, text: str) -> dict[str, Any]:
        if text.startswith("/codex "):
            prompt = text.removeprefix("/codex ").strip()
            if prompt:
                return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": prompt, "provider": "codex"}, "requires_confirmation": False}
        if text.startswith("/vscode "):
            prompt = text.removeprefix("/vscode ").strip()
            if prompt:
                return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": prompt, "provider": "vscode"}, "requires_confirmation": False}
        if text.startswith("/claude "):
            prompt = text.removeprefix("/claude ").strip()
            if prompt:
                return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": prompt, "provider": "claude-code"}, "requires_confirmation": False}
        if text.startswith("/shell "):
            command = text.removeprefix("/shell ").strip()
            if command:
                return {"status": "accepted", "canonical_action": "local.exec", "args": {"tail": [command]}, "requires_confirmation": False}
        parsed = parse_command(text, allow_bare=True)
        if parsed.get("status") == "rejected" and parsed.get("error") == "command_must_start_with_/ai" and not text.startswith("/"):
            if not self.ai_providers_configured():
                return {"status": "rejected", "error": "ai_provider_not_configured"}
            return {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": text}, "requires_confirmation": False}
        if parsed.get("canonical_action") == "task.run" and not self.ai_providers_configured():
            return {"status": "rejected", "error": "ai_provider_not_configured"}
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
                provider = normalize_provider_name(str(json.loads(path.read_text(encoding="utf-8")).get("provider") or ""))
                if provider and (not configured or provider in configured):
                    return provider
            except json.JSONDecodeError:
                pass
        return configured[0] if configured else "none"

    def provider_for_parsed(self, parsed: dict[str, Any]) -> str:
        action = parsed.get("canonical_action")
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        requested = args.get("provider") if isinstance(args, dict) else None
        if isinstance(requested, str) and requested:
            return normalize_provider_name(requested)
        if action == "task.run":
            return self.default_provider()
        if action == "codex.doctor":
            return "codex"
        return "runner"

    def action_runs_in_background(self, parsed: dict[str, Any]) -> bool:
        return parsed.get("canonical_action") in {"task.run", "local.exec", "codex.doctor"}

    def confirmation_runs_background(self, parsed: dict[str, Any]) -> bool:
        token = self.confirmation_token(parsed)
        if not token:
            return False
        item = self.confirmations().get(token)
        if not isinstance(item, dict):
            return False
        saved_parsed = item.get("parsed")
        return isinstance(saved_parsed, dict) and self.action_runs_in_background(saved_parsed)

    def configured_providers(self) -> list[str]:
        return configured_provider_names_from_env(default_all=True) or []

    def ai_providers_configured(self) -> bool:
        return bool(self.configured_providers())

    def heartbeat_text(self, task: TelegramTask) -> str:
        elapsed = max(1, int(time.time() - task.started_at))
        current = task.last_status_detail or "模型思考、工具执行、联网等待或生成中。"
        return f"{current}\nheartbeat: 仍在运行，已等待 {elapsed}s；provider={task.provider}，不是卡死。"

    def status_text(self, task: TelegramTask, message: str | None = None) -> str:
        elapsed = max(0, int(time.time() - task.started_at))
        detail = message or task.last_status_detail or "模型思考、工具执行、联网等待或生成中。"
        if task.last_event_phase:
            detail = f"{detail}\nphase: {task.last_event_phase}"
        lines = [
            "AI 正在运行",
            f"provider: {task.provider}",
            f"task: {task.task_id}",
        ]
        if task.last_run_id:
            lines.append(f"run: {task.last_run_id}")
        lines.extend(
            [
                f"elapsed: {elapsed}s",
                f"status: {detail}",
            ]
        )
        return "\n".join(lines)

    def start_status_message(self, chat_id: str, provider: str, message_id: int | None) -> TelegramTask:
        task = TelegramTask(task_id=str(uuid.uuid4()), chat_id=chat_id, message_id=message_id, started_at=time.time(), provider=provider, draft_id=_new_draft_id())
        text = self.status_text(task, "排队中。")
        result = self.safe_send_message(chat_id, text)
        if result and isinstance(result.get("message_id"), int):
            task.status_message_id = int(result["message_id"])
        task.last_status_text = text
        task.last_status_detail = "排队中。"
        task.last_status_update_at = 0.0
        self.save_task_status(task)
        return task

    def update_status_message(self, task: TelegramTask, message: str | None = None, force: bool = False, heartbeat: bool = False) -> None:
        if message and not heartbeat:
            task.last_status_detail = message
        text = self.status_text(task, message)
        if not force and text == task.last_status_text:
            return
        now = time.time()
        if not force and self.config.status_min_update_seconds > 0 and task.last_status_update_at > 0:
            if now - task.last_status_update_at < self.config.status_min_update_seconds:
                return
        task.last_status_text = text
        task.last_status_update_at = now
        self.safe_send_chat_action(task.chat_id, "typing")
        self.safe_send_message_draft(task.chat_id, task.draft_id, text)
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
            if event.get("run_id"):
                task.last_run_id = str(event["run_id"])
            if event.get("phase"):
                task.last_event_phase = str(event["phase"])
                task.last_event_at = time.time()
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
                self.update_status_message(task, self.heartbeat_text(task), force=True, heartbeat=True)
        return holder.get("response") or {
            "request_id": envelope.get("request_id"),
            "status": "failed",
            "error": {"code": "telegram_execute_missing_response", "detail": "missing_response"},
            "data": {},
        }

    def task_runtime(self, task: TelegramTask, parsed: dict[str, Any]) -> RunnerRuntime:
        provider = self.provider_for_parsed(parsed)
        if provider == "none":
            raise RuntimeError("ai_provider_not_configured")
        task.provider = provider
        self.update_status_message(task, "排队中。", force=True)
        return self.runtime.with_event_observer(self.task_event_observer(task))

    def _is_group_chat(self, message: dict[str, Any]) -> bool:
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        return str(chat.get("type") or "").lower() in {"group", "supergroup"}

    def _message_replies_to_bot(self, message: dict[str, Any]) -> bool:
        reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
        sender = reply.get("from") if isinstance(reply.get("from"), dict) else {}
        if self.bot_user_id and str(sender.get("id") or "") == self.bot_user_id:
            return True
        return bool(sender.get("is_bot")) and bool(self.bot_username) and str(sender.get("username") or "").lower() == self.bot_username.lower()

    def _strip_bot_mention(self, text: str) -> tuple[str, bool]:
        username = self.bot_username.strip().lstrip("@")
        if not username:
            return text, False
        mention = f"@{username}".lower()
        lower = text.lower()
        if mention not in lower:
            return text, False
        stripped = " ".join(part for part in text.split() if part.lower() != mention)
        return stripped.strip(), True

    def group_text_for_message(self, message: dict[str, Any], text: str) -> str | None:
        if not self._is_group_chat(message):
            return text
        mode = self.config.group_mode
        if mode in {"all", "any"}:
            return text
        if text.startswith(("/ai", "/codex", "/vscode", "/claude", "/gptmodel", "/claudemodel", "/shell", "/status", "/features", "/help", "/start")):
            return text
        stripped, mentioned = self._strip_bot_mention(text)
        if mode in {"mention", "mentions"} and (mentioned or self._message_replies_to_bot(message)):
            return stripped or "/ai 状态"
        if mode in {"reply", "replies"} and self._message_replies_to_bot(message):
            return stripped
        return None

    def confirmation_token(self, parsed: dict[str, Any]) -> str:
        if parsed.get("canonical_action") != "confirm":
            return ""
        return " ".join(parsed.get("args", {}).get("tail", [])).strip()

    def confirmation_runs_ai_task(self, parsed: dict[str, Any]) -> bool:
        token = self.confirmation_token(parsed)
        if not token:
            return False
        pending = self.confirmations()
        item = pending.get(token)
        if not isinstance(item, dict):
            return False
        saved_parsed = item.get("parsed")
        return isinstance(saved_parsed, dict) and saved_parsed.get("canonical_action") == "task.run"

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
                    "detail": "这台机器没有配置 Claude Code、VSCode 或 Codex；它只能响应 /ai 状态、/ai 帮助、/ai 功能 等管理命令。请把 AI 对话发给安装了对应 AI 的 Telegram bot。",
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
            if self.action_runs_in_background(item["parsed"]):
                if task is None:
                    task = self.start_status_message(chat_id, self.provider_for_parsed(item["parsed"]), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)
                runtime = self.task_runtime(task, item["parsed"])
                return self.execute_with_status(task, item["parsed"], item["envelope"], runtime)
            return execute(item["parsed"], item["envelope"], self.runtime)

        envelope = {
            "request_id": request_id,
            "platform": "telegram",
            "chat_id": chat_id,
            "sender_id": str(message.get("from", {}).get("id", "")),
            "sender_name": message.get("from", {}).get("username") or message.get("from", {}).get("first_name", ""),
            "raw_text": text,
            "reserved_usd": parse_reserved_usd(os.environ.get("TELEGRAM_RESERVED_USD", str(self.config.reserved_usd))),
        }
        parsed_args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        requested_provider = parsed_args.get("provider") if isinstance(parsed_args, dict) else None
        if isinstance(requested_provider, str) and requested_provider:
            envelope["provider"] = normalize_provider_name(requested_provider)
        runtime = self.runtime
        if self.action_runs_in_background(parsed):
            if task is None:
                task = self.start_status_message(chat_id, self.provider_for_parsed(parsed), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)
            runtime = self.task_runtime(task, parsed)
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

    def run_task_background(self, chat_id: str, message: dict[str, Any], text: str, parsed: dict[str, Any] | None = None) -> TelegramTask:
        parsed_for_provider = parsed or self.parsed_for_text(text)
        task = self.start_status_message(chat_id, self.provider_for_parsed(parsed_for_provider), int(message["message_id"]) if isinstance(message.get("message_id"), int) else None)

        def run() -> None:
            try:
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
                self.save_task_status(task)
                self.safe_send_message_draft(chat_id, task.draft_id, "")
                self.safe_send_response(chat_id, response)
            finally:
                with self._tasks_lock:
                    self._task_threads.pop(task.task_id, None)

        thread = threading.Thread(target=run, name=f"telegram-task-{task.task_id}", daemon=True)
        with self._tasks_lock:
            self._tasks[task.task_id] = task
            self._task_threads[task.task_id] = thread
        thread.start()
        return task

    def drain_background_tasks(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.time() + max(0.0, timeout_seconds)
        current = threading.current_thread()
        while True:
            with self._tasks_lock:
                threads = [thread for thread in self._task_threads.values() if thread is not current and thread.is_alive()]
            if not threads:
                return True
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            for thread in threads:
                thread.join(min(0.05, max(0.0, remaining)))

    def handle_callback_query(self, callback_query: dict[str, Any]) -> bool:
        callback_query_id = str(callback_query.get("id") or "")
        data = str(callback_query.get("data") or "")
        message = callback_query.get("message") if isinstance(callback_query.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id_value = chat.get("id")
        if chat_id_value is None:
            self.safe_answer_callback_query(callback_query_id, "缺少 chat_id")
            return False
        chat_id = str(chat_id_value)
        if not self.chat_allowed(chat_id):
            self.safe_answer_callback_query(callback_query_id, "这个 chat 还没有配对")
            self.safe_send_message(chat_id, self.pairing_hint(chat_id))
            return True
        if data.startswith("cancel:"):
            token = data.removeprefix("cancel:").strip()
            pending = self.confirmations()
            removed = pending.pop(token, None)
            self.save_confirmations(pending)
            self.safe_answer_callback_query(callback_query_id, "已取消" if removed else "确认请求不存在或已过期")
            if isinstance(message.get("message_id"), int):
                self.safe_edit_message_text(chat_id, int(message["message_id"]), "已取消。")
            return True
        if data.startswith("confirm:"):
            token = data.removeprefix("confirm:").strip()
            normalized = f"/ai 确认 {token}"
            parsed = self.parsed_for_text(normalized)
            self.safe_answer_callback_query(callback_query_id, "已确认，开始执行")
            synthetic_message = {
                "message_id": message.get("message_id"),
                "chat": {"id": chat_id},
                "from": callback_query.get("from") or {},
                "text": normalized,
            }
            if self.confirmation_runs_background(parsed):
                self.run_task_background(chat_id, synthetic_message, normalized, parsed)
                return True
            response = self.execute_text(chat_id, synthetic_message, normalized)
            self.safe_send_response(chat_id, response)
            return True
        self.safe_answer_callback_query(callback_query_id, "未知按钮")
        return True

    def handle_update(self, update: dict[str, Any]) -> bool:
        if isinstance(update.get("callback_query"), dict):
            return self.handle_callback_query(update["callback_query"])
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id_value = chat.get("id")
        text = message.get("text")
        if chat_id_value is None or not isinstance(text, str):
            return False
        chat_id = str(chat_id_value)
        if not self.chat_allowed(chat_id):
            self.safe_send_message(chat_id, self.pairing_hint(chat_id))
            return True
        scoped_text = self.group_text_for_message(message, text)
        if scoped_text is None:
            return False
        normalized = self.normalize_text(scoped_text)
        parsed = self.parsed_for_text(normalized)
        if self.action_runs_in_background(parsed) or self.confirmation_runs_background(parsed):
            self.run_task_background(chat_id, message, normalized, parsed)
            return True
        response = self.execute_text(chat_id, message, normalized)
        self.safe_send_response(chat_id, response)
        return True

    def startup_check(self) -> None:
        me = self.client.get_me()
        if not me.get("is_bot"):
            raise RuntimeError("telegram_token_is_not_bot")
        if me.get("username"):
            self.bot_username = str(me["username"]).lstrip("@")
        if me.get("id") is not None:
            self.bot_user_id = str(me["id"])
        if self.config.clear_webhook_on_startup:
            self.client.delete_webhook(drop_pending_updates=False)
        if self.config.sync_commands_on_startup:
            self.client.set_my_commands(self.telegram_menu_commands())

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
                self.run_due_auto_continue()
            except (error.URLError, TimeoutError, RuntimeError, HTTPException, OSError, json.JSONDecodeError) as exc:
                self.record_transport_failure("getUpdates", exc)
                time.sleep(5)


def serve() -> None:
    root = Path(os.environ.get("AI_REMOTE_STATE", str(state_root())))
    load_config_env(root)
    config = TelegramConfig.from_env()
    runtime = RunnerRuntime(root, Path(os.environ.get("AI_WORKSPACE_ROOT", str(workspace_root()))), os.environ.get("MATTERMOST_WEBHOOK_URL"))
    TelegramBot(config, TelegramClient(config), runtime, root).poll_forever()
