from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetLedger
from .commands import command_index
from .context import ContextState, estimate_tokens
from .credentials import CredentialBroker
from .context_store import ContextStore
from .events import EventSink, status_event
from .instructions import InstructionStore
from .paths import state_root, workspace_root
from .providers import (
    DEFAULT_PROVIDER_NAME,
    CLAUDE_BACKEND_PROVIDER_NAMES,
    SUPPORTED_PROVIDER_NAMES,
    SUPPORTED_PROVIDER_USAGE,
    configured_provider_names_from_env,
    is_supported_provider,
    provider_status,
)
from .providers import build_instruction_prompt, invoke_claude, invoke_codex, invoke_vscode, normalize_provider_name
from .runtime_config import (
    apply_api_key,
    apply_base_url,
    apply_claude_api_retries,
    apply_claude_max_turns,
    apply_claude_model,
    apply_codex_subagent_status_events,
    apply_gpt_model,
    apply_model,
    apply_task_budget,
    config_summary,
    load_config_env,
    list_supported_models,
    model_id_from_args,
    normalize_target,
    split_target_args,
)
from .storage import atomic_write_json
from .task_control import active_processes, process_control_enabled, request_stop, run_registered, terminate_active_processes


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("anthropic_or_openai_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("bridge_secret_assignment", re.compile(r"\bAI_BRIDGE_SHARED_SECRET\s*=\s*(?!<|\\$|\\{)[A-Za-z0-9_-]{32,}\b")),
)
DEFAULT_TASK_RESERVED_USD = 0.0
UNLIMITED_BUDGET_VALUES = {"", "0", "off", "none", "no", "false", "unlimited", "infinite", "inf", "无限", "不限", "关闭"}
DEFAULT_LOCAL_EXEC_TIMEOUT_SECONDS = 300
DEFAULT_LOCAL_EXEC_MAX_OUTPUT_BYTES = 120000
MIN_AUTO_CONTINUE_INTERVAL_SECONDS = 30
MAX_AUTO_CONTINUE_INTERVAL_SECONDS = 86400


@dataclass
class RunnerRuntime:
    state: Path
    workspaces: Path
    webhook_url: str | None = None
    event_observer: Callable[[dict[str, Any]], None] | None = None

    @classmethod
    def default(cls) -> "RunnerRuntime":
        return cls(state_root(), workspace_root())

    @property
    def ledger(self) -> BudgetLedger:
        return BudgetLedger(self.state / "budget" / "ledger.json")

    @property
    def instructions(self) -> InstructionStore:
        return InstructionStore(self.state / "instructions" / "global.md", self.workspaces)

    @property
    def credentials(self) -> CredentialBroker:
        return CredentialBroker(self.state / "credentials")

    @property
    def contexts(self) -> ContextStore:
        return ContextStore(self.state / "contexts")

    @property
    def events(self) -> EventSink:
        return EventSink(self.state / "events.jsonl", self.webhook_url, self.event_observer)

    def with_event_observer(self, observer: Callable[[dict[str, Any]], None]) -> "RunnerRuntime":
        return RunnerRuntime(self.state, self.workspaces, self.webhook_url, observer)

    @property
    def policy_path(self) -> Path:
        return self.state / "conversation-policy.json"

    def load_policy(self) -> dict[str, Any]:
        default = {
            "policy": "continue",
            "conversation_id": "default",
            "provider_conversations": {},
            "auto_compact_enabled": True,
            "auto_compact_threshold_percent": 80,
            "permission_scope": os.environ.get("AI_PERMISSION_SCOPE", "full"),
        }
        if self.policy_path.exists():
            data = default | json.loads(self.policy_path.read_text(encoding="utf-8"))
        else:
            data = default
        if not isinstance(data.get("provider_conversations"), dict):
            data["provider_conversations"] = {}
        return data

    def save_policy(self, policy: dict[str, Any]) -> None:
        atomic_write_json(self.policy_path, policy)


def _ok(request_id: str, run_id: str | None, message_zh: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"request_id": request_id, "status": "accepted", "run_id": run_id, "message_zh": message_zh, "data": data or {}, "error": None}


def _error(request_id: str, code: str, detail: str) -> dict[str, Any]:
    return {"request_id": request_id, "status": "error", "run_id": None, "message_zh": "执行失败", "data": {}, "error": {"code": code, "detail": detail}}


def _json_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_state(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _selected_workspace(rt: RunnerRuntime) -> str | None:
    return _json_state(rt.state / "workspace-selection.json").get("workspace_id")


def _selected_provider(rt: RunnerRuntime) -> str | None:
    provider = _json_state(rt.state / "provider-selection.json").get("provider")
    return normalize_provider_name(str(provider)) if provider else None


def _configured_provider_names() -> list[str] | None:
    return configured_provider_names_from_env()


def _default_provider(rt: RunnerRuntime) -> str | None:
    selected = _selected_provider(rt)
    configured = _configured_provider_names()
    if configured is not None:
        if selected in configured:
            return selected
        if len(configured) == 1:
            return configured[0]
        if not configured:
            return None
    return selected or DEFAULT_PROVIDER_NAME


def _codex_subagent_status_events_enabled(rt: RunnerRuntime) -> bool:
    raw = os.environ.get("CODEX_SUBAGENT_STATUS_EVENTS")
    if raw is None:
        raw = load_config_env(rt.state).get("CODEX_SUBAGENT_STATUS_EVENTS")
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled", "关闭", "关"}


def _provider_from_args(args: list[str]) -> str | None:
    provider = args[0] if args else None
    provider = normalize_provider_name(provider) if provider else None
    if is_supported_provider(provider):
        return provider
    return None


def _provider_from_value(value: object) -> str | None:
    if not value:
        return None
    provider = normalize_provider_name(str(value))
    return provider if is_supported_provider(provider) else None


def _config_target_from_args(rt: RunnerRuntime, args: list[str]) -> tuple[str | None, list[str]]:
    target, rest = split_target_args(args)
    if target:
        return target, rest
    provider = _default_provider(rt)
    if provider:
        return provider, args
    return "vscode", args


def _model_action_info(action: str) -> dict[str, str | None]:
    if action == "model.select_gpt":
        return {
            "family": "gpt",
            "message_zh": "GPT 模型已更新",
            "usage": "usage: /ai GPT模型 设置 [claude-code|codex|vscode] <gpt-model>",
            "wrong_family_detail": "这是 GPT 模型切换命令；Claude 模型请使用 /ai Claude模型 设置 [claude-code|codex|vscode] <claude-model>",
        }
    if action == "model.select_claude":
        return {
            "family": "claude",
            "message_zh": "Claude 模型已更新",
            "usage": "usage: /ai Claude模型 设置 [claude-code|codex|vscode] <claude-model>",
            "wrong_family_detail": "这是 Claude 模型切换命令；GPT 模型请使用 /ai GPT模型 设置 [claude-code|codex|vscode] <gpt-model>",
        }
    return {
        "family": None,
        "message_zh": "模型已更新",
        "usage": "usage: /ai 模型 使用 [claude-code|codex|vscode] <model>",
        "wrong_family_detail": "model family does not match command",
    }


def _claude_control_target_from_args(rt: RunnerRuntime, args: list[str]) -> tuple[str | None, list[str]]:
    target, rest = split_target_args(args)
    if target in CLAUDE_BACKEND_PROVIDER_NAMES:
        return target, rest
    provider = _default_provider(rt)
    if provider in CLAUDE_BACKEND_PROVIDER_NAMES:
        return provider, args
    return DEFAULT_PROVIDER_NAME, args


def _valid_workspace_id(workspace_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", workspace_id))


def _secret_findings(text: str) -> list[str]:
    scrubbed = re.sub(r"\{\{credential://[^}]+\}\}", "{{credential-handle}}", text)
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(scrubbed)]


def _provider_capabilities(provider: str) -> dict[str, Any]:
    status = next((item for item in provider_status() if item.get("provider") == provider), None)
    return status.get("capabilities", {}) if status else {}


def _context_threshold(policy: dict[str, Any]) -> int:
    try:
        value = int(policy.get("auto_compact_threshold_percent", 80))
    except (TypeError, ValueError):
        return 80
    return min(90, max(70, value))


def parse_reserved_usd(raw: object, default: float = DEFAULT_TASK_RESERVED_USD) -> float:
    value = str(raw).strip()
    if value.lower() in UNLIMITED_BUDGET_VALUES:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed)


def _default_reserved_usd() -> float:
    return parse_reserved_usd(os.environ.get("AI_TASK_RESERVED_USD", str(DEFAULT_TASK_RESERVED_USD)))


def _local_exec_timeout_seconds() -> int:
    raw = os.environ.get("AI_LOCAL_EXEC_TIMEOUT_SECONDS", str(DEFAULT_LOCAL_EXEC_TIMEOUT_SECONDS))
    try:
        value = int(float(raw))
    except ValueError:
        return DEFAULT_LOCAL_EXEC_TIMEOUT_SECONDS
    return min(86400, max(1, value))


def _local_exec_max_output_bytes() -> int:
    raw = os.environ.get("AI_LOCAL_EXEC_MAX_OUTPUT_BYTES", str(DEFAULT_LOCAL_EXEC_MAX_OUTPUT_BYTES))
    try:
        value = int(float(raw))
    except ValueError:
        return DEFAULT_LOCAL_EXEC_MAX_OUTPUT_BYTES
    return min(5_000_000, max(1024, value))


def _trim_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip() + "\n...(output truncated)"


def _preview_command(command: str, max_chars: int = 180) -> str:
    compact = " ".join(command.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 6].rstrip() + " ..."


def _local_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("TERM") or env.get("TERM") == "dumb":
        env["TERM"] = "xterm-256color"
    return env


def _format_local_exec_output(command: str, cwd: Path, returncode: int | str, stdout: str, stderr: str, max_bytes: int) -> str:
    sections = [
        f"command: {command}",
        f"cwd: {cwd}",
        f"exit_code: {returncode}",
    ]
    if stdout.strip():
        sections.append("stdout:\n" + stdout.rstrip())
    if stderr.strip():
        sections.append("stderr:\n" + stderr.rstrip())
    return _trim_utf8("\n\n".join(sections), max_bytes)


def _policy_conversation_id(policy: dict[str, Any], provider: str) -> str:
    provider_map = policy.get("provider_conversations")
    if isinstance(provider_map, dict):
        value = provider_map.get(provider)
        if value:
            return str(value)
    conversation_id = str(policy.get("conversation_id") or "default")
    _set_policy_conversation_id(policy, provider, conversation_id)
    return conversation_id


def _set_policy_conversation_id(policy: dict[str, Any], provider: str, conversation_id: str) -> None:
    provider_map = policy.setdefault("provider_conversations", {})
    if not isinstance(provider_map, dict):
        provider_map = {}
        policy["provider_conversations"] = provider_map
    provider_map[provider] = conversation_id
    policy["conversation_id"] = conversation_id


def _prompt_with_history(prompt: str, transcript: str) -> str:
    current = (
        "# 当前用户消息\n"
        f"{prompt}\n\n"
        "# 回复要求\n"
        "请直接回复当前用户消息。即使用户只是发送问候、数字或测试消息，也要给出可见的简短回复；不要返回空内容。"
    )
    if not transcript:
        return current
    return (
        "# 持续对话历史\n"
        "下面是同一个对话窗口中的最近公开问答，请在回答新问题时延续这些上下文和用户偏好。\n\n"
        f"{transcript}\n\n"
        f"{current}"
    )


def _install_manifest(rt: RunnerRuntime) -> dict[str, Any]:
    path = rt.state / "install-manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _recent_run_events(rt: RunnerRuntime, limit: int = 10) -> list[dict[str, Any]]:
    path = rt.state / "events.jsonl"
    if not path.exists():
        return []
    runs: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-500:]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = str(event.get("run_id") or "")
        if not run_id:
            continue
        runs[run_id] = {
            "run_id": run_id,
            "provider": event.get("provider") or "runner",
            "phase": event.get("phase") or "unknown",
            "message_zh": event.get("public_message_zh") or event.get("error") or "",
            "time": event.get("time"),
        }
    return sorted(runs.values(), key=lambda item: int(item.get("time") or 0), reverse=True)[:limit]


def _telegram_tasks(rt: RunnerRuntime, limit: int = 10) -> list[dict[str, Any]]:
    path = rt.state / "telegram-tasks.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    tasks = []
    for task_id, item in data.items():
        if not isinstance(item, dict):
            continue
        task = dict(item)
        task.setdefault("task_id", task_id)
        tasks.append(task)
    tasks.sort(key=lambda item: int(item.get("started_at") or 0), reverse=True)
    return tasks[:limit]


def _auto_continue_path(rt: RunnerRuntime) -> Path:
    return rt.state / "telegram-auto-continue.json"


def _load_auto_continue(rt: RunnerRuntime) -> dict[str, Any]:
    path = _auto_continue_path(rt)
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


def _save_auto_continue(rt: RunnerRuntime, data: dict[str, Any]) -> None:
    data.setdefault("chats", {})
    atomic_write_json(_auto_continue_path(rt), data)


def _chat_id_from_envelope(envelope: dict[str, Any]) -> str:
    return str(envelope.get("chat_id") or "").strip()


def _parse_interval_seconds(raw: str) -> int:
    value = raw.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|sec|secs|second|seconds|秒|m|min|mins|minute|minutes|分钟|h|hour|hours|小时)?", value)
    if not match:
        raise ValueError("invalid_interval")
    amount = float(match.group(1))
    unit = match.group(2) or "s"
    multiplier = 1
    if unit in {"m", "min", "mins", "minute", "minutes", "分钟"}:
        multiplier = 60
    elif unit in {"h", "hour", "hours", "小时"}:
        multiplier = 3600
    seconds = int(amount * multiplier)
    if seconds < MIN_AUTO_CONTINUE_INTERVAL_SECONDS or seconds > MAX_AUTO_CONTINUE_INTERVAL_SECONDS:
        raise ValueError("interval_out_of_range")
    return seconds


def _command_index_with_availability(items: list[dict[str, Any]], providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    any_provider_available = any(item.get("available") for item in providers)
    codex_available = any(item.get("provider") == "codex" and item.get("available") for item in providers)
    updated = []
    for item in items:
        row = dict(item)
        action = row.get("canonical_action", "")
        if action in {"provider.select"} and not any_provider_available:
            row["enabled"] = False
            row["unsupported"] = True
        if action == "provider.select" and "codex" in row.get("usage", "") and not codex_available:
            row["enabled"] = False
        updated.append(row)
    return updated


def current_status(runtime: RunnerRuntime) -> dict[str, Any]:
    manifest = _install_manifest(runtime)
    configured_providers = _configured_provider_names()
    default_provider = _default_provider(runtime)
    return {
        "core_ready": bool(manifest.get("core_ready", False)),
        "core_ready_status": manifest.get("core_ready_status", "unknown"),
        "bridge_loopback_validated": bool(manifest.get("bridge_loopback_validated", False)),
        "mattermost_command_validated": bool(manifest.get("mattermost_command_validated", False)),
        "integration_ready_status": manifest.get("integration_ready_status", "unknown"),
        "providers": provider_status(),
        "configured_providers": configured_providers if configured_providers is not None else list(SUPPORTED_PROVIDER_NAMES),
        "budget": runtime.ledger.load(),
        "policy": runtime.load_policy(),
        "current_workspace": _selected_workspace(runtime) or "default",
        "default_provider": default_provider or "none",
        "default_workspace": _selected_workspace(runtime) or "default",
        "recent_runs": _recent_run_events(runtime),
        "telegram_tasks": _telegram_tasks(runtime),
        "active_processes": active_processes(runtime.state),
        "telegram_auto_continue": _load_auto_continue(runtime),
        "codex_subagent_status_events_enabled": _codex_subagent_status_events_enabled(runtime),
        "state_root": str(runtime.state),
        "workspace_root": str(runtime.workspaces),
    }


def execute(parsed: dict[str, Any], envelope: dict[str, Any], runtime: RunnerRuntime | None = None) -> dict[str, Any]:
    rt = runtime or RunnerRuntime.default()
    request_id = envelope.get("request_id") or str(uuid.uuid4())
    if parsed.get("status") != "accepted":
        return _error(request_id, parsed.get("error", "rejected"), parsed.get("error", "rejected"))
    if parsed.get("requires_confirmation") and not envelope.get("confirmed"):
        return {
            "request_id": request_id,
            "status": "needs_confirmation",
            "run_id": None,
            "message_zh": "此操作需要确认",
            "data": {"canonical_action": parsed.get("canonical_action"), "confirmation_token": str(uuid.uuid4())},
            "error": None,
        }

    action = parsed["canonical_action"]
    args = parsed.get("args", {}).get("tail", [])
    workspace_id = envelope.get("workspace_id") or _selected_workspace(rt) or "default"
    if not _valid_workspace_id(workspace_id):
        return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
    run_id = envelope.get("run_id") or str(uuid.uuid4())

    def emit_queued(provider: str = "runner") -> None:
        rt.events.emit(status_event(run_id, "queued", "正在排队", provider))

    if action == "status":
        emit_queued()
        return _ok(request_id, run_id, "状态已生成", current_status(rt))
    if action in {"command_index", "feature_index"}:
        emit_queued()
        providers = provider_status()
        manifest = _install_manifest(rt)
        feature_data: dict[str, Any] = {"items": _command_index_with_availability(command_index(), providers), "providers": providers}
        codex = next((item for item in providers if item["provider"] == "codex"), None)
        feature_data["codex_ready"] = manifest.get("codex_ready", bool(codex and codex.get("available")))
        feature_data["codex_status"] = manifest.get("codex_status", "install_required")
        feature_data["codex_telegram_realtime_status"] = manifest.get(
            "codex_telegram_realtime_status",
            bool(codex and codex.get("capabilities", {}).get("telegram_live_status_available")),
        )
        feature_data["codex_exec_full_access_mode"] = manifest.get(
            "codex_exec_full_access_mode",
            (codex or {}).get("capabilities", {}).get("full_access_mode", "unavailable") if codex else "unavailable",
        )
        feature_data["codex_remediation_zh"] = "Codex 必须由安装脚本全局安装并启用 full access；若不可用，请重新运行 runner 安装脚本。"
        if manifest.get("codex_remediation_zh"):
            feature_data["codex_remediation_zh"] = manifest["codex_remediation_zh"]
        if codex and not codex.get("available"):
            feature_data["codex_remediation_zh"] = "Codex 当前不可用；请重新运行 runner 安装脚本，确保 Node.js/npm、网络和版本锁可用。"
        return _ok(request_id, run_id, "索引已生成", feature_data)
    if action == "budget_status":
        emit_queued()
        return _ok(request_id, run_id, "预算已生成", rt.ledger.load())
    if action == "context_status":
        policy = rt.load_policy()
        provider = _provider_from_value(envelope.get("provider")) or _default_provider(rt)
        if not provider:
            return _error(request_id, "ai_provider_not_configured", "这台机器没有配置 Claude Code、VSCode 或 Codex；只能使用状态、帮助、功能等管理命令。")
        emit_queued(provider)
        conversation_id = envelope.get("conversation_id") or _policy_conversation_id(policy, provider)
        existing = rt.contexts.load(conversation_id, provider)
        state = ContextState(
            conversation_id,
            provider,
            existing["context_limit_tokens"],
            existing["context_used_tokens"],
            existing.get("measurement", "estimated"),
            _context_threshold(policy),
            int(existing.get("hard_stop_threshold_percent", 95)),
        )
        return _ok(request_id, run_id, "上下文状态已生成", state.__dict__ | {"context_used_percent": state.context_used_percent, "policy": policy})
    if action in {"local.exec", "codex.doctor"}:
        if action == "codex.doctor":
            command = os.environ.get("AI_CODEX_DOCTOR_COMMAND", "codex doctor --summary --ascii").strip()
            provider = "codex"
        else:
            command = " ".join(args).strip()
            provider = "runner"
        if not command:
            return _error(request_id, "missing_command", "usage: /ai shell <command> or /ai 脚本 运行 <command>")
        workspace = rt.workspaces / workspace_id
        workspace.mkdir(parents=True, exist_ok=True)
        timeout_seconds = _local_exec_timeout_seconds()
        max_output_bytes = _local_exec_max_output_bytes()
        rt.events.emit(status_event(run_id, "running_command", f"本机命令执行中：{_preview_command(command)}", provider))
        try:
            if process_control_enabled():
                result = run_registered(
                    rt.state,
                    run_id,
                    provider,
                    command,
                    cwd=workspace,
                    env=_local_subprocess_env(),
                    input=None,
                    shell=True,
                    executable="/bin/bash" if Path("/bin/bash").exists() else None,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                    action="local.exec",
                )
            else:
                result = subprocess.run(
                    command,
                    cwd=workspace,
                    env=_local_subprocess_env(),
                    shell=True,
                    executable="/bin/bash" if Path("/bin/bash").exists() else None,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            output = _format_local_exec_output(command, workspace, "timeout", stdout, stderr, max_output_bytes)
            rt.events.emit(status_event(run_id, "error", f"本机命令超时：{_preview_command(command)}", provider))
            return {
                "request_id": request_id,
                "status": "error",
                "run_id": run_id,
                "message_zh": "命令执行超时",
                "data": {"command": command, "cwd": str(workspace), "timeout_seconds": timeout_seconds, "output": output},
                "error": {"code": "local_exec_timeout", "detail": output},
            }
        output = _format_local_exec_output(command, workspace, result.returncode, result.stdout, result.stderr, max_output_bytes)
        if result.returncode != 0:
            rt.events.emit(status_event(run_id, "error", f"本机命令失败：exit={result.returncode} {_preview_command(command)}", provider))
            return {
                "request_id": request_id,
                "status": "error",
                "run_id": run_id,
                "message_zh": "命令执行失败",
                "data": {"command": command, "cwd": str(workspace), "exit_code": result.returncode, "output": output},
                "error": {"code": f"local_exec_exit_{result.returncode}", "detail": output},
            }
        rt.events.emit(status_event(run_id, "done", f"本机命令完成：{_preview_command(command)}", provider))
        return _ok(
            request_id,
            run_id,
            "命令执行完成",
            {"command": command, "cwd": str(workspace), "exit_code": result.returncode, "output": output},
        )
    if action == "task.run":
        prompt = parsed.get("args", {}).get("prompt") or envelope.get("raw_text", "")
        provider = _provider_from_value(envelope.get("provider")) or _default_provider(rt)
        if not provider:
            return _error(request_id, "ai_provider_not_configured", "这台机器没有配置 Claude Code、VSCode 或 Codex；请把 AI 任务发送到安装了对应 AI 的 Telegram bot。")
        configured = _configured_provider_names()
        if configured is not None and provider not in configured:
            return _error(request_id, "ai_provider_not_configured", f"{provider} 没有配置在这台机器上；当前配置: {','.join(configured) or 'none'}")
        reserved_usd = parse_reserved_usd(envelope.get("reserved_usd", _default_reserved_usd()))
        budget_ok, budget_reason = rt.ledger.can_reserve(reserved_usd)
        if not budget_ok:
            return _error(request_id, budget_reason, f"Budget preflight failed before provider start: {budget_reason}")
        emit_queued(provider)
        instruction_prompt = build_instruction_prompt(rt.instructions, workspace_id)
        secret_findings = _secret_findings(instruction_prompt)
        if secret_findings:
            return _error(request_id, "secrets_in_instructions", f"Secret-like material in instructions: {', '.join(secret_findings)}. Move secrets into credential handles.")
        global_info = rt.instructions.show("global", workspace_id)
        project_info = rt.instructions.show("project", workspace_id)
        policy = rt.load_policy()
        require_shell_confirmation = os.environ.get("AI_REQUIRE_SHELL_CONFIRMATION", "0").lower() in {"1", "true", "yes"}
        permission_scope = str(policy.get("permission_scope", "full"))
        if provider == "codex" and permission_scope != "full":
            return _error(
                request_id,
                "codex_permission_scope_unsupported",
                "Codex 当前只按 full access 运行。请发送 /ai 完全访问 开启 后再使用 Codex，或切换到 Claude Code 使用 scoped 模式。",
            )
        if policy.get("permission_scope") == "shell" and require_shell_confirmation and not envelope.get("confirmed"):
            return {
                "request_id": request_id,
                "status": "needs_confirmation",
                "run_id": run_id,
                "message_zh": "Shell 模式任务需要逐次确认",
                "data": {"canonical_action": "task.run", "permission_scope": "shell", "confirmation_token": str(uuid.uuid4())},
                "error": None,
            }
        if envelope.get("conversation_id"):
            conversation_id = envelope["conversation_id"]
        elif policy.get("policy") == "new_each_request":
            conversation_id = str(uuid.uuid4())
        else:
            conversation_id = _policy_conversation_id(policy, provider)
            rt.save_policy(policy)
        existing = rt.contexts.load(conversation_id, provider)
        if existing.get("summary_artifact") and Path(existing["summary_artifact"]).exists():
            instruction_prompt = f"{instruction_prompt}\n\n# Previous Context Summary\n{Path(existing['summary_artifact']).read_text(encoding='utf-8')}\n"
        threshold = _context_threshold(policy)
        transcript = "" if policy.get("policy") == "new_each_request" else rt.contexts.transcript(conversation_id, provider)
        provider_prompt = _prompt_with_history(prompt, transcript)
        used = existing["context_used_tokens"] + estimate_tokens(instruction_prompt, provider_prompt)
        context_state = ContextState(conversation_id, provider, existing["context_limit_tokens"], used, auto_compact_threshold_percent=threshold)
        if context_state.hard_stop:
            return _error(
                request_id,
                "context_hard_stop",
                f"Context usage {context_state.context_used_percent}% exceeds hard limit {context_state.hard_stop_threshold_percent}%. Run /ai 压缩 or /ai 新对话 to continue.",
            )
        if context_state.needs_warning:
            rt.events.emit(status_event(run_id, "warning", "上下文接近上限，请考虑压缩或新对话", provider))
            auto_compact_enabled = (policy.get("policy") != "new_each_request") and (bool(policy.get("auto_compact_enabled")) or str(envelope.get("auto_compact_enabled", "")).lower() in {"1", "true", "yes"})
            if auto_compact_enabled:
                compacted = rt.contexts.compact(conversation_id, provider)
                conversation_id = compacted["new_conversation_id"]
                _set_policy_conversation_id(policy, provider, conversation_id)
                rt.save_policy(policy)
                instruction_prompt = f"{instruction_prompt}\n\n# Previous Context Summary\n{Path(compacted['summary_artifact']).read_text(encoding='utf-8')}\n"
                transcript = rt.contexts.transcript(conversation_id, provider)
                provider_prompt = _prompt_with_history(prompt, transcript)
        workspace = rt.workspaces / workspace_id
        workspace.mkdir(parents=True, exist_ok=True)
        emit = rt.events.emit
        if provider == "codex":
            result = invoke_codex(provider_prompt, workspace, rt.ledger, instruction_prompt=instruction_prompt, run_id=run_id, reserved_usd=reserved_usd, emit=emit)
        elif provider == "vscode":
            result = invoke_vscode(provider_prompt, workspace, instruction_prompt, rt.ledger, run_id=run_id, reserved_usd=reserved_usd, emit=emit, permission_scope=permission_scope)
        else:
            result = invoke_claude(provider_prompt, workspace, instruction_prompt, rt.ledger, run_id=run_id, reserved_usd=reserved_usd, emit=emit, permission_scope=permission_scope)
        rt.contexts.add_exchange(conversation_id, provider, instruction_prompt, prompt, result.output_text)
        data = {
            "provider": result.provider,
            "status": result.status,
            "output": result.output_text,
            "conversation_id": conversation_id,
            "global_md_sha256": global_info["sha256"],
            "project_md_sha256": project_info["sha256"],
        }
        if result.status != "completed":
            return {
                "request_id": request_id,
                "status": "error",
                "run_id": run_id,
                "message_zh": "执行失败",
                "data": data,
                "error": {"code": f"{result.provider}_{result.status}", "detail": result.output_text or result.status},
            }
        return _ok(
            request_id,
            run_id,
            "任务已完成",
            data,
        )
    if action == "compact_context":
        policy = rt.load_policy()
        provider = _provider_from_value(envelope.get("provider")) or _default_provider(rt)
        if not provider:
            return _error(request_id, "ai_provider_not_configured", "这台机器没有配置 Claude Code、VSCode 或 Codex，无法压缩 AI 对话上下文。")
        capabilities = _provider_capabilities(provider)
        if not (capabilities.get("new_conversation") or capabilities.get("continue_conversation")):
            return _error(request_id, "provider_compaction_unsupported", f"{provider} does not report new or continued conversation support")
        conversation_id = envelope.get("conversation_id") or _policy_conversation_id(policy, provider)
        compacted = rt.contexts.compact(conversation_id, provider)
        _set_policy_conversation_id(policy, provider, compacted["new_conversation_id"])
        rt.save_policy(policy)
        return _ok(request_id, run_id, "上下文已压缩", compacted)
    if action == "provider.list":
        return _ok(request_id, run_id, "提供商列表已生成", {"providers": provider_status()})
    if action == "provider.select":
        provider = _provider_from_args(args)
        if provider is None:
            return _error(request_id, "invalid_provider", f"provider must be {SUPPORTED_PROVIDER_USAGE}")
        configured = _configured_provider_names()
        if configured is not None and provider not in configured:
            return _error(request_id, "ai_provider_not_configured", f"{provider} 没有配置在这台机器上；当前配置: {','.join(configured) or 'none'}")
        _write_json_state(rt.state / "provider-selection.json", {"provider": provider})
        return _ok(request_id, run_id, "提供商已选择", {"provider": provider})
    if action == "model.list":
        target, rest = _config_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", f"target must be {SUPPORTED_PROVIDER_USAGE}")
        if rest:
            normalized = normalize_target(rest[0])
            if normalized:
                target = normalized
        return _ok(request_id, run_id, "模型列表已生成", list_supported_models(target))
    if action in {"model.select", "model.select_gpt", "model.select_claude"}:
        info = _model_action_info(action)
        target, rest = _config_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", f"target must be {SUPPORTED_PROVIDER_USAGE}")
        try:
            model = model_id_from_args(rest)
            if info["family"] == "gpt":
                data = apply_gpt_model(rt.state, target, model)
            elif info["family"] == "claude":
                data = apply_claude_model(rt.state, target, model)
            else:
                data = apply_model(rt.state, target, model)
        except ValueError as exc:
            code = str(exc)
            if code == "missing_model":
                return _error(request_id, "missing_model", str(info["usage"]))
            if code in {"gpt_model_required", "claude_model_required"}:
                return _error(request_id, "wrong_model_family", str(info["wrong_family_detail"]))
            return _error(request_id, "invalid_model", "model must be one model id without spaces; put claude-code/vscode/codex before the model id")
        return _ok(request_id, run_id, str(info["message_zh"]), data)
    if action == "provider_config.set_api_key":
        target, rest = _config_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", f"target must be {SUPPORTED_PROVIDER_USAGE}")
        if not rest:
            return _error(request_id, "missing_api_key", "usage: /ai 密钥 设置 [claude-code|codex|vscode] <api_key>")
        try:
            return _ok(request_id, run_id, "API key 已更新", apply_api_key(rt.state, target, "".join(rest).strip()))
        except ValueError as exc:
            code = str(exc)
            if code == "wrong_api_key_family":
                return _error(request_id, code, "Codex/OpenAI provider cannot use an Anthropic sk-ant-* key; use /ai 密钥 设置 claude-code ... for Claude.")
            return _error(request_id, "invalid_api_key", "API key cannot be empty or contain whitespace.")
    if action == "provider_config.set_base_url":
        target, rest = _config_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", f"target must be {SUPPORTED_PROVIDER_USAGE}")
        if not rest:
            return _error(request_id, "missing_base_url", "usage: /ai 代理 设置 [claude-code|codex|vscode] <base_url>")
        try:
            return _ok(request_id, run_id, "第三方代理地址已更新", apply_base_url(rt.state, target, " ".join(rest).strip()))
        except ValueError:
            return _error(request_id, "invalid_base_url", "base_url must be an http(s) URL without whitespace, for example https://api.example.com/v1")
    if action == "provider_config.show":
        targets = [normalize_target(item) for item in args] if args else list(SUPPORTED_PROVIDER_NAMES)
        valid_targets = [target for target in targets if target]
        if not valid_targets:
            return _error(request_id, "invalid_target", f"target must be {SUPPORTED_PROVIDER_USAGE}")
        return _ok(request_id, run_id, "配置已读取", {"targets": [config_summary(target) for target in valid_targets]})
    if action == "codex.subagent_status.show":
        enabled = _codex_subagent_status_events_enabled(rt)
        return _ok(
            request_id,
            run_id,
            "Codex 子 agent 状态开关已读取",
            {
                "target": "codex",
                "enabled": enabled,
                "config_key": "CODEX_SUBAGENT_STATUS_EVENTS",
                "status_zh": "开启" if enabled else "关闭",
            },
        )
    if action in {"codex.subagent_status.enable", "codex.subagent_status.disable"}:
        enabled = action.endswith(".enable")
        data = apply_codex_subagent_status_events(rt.state, enabled)
        message = "Codex 子 agent 状态展示已开启" if enabled else "Codex 子 agent 状态展示已关闭"
        return _ok(request_id, run_id, message, data)
    if action == "budget.set_task_reserved":
        if not args:
            return _error(request_id, "missing_budget", "usage: /ai 预算 设置 <usd>")
        reserved_usd = args[0].strip()
        if parse_reserved_usd(reserved_usd, -1.0) < 0:
            return _error(request_id, "invalid_budget", "budget must be a number, 0, unlimited, or 无限")
        return _ok(request_id, run_id, "单次任务预算已更新", apply_task_budget(rt.state, reserved_usd))
    if action in {"auto_continue.status", "auto_continue.set", "auto_continue.disable"}:
        chat_id = _chat_id_from_envelope(envelope)
        data = _load_auto_continue(rt)
        chats = data.setdefault("chats", {})
        if action == "auto_continue.status":
            if chat_id:
                return _ok(request_id, run_id, "定时继续状态已读取", {"chat_id": chat_id, "schedule": chats.get(chat_id, {"enabled": False})})
            return _ok(request_id, run_id, "定时继续状态已读取", data)
        if not chat_id:
            return _error(request_id, "telegram_chat_required", "定时继续是Telegram chat级功能，请从已配对Telegram聊天里设置。")
        if action == "auto_continue.disable":
            existing = chats.get(chat_id, {})
            chats[chat_id] = dict(existing) | {"enabled": False, "updated_at": int(time.time())}
            _save_auto_continue(rt, data)
            return _ok(request_id, run_id, "定时继续已关闭", {"chat_id": chat_id, "schedule": chats[chat_id]})
        if not args:
            return _error(request_id, "missing_interval", "usage: /ai 定时继续 设置 <秒数|5m|1h>")
        try:
            interval_seconds = _parse_interval_seconds(args[0])
        except ValueError as exc:
            code = str(exc)
            if code == "interval_out_of_range":
                return _error(request_id, code, "interval must be between 30 seconds and 24 hours")
            return _error(request_id, "invalid_interval", "interval examples: 300, 5m, 1h")
        now = int(time.time())
        chats[chat_id] = {
            "enabled": True,
            "interval_seconds": interval_seconds,
            "prompt": "继续",
            "next_due_at": now + interval_seconds,
            "updated_at": now,
            "chat_id": chat_id,
        }
        _save_auto_continue(rt, data)
        return _ok(request_id, run_id, "定时继续已设置", {"chat_id": chat_id, "schedule": chats[chat_id]})
    if action == "claude.max_turns.set":
        target, rest = _claude_control_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", "target must be claude-code or vscode")
        if not rest:
            return _error(request_id, "missing_max_turns", "usage: /ai 轮数 设置 <正整数|0|无限>")
        max_turns = rest[0].strip()
        try:
            return _ok(request_id, run_id, "Claude 后端最大轮数已更新", apply_claude_max_turns(rt.state, max_turns, target))
        except ValueError:
            return _error(request_id, "invalid_max_turns", "max_turns must be 0/unlimited/无限 or a positive integer")
    if action == "claude.retry.set":
        target, rest = _claude_control_target_from_args(rt, args)
        if target is None:
            return _error(request_id, "invalid_target", "target must be claude-code or vscode")
        if not rest:
            return _error(request_id, "missing_retry_attempts", "usage: /ai 重试 设置 <0-5>")
        attempts = rest[0].strip()
        try:
            return _ok(request_id, run_id, "Claude 后端 API 重试次数已更新", apply_claude_api_retries(rt.state, attempts, target))
        except ValueError:
            return _error(request_id, "invalid_retry_attempts", "retry attempts must be an integer from 0 to 5")
    if action == "credential.list":
        return _ok(request_id, run_id, "凭据列表已生成", {"credentials": rt.credentials.list_public()})
    if action == "credential.add":
        handle = args[0] if args else f"credential://pending/{uuid.uuid4()}"
        return _ok(
            request_id,
            run_id,
            "凭据添加流程已创建",
            {
                "handle": handle,
                "secret_material": "never send secret material in chat",
                "bridge_upload_url_endpoint": "/bridge/credential-upload-url",
                "bridge_upload_url_method": "POST",
                "local_capture_command": f"python3 -m ai_remote_runner.cli credential-add-secret --metadata-json '{{\"handle\":\"{handle}\"}}'",
                "status": "awaiting_local_secret_capture",
            },
        )
    if action == "credential.test":
        if not args:
            return _error(request_id, "missing_credential_handle", "missing_credential_handle")
        return _ok(request_id, run_id, "凭据测试完成", rt.credentials.test(args[0]))
    if action == "credential.grant":
        if len(args) < 3:
            return _error(request_id, "missing_credential_grant_args", "usage: /ai 凭据 授权 <handle> <agent> <action> [duration_seconds]")
        duration_seconds = None
        if len(args) >= 4:
            try:
                duration_seconds = int(args[3])
            except ValueError:
                return _error(request_id, "invalid_duration_seconds", "duration_seconds must be an integer")
        try:
            granted = rt.credentials.grant(args[0], args[1], args[2], duration_seconds)
        except KeyError:
            return _error(request_id, "credential_not_found", args[0])
        return _ok(request_id, run_id, "凭据授权已更新", {"credential": granted})
    if action == "credential.delete":
        if not args:
            return _error(request_id, "missing_credential_handle", "missing_credential_handle")
        return _ok(request_id, run_id, "凭据已删除", rt.credentials.delete(args[0]))
    if action.endswith(".show"):
        scope = "global" if action.startswith("global_") else "project"
        return _ok(request_id, run_id, "指令已读取", rt.instructions.show(scope, workspace_id))
    if action.endswith(".append"):
        scope = "global" if action.startswith("global_") else "project"
        text = " ".join(args)
        return _ok(request_id, run_id, "指令已追加", rt.instructions.write(scope, text, workspace_id, append=True))
    if action.endswith(".rollback"):
        scope = "global" if action.startswith("global_") else "project"
        if not args:
            return _error(request_id, "missing_snapshot", "missing_snapshot")
        try:
            return _ok(request_id, run_id, "指令已回滚", rt.instructions.rollback(scope, args[0], workspace_id))
        except FileNotFoundError:
            return _error(request_id, "snapshot_not_found", args[0])
    if action.endswith(".apply"):
        scope = "global" if action.startswith("global_") else "project"
        shown = rt.instructions.show(scope, workspace_id)
        return _ok(request_id, run_id, "指令已应用", {"scope": scope, "workspace_id": workspace_id, "sha256": shown["sha256"], "provider": _default_provider(rt) or "none"})
    if action.endswith(".set"):
        scope = "global" if action.startswith("global_") else "project"
        text = " ".join(args)
        return _ok(request_id, run_id, "指令已替换", rt.instructions.write(scope, text, workspace_id, append=False))
    if action == "conversation_status":
        data = rt.load_policy()
        data["policy"] = "continue"
        data["last_action"] = action
        provider = _provider_from_value(envelope.get("provider")) or _default_provider(rt)
        if not provider:
            return _error(request_id, "ai_provider_not_configured", "这台机器没有配置 Claude Code、VSCode 或 Codex，无法创建 AI 对话。")
        conversation_id = _policy_conversation_id(data, provider)
        _set_policy_conversation_id(data, provider, conversation_id)
        rt.save_policy(data)
        context = rt.contexts.load(conversation_id, provider)
        return _ok(
            request_id,
            run_id,
            "长期对话已启用",
            {
                "policy": data,
                "conversation_id": conversation_id,
                "provider": provider,
                "context_used_tokens": context.get("context_used_tokens", 0),
                "context_used_percent": context.get("context_used_percent", 0),
                "auto_compact_threshold_percent": _context_threshold(data),
                "recent_exchanges": len(context.get("exchanges", [])),
            },
        )
    if action in {"new_conversation", "continue_conversation", "set_policy_new_each_request", "set_policy_continue"}:
        data = rt.load_policy()
        provider = _provider_from_value(envelope.get("provider")) or _default_provider(rt)
        if not provider:
            return _error(request_id, "ai_provider_not_configured", "这台机器没有配置 Claude Code、VSCode 或 Codex，无法更新 AI 会话策略。")
        if action == "new_conversation":
            _set_policy_conversation_id(data, provider, str(uuid.uuid4()))
            data["policy"] = "continue"
        elif action == "set_policy_new_each_request":
            data["policy"] = "new_each_request"
        elif action in {"set_policy_continue", "continue_conversation"}:
            data["policy"] = "continue"
            _set_policy_conversation_id(data, provider, _policy_conversation_id(data, provider))
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "会话策略已更新", data)
    if action in {"set_auto_compact_enabled", "set_auto_compact_disabled"}:
        data = rt.load_policy()
        data["auto_compact_enabled"] = action == "set_auto_compact_enabled"
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "自动压缩策略已更新", data)
    if action in {"set_permission_chat", "set_permission_edit", "set_permission_shell", "set_permission_full"}:
        data = rt.load_policy()
        data["permission_scope"] = action.removeprefix("set_permission_")
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "执行权限模式已更新", data)
    if action == "cancel":
        data = request_stop(rt.state, force=False) | {"detail": "取消标记已记录；如需终止本runner启动的活动进程，请发送 /ai 强行停止。"}
        _write_json_state(rt.state / "cancel-request.json", data | {"run_id": run_id})
        return _ok(request_id, run_id, data["detail"], data)
    if action == "task.force_stop":
        target_run_id = args[0] if args else None
        request_stop(rt.state, force=True)
        result = terminate_active_processes(rt.state, target_run_id=target_run_id, grace_seconds=1.0)
        rt.events.emit(status_event(run_id, "warning", f"强行停止完成：匹配 {result['matched']} 个活动进程，已终止 {result['terminated']} 个。", "runner"))
        return _ok(request_id, run_id, "强行停止已执行", result)
    if action == "confirm":
        return _error(request_id, "confirmation_not_found", "没有找到可确认的待执行请求，或确认已过期。")
    if action == "workspace.list":
        rt.workspaces.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区列表已生成", {"workspaces": sorted(path.name for path in rt.workspaces.iterdir() if path.is_dir())})
    if action == "workspace.create":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        if not _valid_workspace_id(args[0]):
            return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
        target = rt.workspaces / args[0]
        target.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区已创建", {"workspace_id": args[0], "path": str(target)})
    if action == "workspace.select":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        if not _valid_workspace_id(args[0]):
            return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
        _write_json_state(rt.state / "workspace-selection.json", {"workspace_id": args[0]})
        return _ok(request_id, run_id, "工作区已选择", {"workspace_id": args[0]})
    if action in {"extension.list", "tool.list", "mcp.list"}:
        return _ok(
            request_id,
            run_id,
            "扩展索引已生成",
            {
                "items": [
                    {"id": "filesystem", "kind": "mcp", "installed": False, "description_zh": "文件系统访问扩展。"},
                    {"id": "git", "kind": "mcp", "installed": False, "description_zh": "Git 仓库操作扩展。"},
                    {"id": "opencli", "kind": "tool", "installed": False, "description_zh": "可选命令行工具。"},
                ]
            },
        )
    if action == "description.generate":
        target = args[0] if args else "unknown"
        return _ok(request_id, run_id, "说明已生成", {"id": target, "description_zh": f"{target} 的中文说明待人工确认。", "description_source": "generated_ai_stub"})
    if action == "description.list":
        return _ok(request_id, run_id, "说明索引已生成", {"items": command_index()})
    if action == "description.edit":
        target = args[0] if args else "unknown"
        text = " ".join(args[1:]) if len(args) > 1 else ""
        return _ok(request_id, run_id, "说明编辑请求已记录", {"id": target, "description_zh": text, "description_source": "manual"})
    return _error(request_id, "unsupported_action", action)
