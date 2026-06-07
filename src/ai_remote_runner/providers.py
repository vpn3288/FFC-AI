from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetLedger
from .instructions import InstructionStore


PROBE_TIMEOUT_SECONDS = 30
AUTH_PROBE_TIMEOUT_SECONDS = 60
CODEX_EXEC_HELP_COMMAND = ["codex", "exec", "--help"]


def _run_probe(command: list[str], timeout_seconds: int = PROBE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return None


def _version(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {"available": False, "path": None, "version": None}
    result = _run_probe([command, "--version"])
    if result is None:
        return {"available": False, "path": path, "version": None, "error": "probe_timeout"}
    return {"available": result.returncode == 0, "path": path, "version": result.stdout.strip() or result.stderr.strip()}


def _returns_ok(command: list[str]) -> bool:
    if not shutil.which(command[0]):
        return False
    result = _run_probe(command)
    if result is None:
        return False
    return result.returncode == 0


def _help_has(command: list[str], *needles: str) -> bool:
    if not shutil.which(command[0]):
        return False
    result = _run_probe(command)
    if result is None:
        return False
    haystack = result.stdout + result.stderr
    return result.returncode == 0 and all(needle in haystack for needle in needles)


def _codex_exec_help_has(*needles: str) -> bool:
    return _help_has(CODEX_EXEC_HELP_COMMAND, *needles)


def _claude_auth_ready() -> bool:
    if not shutil.which("claude"):
        return False
    result = _run_probe(["claude", "auth", "status", "--json"], timeout_seconds=AUTH_PROBE_TIMEOUT_SECONDS)
    if result is None:
        return False
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return bool(data.get("loggedIn") or data.get("apiProvider"))


def discover_claude() -> dict[str, Any]:
    base = _version("claude")
    auth_check = _claude_auth_ready()
    root_safe_full_access = _help_has(["claude", "-p", "--help"], "acceptEdits", "--add-dir", "--tools", "--allowedTools")
    base["provider"] = "claude-code"
    base["capabilities"] = {
        "new_conversation": True,
        "continue_conversation": True,
        "manual_compact": False,
        "auto_compact": False,
        "context_usage": "estimated",
        "status_events": True,
        "file_edits": True,
        "shell_commands": True,
        "full_access_available": root_safe_full_access,
        "root_safe_full_access_available": root_safe_full_access,
        "dangerously_skip_permissions_available": _help_has(["claude", "-p", "--help"], "--dangerously-skip-permissions"),
        "auth_check_available": auth_check,
        "print_json_available": base["available"],
        "bare_flag_available": _help_has(["claude", "-p", "--help"], "--bare"),
        "append_system_prompt_available": _help_has(["claude", "-p", "--help"], "--append-system-prompt"),
    }
    return base


def discover_vscode() -> dict[str, Any]:
    base = _version("code")
    code_available = bool(base.get("available"))
    backend = discover_claude()
    base["provider"] = "vscode"
    base["backend_provider"] = "claude-code"
    base["available"] = bool(code_available and backend.get("available"))
    base["capabilities"] = {
        "new_conversation": True,
        "continue_conversation": True,
        "manual_compact": False,
        "auto_compact": False,
        "context_usage": "estimated",
        "status_events": True,
        "file_edits": True,
        "shell_commands": True,
        "full_access_available": bool(backend.get("capabilities", {}).get("full_access_available")),
        "root_safe_full_access_available": bool(backend.get("capabilities", {}).get("root_safe_full_access_available")),
        "vscode_command_available": code_available,
        "claude_backend_available": bool(backend.get("available")),
        "backend": backend,
    }
    return base


def discover_codex() -> dict[str, Any]:
    base = _version("codex")
    exec_available = _returns_ok(CODEX_EXEC_HELP_COMMAND)
    approval_config_available = _returns_ok(["codex", "exec", "-c", 'approval_policy="never"', "--help"])
    sandbox_available = _codex_exec_help_has("--sandbox")
    bypass_available = _codex_exec_help_has("--dangerously-bypass-approvals-and-sandbox")
    output_last_message_available = _codex_exec_help_has("--output-last-message")
    json_available = _codex_exec_help_has("--json")
    cd_available = _codex_exec_help_has("--cd")
    add_dir_available = _codex_exec_help_has("--add-dir")
    skip_git_repo_check_available = _codex_exec_help_has("--skip-git-repo-check")
    full_access_available = bypass_available or sandbox_available
    full_access_mode = "bypass" if bypass_available else "sandbox" if sandbox_available else "unavailable"
    full_access_flags = []
    if bypass_available:
        full_access_flags.append("--dangerously-bypass-approvals-and-sandbox")
    elif sandbox_available:
        full_access_flags.extend(["--sandbox", "danger-full-access"])
    if add_dir_available:
        full_access_flags.extend(["--add-dir", "/"])
    if skip_git_repo_check_available:
        full_access_flags.append("--skip-git-repo-check")
    base["provider"] = "codex"
    base["available"] = bool(base["available"] and exec_available and json_available and output_last_message_available and cd_available and full_access_available)
    base["capabilities"] = {
        "new_conversation": True,
        "continue_conversation": _returns_ok(["codex", "exec", "resume", "--help"]),
        "manual_compact": False,
        "auto_compact": False,
        "context_usage": "estimated",
        "status_events": True,
        "file_edits": True,
        "shell_commands": True,
        "exec_available": exec_available,
        "approval_config_available": approval_config_available,
        "sandbox_available": sandbox_available,
        "bypass_approvals_and_sandbox_available": bypass_available,
        "full_access_available": full_access_available,
        "full_access_mode": full_access_mode,
        "full_access_flags": full_access_flags,
        "output_last_message_available": output_last_message_available,
        "json_available": json_available,
        "jsonl_status_events": json_available,
        "telegram_live_status_available": json_available and output_last_message_available,
        "cd_available": cd_available,
        "add_dir_available": add_dir_available,
        "skip_git_repo_check_available": skip_git_repo_check_available,
        "ephemeral_available": _codex_exec_help_has("--ephemeral"),
        "dangerously_bypass_hook_trust_available": _codex_exec_help_has("--dangerously-bypass-hook-trust"),
        "ignore_rules_available": _codex_exec_help_has("--ignore-rules"),
    }
    return base


def normalize_provider_name(value: str) -> str:
    provider = value.strip().lower()
    aliases = {
        "claude": "claude-code",
        "claudecode": "claude-code",
        "code": "vscode",
        "vs-code": "vscode",
    }
    return aliases.get(provider, provider)


def _unconfigured_provider(name: str) -> dict[str, Any]:
    return {
        "provider": name,
        "available": False,
        "configured": False,
        "path": None,
        "version": None,
        "status": "not_configured_on_this_machine",
        "remediation_zh": "这台机器按单机单 AI/工具模式安装，没有启用该 provider。",
        "capabilities": {
            "new_conversation": False,
            "continue_conversation": False,
            "manual_compact": False,
            "auto_compact": False,
            "context_usage": "none",
            "status_events": False,
            "file_edits": False,
            "shell_commands": False,
            "full_access_available": False,
        },
    }


def provider_status() -> list[dict[str, Any]]:
    configured_raw = os.environ.get("AI_RUNNER_PROVIDERS")
    if configured_raw is None:
        configured = None
    else:
        configured = {normalize_provider_name(item) for item in configured_raw.split(",") if item.strip()}
    if configured is None:
        return [discover_claude(), discover_vscode(), discover_codex()]

    providers: list[dict[str, Any]] = []
    for name, discover in (("claude-code", discover_claude), ("vscode", discover_vscode), ("codex", discover_codex)):
        if name in configured:
            provider = discover()
            provider["configured"] = True
        else:
            provider = _unconfigured_provider(name)
        providers.append(provider)
    return providers


CLAUDE_CHAT_ONLY_TEMPLATE = [
    "claude",
    "-p",
    "--bare",
    "--output-format",
    "json",
    "--permission-mode",
    "plan",
    "--tools",
    "",
    "--no-session-persistence",
]


CLAUDE_EDIT_APPROVED_TEMPLATE = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--permission-mode",
    "bypassPermissions",
    "--tools",
    "Read,Grep,Glob,Edit,Write",
    "--no-session-persistence",
]


CLAUDE_SHELL_APPROVED_TEMPLATE = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--permission-mode",
    "bypassPermissions",
    "--tools",
    "Read,Grep,Glob,Edit,Write,Bash",
    "--no-session-persistence",
]


CLAUDE_FULL_ACCESS_TEMPLATE = [
    "claude",
    "-p",
    "--output-format",
    "json",
    "--add-dir",
    "/",
    "--permission-mode",
    "acceptEdits",
    "--tools",
    "Bash,Read,Write,Edit,Grep,Glob",
    "--allowedTools",
    "Bash(*)",
]


CODEX_EXEC_TEMPLATE = [
    "codex",
    "exec",
    "-c",
    'approval_policy="never"',
    "-c",
    "sandbox_workspace_write.network_access=true",
    "-c",
    "shell_environment_policy.inherit=all",
    "--json",
]


CLAUDE_DEFAULT_MODEL = ""
CLAUDE_MODEL_FALLBACKS = [
    "gpt-5.5",
    "claude-opus-4-6-thinking",
    "claude-opus-4-6",
    "claude-opus-4-7-thinking",
    "claude-opus-4-7",
    "claude-opus-4-8-thinking",
    "claude-opus-4-8",
]


@dataclass
class ProviderResult:
    run_id: str
    provider: str
    status: str
    output_text: str
    raw: dict[str, Any] | None
    returncode: int


def _actual_cost_usd(raw: dict[str, Any] | None) -> float | None:
    if not raw:
        return None
    candidates: list[Any] = [
        raw.get("total_cost_usd"),
        raw.get("cost_usd"),
        raw.get("total_cost"),
    ]
    usage = raw.get("usage")
    if isinstance(usage, dict):
        candidates.extend([usage.get("total_cost_usd"), usage.get("cost_usd"), usage.get("total_cost")])
    for value in candidates:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def build_instruction_prompt(store: InstructionStore, workspace_id: str) -> str:
    global_doc = store.show("global")["preview"]
    project_doc = store.show("project", workspace_id)["preview"]
    return f"# Global Instructions\n{global_doc}\n\n# Project Instructions\n{project_doc}\n"


def _claude_chat_retry_prompt(prompt: str) -> str:
    return (
        "你正在作为 Telegram/Mattermost 聊天机器人回复用户。\n"
        "上一轮 Claude Code 返回了空字符串，这对聊天用户不可见。\n"
        "现在必须直接输出一条非空中文回复；不要解释系统状态，不要输出 JSON，不要留空。\n"
        "如果用户消息只是问候、数字、ping、test、测试或其他短消息，请严格只输出：收到，我在。\n"
        "如果用户提出了明确问题，请直接回答该问题；如果无法判断，也请严格只输出：收到，我在。\n"
        "你的下一条响应第一个字符必须是中文可见字符。\n\n"
        "# 用户原始消息\n"
        f"{prompt}\n\n"
        "# 输出要求\n"
        "只输出最终给用户看的文本。"
    )


def _current_user_text(prompt: str) -> str:
    marker = "# 当前用户消息\n"
    if marker in prompt:
        current = prompt.rsplit(marker, 1)[1]
        if "\n\n# 回复要求" in current:
            current = current.split("\n\n# 回复要求", 1)[0]
        return current.strip()
    return prompt.strip()


def _short_chat_fallback(prompt: str) -> str | None:
    text = _current_user_text(prompt)
    normalized = text.strip().lower()
    simple_messages = {
        "你好",
        "您好",
        "hello",
        "hi",
        "hey",
        "ping",
        "test",
        "测试",
        "在吗",
        "在不在",
    }
    if normalized in simple_messages or normalized.isdigit():
        return "收到，我在。"
    if len(text) <= 12 and any(item in text for item in ("你好", "您好", "测试", "在吗")):
        return "收到，我在。"
    return None


def _run_claude_command(command: list[str], workspace: Path, prompt: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    return subprocess.run(command, cwd=workspace, env=env, input=prompt, text=True, capture_output=True, timeout=timeout_seconds, check=False)


def _format_budget_usd(budget_usd: float) -> str:
    return f"{max(0.0, budget_usd):.6f}".rstrip("0").rstrip(".") or "0"


def _claude_budget_limited(reserved_usd: float) -> bool:
    return reserved_usd > 0


def _claude_command_with_budget(command: list[str], budget_usd: float) -> list[str]:
    updated = list(command)
    if "--max-budget-usd" in updated:
        index = updated.index("--max-budget-usd")
        if _claude_budget_limited(budget_usd):
            updated[index + 1] = _format_budget_usd(budget_usd)
        else:
            del updated[index : index + 2]
        return updated
    if not _claude_budget_limited(budget_usd):
        return updated
    insert_at = updated.index("--append-system-prompt") if "--append-system-prompt" in updated else len(updated)
    updated[insert_at:insert_at] = ["--max-budget-usd", _format_budget_usd(budget_usd)]
    return updated


CLAUDE_UNLIMITED_MAX_TURN_VALUES = {"", "0", "none", "false", "off", "unlimited", "infinite", "inf", "无限", "不限"}
CLAUDE_TRANSIENT_API_ERROR_MARKERS = (
    "empty or malformed response",
    "malformed response",
    "service temporarily unavailable",
    "no available accounts",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "rate limit",
    "overloaded",
    "temporarily unavailable",
    "gateway",
    "upstream",
)
CLAUDE_PERMANENT_API_ERROR_MARKERS = (
    "invalid api key",
    "unauthorized",
    "forbidden",
    "permission denied",
    "authentication",
    "http 401",
    "http 403",
)


def _claude_max_turn_args(raw: object | None = None) -> list[str]:
    value = os.environ.get("CLAUDE_MAX_TURNS", "0") if raw is None else str(raw)
    normalized = str(value).strip()
    if normalized.lower() in CLAUDE_UNLIMITED_MAX_TURN_VALUES:
        return []
    try:
        parsed = int(normalized)
    except ValueError:
        return []
    if parsed <= 0:
        return []
    return ["--max-turns", str(parsed)]


def _claude_recorded_cost(first_cost_usd: float | None, retry_cost_usd: float | None, retry_started: bool, reserved_usd: float) -> float | None:
    if retry_started and (retry_cost_usd is None or retry_cost_usd <= 0):
        return first_cost_usd if reserved_usd <= 0 else reserved_usd
    if first_cost_usd is not None and retry_cost_usd is not None:
        return first_cost_usd + retry_cost_usd
    if retry_cost_usd is not None:
        return retry_cost_usd
    if first_cost_usd is not None:
        return first_cost_usd
    return None


def _claude_recorded_attempt_cost(costs: list[float | None], retry_started: bool, reserved_usd: float) -> float | None:
    known = [float(cost) for cost in costs if cost is not None]
    if retry_started and (not costs or costs[-1] is None or float(costs[-1]) <= 0):
        if reserved_usd > 0:
            return reserved_usd
        return sum(known) if known else None
    return sum(known) if known else None


def _claude_adapter_env(provider_id: str, key: str, default: str = "") -> str:
    if provider_id == "vscode":
        value = os.environ.get(f"VSCODE_{key}")
        return value if value is not None else default
    value = os.environ.get(key)
    if value is not None:
        return value
    return default


def _claude_positive_int_value(raw: str, default: int, max_value: int = 5) -> int:
    raw = raw.strip()
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(0, min(parsed, max_value))


def _claude_positive_int_env(name: str, default: int, max_value: int = 5) -> int:
    return _claude_positive_int_value(os.environ.get(name, str(default)), default, max_value)


def _claude_nonnegative_float_value(raw: str, default: float, max_value: float = 120.0) -> float:
    raw = raw.strip()
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return max(0.0, min(parsed, max_value))


def _claude_nonnegative_float_env(name: str, default: float, max_value: float = 120.0) -> float:
    return _claude_nonnegative_float_value(os.environ.get(name, str(default)), default, max_value)


def _claude_output(result: subprocess.CompletedProcess[str]) -> tuple[dict[str, Any] | None, str]:
    raw: dict[str, Any] | None = None
    output_text = result.stdout
    try:
        raw = json.loads(result.stdout)
        raw_output = raw.get("result")
        if raw_output is None:
            raw_output = raw.get("message")
        output_text = str(raw_output or "")
    except json.JSONDecodeError:
        pass
    if result.returncode != 0 and not output_text:
        output_text = result.stderr or result.stdout
    return raw, output_text


def _claude_error_haystack(result: subprocess.CompletedProcess[str], output_text: str) -> str:
    return "\n".join(part for part in (output_text, result.stderr, result.stdout) if part).lower()


def _claude_is_transient_api_error(result: subprocess.CompletedProcess[str], output_text: str) -> bool:
    if result.returncode == 0:
        return False
    haystack = _claude_error_haystack(result, output_text)
    if not haystack:
        return False
    if any(marker in haystack for marker in CLAUDE_PERMANENT_API_ERROR_MARKERS):
        return False
    if "api error" not in haystack and "http " not in haystack:
        return False
    return any(marker in haystack for marker in CLAUDE_TRANSIENT_API_ERROR_MARKERS)


def _claude_retry_delay_seconds(attempt_index: int, provider_id: str = "claude-code") -> float:
    base = _claude_nonnegative_float_value(_claude_adapter_env(provider_id, "CLAUDE_API_RETRY_SLEEP_SECONDS", "12"), 12.0)
    return min(base * max(1, attempt_index), 120.0)


def _codex_jsonl_last_agent_message(output: str) -> str:
    last_message = ""
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                last_message = text
    return last_message


STATUS_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(token|api[_-]?key|password|secret)=([^\s'\";&|]+)"),
)


def _redact_status_text(text: str) -> str:
    redacted = text
    for pattern in STATUS_SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
        else:
            redacted = pattern.sub("<redacted>", redacted)
    return redacted


def _preview_text(text: str, max_chars: int = 180) -> str:
    compact = " ".join(text.split())
    compact = _redact_status_text(compact)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 6].rstrip() + " ..."


def _stringish(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_stringish(item) for item in value if item is not None)
    return ""


def _nested_dict(item: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = item
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_item_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    candidates: list[Any] = [item.get(key) for key in keys]
    for nested in (_nested_dict(item, "arguments"), _nested_dict(item, "args"), _nested_dict(item, "call"), _nested_dict(item, "params")):
        candidates.extend(nested.get(key) for key in keys)
    for candidate in candidates:
        text = _stringish(candidate).strip()
        if text:
            return text
    return ""


def _codex_command_text(item: dict[str, Any]) -> str:
    command = _first_item_text(item, ("command", "cmd", "shell_command"))
    if command:
        return command
    argv = item.get("argv") or _nested_dict(item, "arguments").get("argv")
    if isinstance(argv, list):
        return " ".join(_stringish(part) for part in argv if part is not None).strip()
    return ""


def _codex_file_target(item: dict[str, Any]) -> str:
    direct = _first_item_text(item, ("path", "file", "filename", "target", "uri"))
    if direct:
        return direct
    files = item.get("files") or item.get("paths") or item.get("changes")
    if isinstance(files, list):
        values = []
        for value in files:
            if isinstance(value, dict):
                values.append(_first_item_text(value, ("path", "file", "filename", "target")))
            else:
                values.append(_stringish(value))
        compact = ", ".join(item for item in values if item)
        if compact:
            return compact
    return ""


def _codex_tool_name(item: dict[str, Any]) -> str:
    return _first_item_text(item, ("name", "tool", "tool_name", "function", "server_name"))


def _codex_item_label(item: dict[str, Any], event_type: str = "") -> str:
    item_type = str(item.get("type") or "item")
    item_type_normalized = item_type.lower()
    if item_type_normalized in {"command_execution", "exec", "exec_command", "shell_command", "local_shell_call", "terminal_command"}:
        command_text = _preview_text(_codex_command_text(item), 180)
        if event_type == "item.completed":
            exit_code = item.get("exit_code", item.get("returncode"))
            if exit_code is not None:
                return f"命令已完成：exit={exit_code} {command_text}".strip()
            return f"命令已完成：{command_text}".strip() if command_text else "命令已完成。"
        return f"运行命令：{command_text}" if command_text else "正在运行命令。"
    if item_type_normalized in {"file_change", "file_edit", "file_write", "patch", "apply_patch"}:
        path = _preview_text(_codex_file_target(item), 160)
        if event_type == "item.completed":
            return f"文件修改已完成：{path}" if path else "文件修改已完成。"
        return f"正在修改文件：{path}" if path else "正在修改文件。"
    if item_type_normalized in {"reasoning", "thinking"}:
        return "正在推理和规划。"
    if item_type_normalized in {"mcp_tool_call", "tool_call", "function_call", "custom_tool_call"}:
        name = _preview_text(_codex_tool_name(item), 120)
        if event_type == "item.completed":
            return f"MCP 工具调用已完成：{name}" if name else "MCP 工具调用已完成。"
        return f"正在调用 MCP 工具：{name}" if name else "正在调用 MCP 工具。"
    if item_type_normalized in {"web_search", "web_search_call", "search"}:
        if event_type == "item.completed":
            return "联网检索已完成。"
        return "正在联网检索。"
    if item_type_normalized == "plan_update":
        return "正在更新执行计划。"
    if item_type_normalized in {"agent_message", "assistant_message", "message"}:
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return f"正在整理最终回复：{_preview_text(text)}"
        return "正在生成回复。"
    return f"正在处理 {item_type}。"


def _codex_event_phase(item: dict[str, Any], event_type: str = "") -> str:
    item_type = str(item.get("type") or "").lower()
    if item_type in {"command_execution", "exec", "exec_command", "shell_command", "local_shell_call", "terminal_command"}:
        return "running_command"
    if item_type in {"file_change", "file_edit", "file_write", "patch", "apply_patch"}:
        return "writing_files"
    if item_type in {"reasoning", "thinking"}:
        return "thinking"
    if item_type in {"web_search", "web_search_call", "search", "mcp_tool_call", "tool_call", "function_call", "custom_tool_call"}:
        return "calling_model"
    return "running"


def _emit_codex_jsonl_event(event: dict[str, Any], run_id: str, emit: Callable[[dict[str, Any]], None], seen: set[str]) -> None:
    event_type = str(event.get("type") or "")
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        message = "Codex 会话已创建。"
        if thread_id:
            message = f"{message} thread={thread_id}"
        emit({"run_id": run_id, "provider": "codex", "phase": "queued", "public_message_zh": message})
        return
    if event_type == "turn.started":
        emit({"run_id": run_id, "provider": "codex", "phase": "thinking", "public_message_zh": "Codex 已开始处理任务。"})
        return
    if event_type == "turn.completed":
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        message = "Codex 本轮已完成。"
        if usage:
            message = (
                "Codex 本轮已完成。"
                f"input={usage.get('input_tokens', '?')} "
                f"output={usage.get('output_tokens', '?')} "
                f"reasoning={usage.get('reasoning_output_tokens', '?')}"
            )
        emit({"run_id": run_id, "provider": "codex", "phase": "running", "public_message_zh": message})
        return
    if event_type == "turn.failed":
        emit({"run_id": run_id, "provider": "codex", "phase": "error", "error": str(event.get("error") or "turn_failed")})
        return
    if event_type == "error":
        emit({"run_id": run_id, "provider": "codex", "phase": "error", "error": str(event.get("message") or event.get("error") or "codex_error")})
        return
    item = event.get("item")
    if not isinstance(item, dict):
        return
    if event_type not in {"item.started", "item.completed", "item.updated"}:
        return
    fingerprint = f"{event_type}:{item.get('id')}:{item.get('status')}:{item.get('type')}:{item.get('exit_code')}:{item.get('text')}"
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    label = _codex_item_label(item, event_type)
    emit({"run_id": run_id, "provider": "codex", "phase": _codex_event_phase(item, event_type), "public_message_zh": label})


def _emit_codex_jsonl_events(
    output: str,
    run_id: str,
    emit: Callable[[dict[str, Any]], None] | None,
    seen: set[str] | None = None,
) -> None:
    if not emit:
        return
    emitted = seen if seen is not None else set()
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        _emit_codex_jsonl_event(event, run_id, emit, emitted)


def _codex_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("TERM") or env.get("TERM") == "dumb":
        env["TERM"] = "xterm-256color"
    return env


def _run_codex_command(
    command: list[str],
    workspace: Path,
    prompt: str,
    timeout_seconds: int,
    run_id: str,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _codex_subprocess_env()
    if emit is None:
        return subprocess.run(command, cwd=workspace, env=env, input=prompt, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    seen_events: set[str] = set()

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            _emit_codex_jsonl_events(line, run_id, emit, seen_events)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=read_stdout, name=f"codex-stdout-{run_id}", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name=f"codex-stderr-{run_id}", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass
        process.wait(timeout=timeout_seconds)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        return subprocess.CompletedProcess(command, int(process.returncode or 0), "".join(stdout_lines), "".join(stderr_lines))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise subprocess.TimeoutExpired(command, timeout_seconds, output="".join(stdout_lines), stderr="".join(stderr_lines))
    except BaseException:
        if process.poll() is None:
            process.kill()
        raise
    finally:
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def _invoke_claude_backend(
    provider_id: str,
    public_name_zh: str,
    prompt: str,
    workspace: Path,
    instruction_prompt: str,
    ledger: BudgetLedger,
    run_id: str | None = None,
    reserved_usd: float = 1.0,
    timeout_seconds: int = 1800,
    max_output_bytes: int = 200000,
    emit: Callable[[dict[str, Any]], None] | None = None,
    permission_scope: str = "full",
) -> ProviderResult:
    actual_run_id = run_id or str(uuid.uuid4())
    ledger.reserve(actual_run_id, provider_id, reserved_usd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    template = {
        "chat": CLAUDE_CHAT_ONLY_TEMPLATE,
        "edit": CLAUDE_EDIT_APPROVED_TEMPLATE,
        "shell": CLAUDE_SHELL_APPROVED_TEMPLATE,
        "full": CLAUDE_FULL_ACCESS_TEMPLATE,
    }.get(permission_scope, CLAUDE_FULL_ACCESS_TEMPLATE)
    command = [
        *template,
        *_claude_max_turn_args(_claude_adapter_env(provider_id, "CLAUDE_MAX_TURNS", "0")),
        "--append-system-prompt",
        instruction_prompt,
    ]
    command = _claude_command_with_budget(command, reserved_usd)
    claude_model = _claude_adapter_env(provider_id, "CLAUDE_MODEL", CLAUDE_DEFAULT_MODEL).strip()
    if claude_model:
        command.extend(["--model", claude_model])
    if emit:
        emit(
            {
                "run_id": actual_run_id,
                "provider": provider_id,
                "phase": "calling_model",
                "public_message_zh": f"正在调用 {public_name_zh}：模型思考、工具执行或联网等待中。",
            }
        )
    try:
        result = _run_claude_command(command, workspace, prompt, timeout_seconds)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": provider_id, "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, provider_id, "timeout", "", None, -1)
    raw, output_text = _claude_output(result)
    attempt_raws: list[dict[str, Any] | None] = [raw]
    attempt_costs: list[float | None] = [_actual_cost_usd(raw)]
    transient_retry_started = False
    transient_retries = _claude_positive_int_value(_claude_adapter_env(provider_id, "CLAUDE_API_RETRY_ATTEMPTS", "3"), 3)
    for retry_index in range(1, transient_retries + 1):
        if not _claude_is_transient_api_error(result, output_text):
            break
        transient_retry_started = True
        if emit:
            emit(
                {
                    "run_id": actual_run_id,
                    "provider": provider_id,
                    "phase": "warning",
                    "public_message_zh": f"{public_name_zh} 网关/API 返回临时异常，正在自动重试 {retry_index}/{transient_retries}：{_preview_text(output_text, 120)}",
                }
            )
        delay_seconds = _claude_retry_delay_seconds(retry_index, provider_id)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        try:
            result = _run_claude_command(command, workspace, prompt, timeout_seconds)
        except subprocess.TimeoutExpired:
            actual_cost_usd = _claude_recorded_attempt_cost(attempt_costs, True, reserved_usd)
            ledger.complete(actual_run_id, actual_cost_usd, status="timeout")
            if emit:
                emit({"run_id": actual_run_id, "provider": provider_id, "phase": "error", "error": "timeout_after_api_retry"})
            raw_for_result = {"attempts": attempt_raws} if len(attempt_raws) > 1 else raw
            return ProviderResult(actual_run_id, provider_id, "timeout", "", raw_for_result, -1)
        raw, output_text = _claude_output(result)
        attempt_raws.append(raw)
        attempt_costs.append(_actual_cost_usd(raw))
    if len(attempt_raws) > 1:
        raw = {"attempts": attempt_raws, "final_attempt": raw}
    first_cost_usd = attempt_costs[0]
    retry_cost_usd: float | None = None
    retry_started = False
    if result.returncode == 0 and permission_scope in {"chat", "full"} and not output_text.strip():
        fallback = _short_chat_fallback(prompt)
        if fallback:
            output_text = fallback
            if emit:
                emit({"run_id": actual_run_id, "provider": provider_id, "phase": "warning", "public_message_zh": "模型返回空内容，已使用短消息安全回复。"})
        budget_limited = _claude_budget_limited(reserved_usd)
        remaining_budget_usd = reserved_usd - first_cost_usd if first_cost_usd is not None else 0.0
        if not fallback and (not budget_limited or remaining_budget_usd > 0):
            if emit:
                emit({"run_id": actual_run_id, "provider": provider_id, "phase": "warning", "public_message_zh": "模型返回空内容，正在自动重试一次。"})
            try:
                retry_started = True
                retry_result = _run_claude_command(_claude_command_with_budget(command, remaining_budget_usd), workspace, _claude_chat_retry_prompt(prompt), timeout_seconds)
                retry_raw, retry_output = _claude_output(retry_result)
                retry_cost_usd = _actual_cost_usd(retry_raw)
                attempt_costs.append(retry_cost_usd)
                result = retry_result
                raw = {"first_attempt": raw, "retry_attempt": retry_raw}
                output_text = retry_output
            except subprocess.TimeoutExpired:
                ledger.complete(actual_run_id, _claude_recorded_attempt_cost(attempt_costs + [None], True, reserved_usd), status="timeout")
                if emit:
                    emit({"run_id": actual_run_id, "provider": provider_id, "phase": "error", "error": "timeout_after_empty_retry"})
                return ProviderResult(actual_run_id, provider_id, "timeout", "", raw, -1)
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    if result.returncode == 0 and not output_text.strip():
        output_text = f"{public_name_zh} 返回了空内容；runner 已按失败处理。请重试，或把问题描述得更具体。"
        status = "empty_output"
    else:
        status = "completed" if result.returncode == 0 else "failed"
    actual_cost_usd = _claude_recorded_attempt_cost(attempt_costs, retry_started or transient_retry_started, reserved_usd)
    if actual_cost_usd is None and (retry_started or transient_retry_started):
        actual_cost_usd = _claude_recorded_cost(first_cost_usd, retry_cost_usd, retry_started, reserved_usd)
    if actual_cost_usd is None:
        actual_cost_usd = _actual_cost_usd(raw)
    ledger.complete(actual_run_id, actual_cost_usd, status=status)
    if emit:
        event = {"run_id": actual_run_id, "provider": provider_id, "phase": "done" if status == "completed" else "error"}
        if status != "completed":
            event["error"] = output_text or f"returncode={result.returncode}"
        emit(event)
    return ProviderResult(actual_run_id, provider_id, status, output_text, raw, result.returncode)


def invoke_claude(
    prompt: str,
    workspace: Path,
    instruction_prompt: str,
    ledger: BudgetLedger,
    run_id: str | None = None,
    reserved_usd: float = 1.0,
    timeout_seconds: int = 1800,
    max_output_bytes: int = 200000,
    emit: Callable[[dict[str, Any]], None] | None = None,
    permission_scope: str = "full",
) -> ProviderResult:
    return _invoke_claude_backend(
        "claude-code",
        "Claude Code",
        prompt,
        workspace,
        instruction_prompt,
        ledger,
        run_id=run_id,
        reserved_usd=reserved_usd,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        emit=emit,
        permission_scope=permission_scope,
    )


def invoke_vscode(
    prompt: str,
    workspace: Path,
    instruction_prompt: str,
    ledger: BudgetLedger,
    run_id: str | None = None,
    reserved_usd: float = 1.0,
    timeout_seconds: int = 1800,
    max_output_bytes: int = 200000,
    emit: Callable[[dict[str, Any]], None] | None = None,
    permission_scope: str = "full",
) -> ProviderResult:
    return _invoke_claude_backend(
        "vscode",
        "VSCode Claude 后端",
        prompt,
        workspace,
        instruction_prompt,
        ledger,
        run_id=run_id,
        reserved_usd=reserved_usd,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        emit=emit,
        permission_scope=permission_scope,
    )


def _codex_effective_prompt(prompt: str, instruction_prompt: str = "") -> str:
    return f"{instruction_prompt}\n\n# User Task\n{prompt}" if instruction_prompt else prompt


def codex_command(prompt: str, workspace: Path, output_file: Path, instruction_prompt: str = "") -> list[str]:
    if not _codex_exec_help_has("--json"):
        raise RuntimeError("codex_json_unavailable")
    if not _codex_exec_help_has("--cd"):
        raise RuntimeError("codex_cd_unavailable")
    if not _codex_exec_help_has("--output-last-message"):
        raise RuntimeError("codex_output_last_message_unavailable")
    command = [
        *CODEX_EXEC_TEMPLATE,
        "--cd",
        str(workspace),
        "--output-last-message",
        str(output_file),
        "--",
        "-",
    ]
    if _codex_exec_help_has("--ephemeral"):
        command.insert(command.index("--cd"), "--ephemeral")
    if _codex_exec_help_has("--color"):
        command[command.index("--cd") : command.index("--cd")] = ["--color", "never"]
    if _codex_exec_help_has("--dangerously-bypass-approvals-and-sandbox"):
        command.insert(command.index("--cd"), "--dangerously-bypass-approvals-and-sandbox")
    elif _codex_exec_help_has("--sandbox"):
        command[command.index("--cd") : command.index("--cd")] = ["--sandbox", "danger-full-access"]
    else:
        raise RuntimeError("codex_full_access_unavailable")
    if _codex_exec_help_has("--dangerously-bypass-hook-trust"):
        command.insert(command.index("--cd"), "--dangerously-bypass-hook-trust")
    if _codex_exec_help_has("--ignore-rules"):
        command.insert(command.index("--cd"), "--ignore-rules")
    if _codex_exec_help_has("--add-dir"):
        command[command.index("--cd") : command.index("--cd")] = ["--add-dir", "/"]
    if _codex_exec_help_has("--skip-git-repo-check"):
        command.insert(command.index("--cd"), "--skip-git-repo-check")
    return command


def invoke_codex(
    prompt: str,
    workspace: Path,
    ledger: BudgetLedger,
    instruction_prompt: str = "",
    run_id: str | None = None,
    reserved_usd: float = 1.0,
    timeout_seconds: int = 1800,
    max_output_bytes: int = 200000,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> ProviderResult:
    actual_run_id = run_id or str(uuid.uuid4())
    ledger.reserve(actual_run_id, "codex", reserved_usd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    output_file = workspace / f".ai-remote-codex-{actual_run_id}-last-message.txt"
    output_file.unlink(missing_ok=True)
    try:
        command = codex_command(prompt, workspace, output_file, instruction_prompt)
    except RuntimeError as exc:
        ledger.complete(actual_run_id, None, status="failed")
        if emit:
            emit({"run_id": actual_run_id, "provider": "codex", "phase": "error", "error": str(exc)})
        return ProviderResult(actual_run_id, "codex", "failed", str(exc), None, -1)
    if emit:
        emit(
            {
                "run_id": actual_run_id,
                "provider": "codex",
                "phase": "calling_model",
                "public_message_zh": "正在调用 Codex：模型思考、工具执行或联网等待中。",
            }
        )
    try:
        result = _run_codex_command(command, workspace, _codex_effective_prompt(prompt, instruction_prompt), timeout_seconds, actual_run_id, emit)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": "codex", "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, "codex", "timeout", "", None, -1)
    raw: dict[str, Any] | None = None
    if output_file.exists():
        output_text = output_file.read_text(encoding="utf-8")
    else:
        output_text = _codex_jsonl_last_agent_message(result.stdout) or result.stderr or result.stdout
    if result.stdout.strip():
        raw = {"stdout_jsonl": result.stdout}
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    if result.returncode == 0 and not output_text.strip():
        output_text = "Codex 返回了空内容；runner 已按失败处理。请重试，或把问题描述得更具体。"
        status = "empty_output"
    else:
        status = "completed" if result.returncode == 0 else "failed"
    ledger.complete(actual_run_id, None, status=status)
    if emit:
        event = {"run_id": actual_run_id, "provider": "codex", "phase": "done" if status == "completed" else "error"}
        if status != "completed":
            event["error"] = output_text or f"returncode={result.returncode}"
        emit(event)
    return ProviderResult(actual_run_id, "codex", status, output_text, raw, result.returncode)
