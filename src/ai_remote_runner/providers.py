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
from .model_aliases import normalize_model_name
from .paths import state_root
from .task_control import (
    popen_process_group_kwargs,
    register_process,
    run_registered,
    unregister_process,
)


PROBE_TIMEOUT_SECONDS = 30
AUTH_PROBE_TIMEOUT_SECONDS = 8
CODEX_EXEC_HELP_COMMAND = ["codex", "exec", "--help"]
CODEX_EXEC_RESUME_HELP_COMMAND = ["codex", "exec", "resume", "--help"]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    adapter_type: str
    aliases: tuple[str, ...] = ()
    claude_backend: bool = False


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec("claude-code", "Claude Code", "claude-code", ("claude", "claudecode", "anthropic"), True),
    ProviderSpec("vscode", "VSCode", "vscode", ("code", "vs-code"), True),
    ProviderSpec("codex", "Codex", "codex", ("openai",), False),
)
SUPPORTED_PROVIDER_NAMES = tuple(spec.name for spec in PROVIDER_SPECS)
CLAUDE_BACKEND_PROVIDER_NAMES = tuple(spec.name for spec in PROVIDER_SPECS if spec.claude_backend)
DEFAULT_PROVIDER_NAME = "claude-code"
SUPPORTED_PROVIDER_USAGE = "claude-code, vscode, or codex"
_PROVIDER_ALIASES = {alias: spec.name for spec in PROVIDER_SPECS for alias in spec.aliases}
_PROVIDER_ALIASES.update({spec.name: spec.name for spec in PROVIDER_SPECS})


def normalize_provider_name(value: str) -> str:
    provider = value.strip().lower()
    return _PROVIDER_ALIASES.get(provider, provider)


def is_supported_provider(value: str | None) -> bool:
    return bool(value and normalize_provider_name(value) in SUPPORTED_PROVIDER_NAMES)


def configured_provider_names_from_env(default_all: bool = False) -> list[str] | None:
    raw = os.environ.get("AI_RUNNER_PROVIDERS")
    if raw is None:
        return list(SUPPORTED_PROVIDER_NAMES) if default_all else None
    providers: list[str] = []
    for item in raw.split(","):
        provider = normalize_provider_name(item)
        if provider in SUPPORTED_PROVIDER_NAMES and provider not in providers:
            providers.append(provider)
    return providers


def _run_probe(command: list[str], timeout_seconds: int = PROBE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _version(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {"available": False, "path": None, "version": None}
    result = _run_probe([command, "--version"])
    if result is None:
        return {"available": False, "path": path, "version": None, "error": "probe_timeout"}
    return {"available": result.returncode == 0, "path": path, "version": result.stdout.strip() or result.stderr.strip()}


def _vscode_version() -> dict[str, Any]:
    candidates = [
        os.environ.get("AI_VSCODE_ROOT_WRAPPER", "").strip() or "/usr/local/bin/code-root",
        "code-root",
        "code",
    ]
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for command in candidates:
        if not command or command in seen:
            continue
        seen.add(command)
        result = _version(command)
        result["probe_command"] = command
        if result.get("available"):
            return result
        failures.append(result)
    fallback = failures[-1] if failures else {"available": False, "path": None, "version": None}
    return fallback | {"probe_failures": failures}


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
    if _claude_env_auth_configured():
        return True
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


def _claude_env_auth_configured() -> bool:
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        if os.environ.get(name, "").strip():
            return True
    return False


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
    base = _vscode_version()
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
    resume_available = _returns_ok(CODEX_EXEC_RESUME_HELP_COMMAND)
    resume_json_available = _help_has(CODEX_EXEC_RESUME_HELP_COMMAND, "--json")
    resume_output_last_message_available = _help_has(CODEX_EXEC_RESUME_HELP_COMMAND, "--output-last-message")
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
        "continue_conversation": bool(resume_available and resume_json_available and resume_output_last_message_available),
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
        "resume_available": resume_available,
        "resume_json_available": resume_json_available,
        "resume_output_last_message_available": resume_output_last_message_available,
        "telegram_live_status_available": json_available and output_last_message_available,
        "cd_available": cd_available,
        "add_dir_available": add_dir_available,
        "skip_git_repo_check_available": skip_git_repo_check_available,
        "ephemeral_available": _codex_exec_help_has("--ephemeral"),
        "dangerously_bypass_hook_trust_available": _codex_exec_help_has("--dangerously-bypass-hook-trust"),
        "ignore_rules_available": _codex_exec_help_has("--ignore-rules"),
    }
    return base


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
    configured_names = configured_provider_names_from_env()
    configured = None if configured_names is None else set(configured_names)
    if configured is None:
        return [discover_claude(), discover_vscode(), discover_codex()]

    discoverers: dict[str, Callable[[], dict[str, Any]]] = {
        "claude-code": discover_claude,
        "vscode": discover_vscode,
        "codex": discover_codex,
    }
    providers: list[dict[str, Any]] = []
    for name in SUPPORTED_PROVIDER_NAMES:
        if name in configured:
            provider = discoverers[name]()
            provider["configured"] = True
        else:
            provider = _unconfigured_provider(name)
        providers.append(provider)
    return providers


CLAUDE_CHAT_ONLY_TEMPLATE = [
    "claude",
    "-p",
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
    "acceptEdits",
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
    "acceptEdits",
    "--tools",
    "Read,Grep,Glob,Edit,Write,Bash",
    "--no-session-persistence",
]


CLAUDE_FULL_ACCESS_TEMPLATE = [
    "claude",
    "-p",
    "--bare",
    "--output-format",
    "json",
    "--add-dir",
    "/",
    "--permission-mode",
    "bypassPermissions",
    "--tools",
    "Bash,Read,Write,Edit,Grep,Glob",
    "--allowedTools",
    "Bash(*)",
    "--no-session-persistence",
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


@dataclass(frozen=True)
class CodexJsonlRunSummary:
    pending_tool_call_count: int = 0
    pending_tool_call_labels: tuple[str, ...] = ()
    turn_completed: bool = False
    context_warning: str = ""


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


def _state_from_ledger(ledger: BudgetLedger) -> Path:
    try:
        return ledger.path.parent.parent
    except Exception:
        return state_root()


def _claude_timeout_args(provider_id: str = "claude-code") -> list[str]:
    return []


def _run_claude_command(
    command: list[str],
    workspace: Path,
    prompt: str,
    timeout_seconds: int,
    *,
    state: Path | None = None,
    run_id: str = "",
    provider: str = "claude-code",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()

    # Ensure third-party API settings are explicitly passed to Claude Code
    # This fixes the issue where Claude Code ignores ANTHROPIC_BASE_URL
    if "ANTHROPIC_BASE_URL" in env and "ANTHROPIC_AUTH_TOKEN" in env:
        base_url = env.get("ANTHROPIC_BASE_URL", "").strip()
        auth_token = env.get("ANTHROPIC_AUTH_TOKEN", "").strip()

        # Only apply if both are set and base_url is not the official Anthropic API
        if base_url and auth_token and "api.anthropic.com" not in base_url:
            # Force Claude Code to use third-party API by ensuring these are set
            env["ANTHROPIC_BASE_URL"] = base_url
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token

            # Additional environment variables to ensure Claude Code respects third-party API
            env["ANTHROPIC_API_URL"] = base_url
            env["ANTHROPIC_API_KEY"] = auth_token

            # Disable any OAuth that might interfere
            env["CLAUDE_CODE_DISABLE_OAUTH"] = "1"

    if state is None or not run_id:
        return subprocess.run(command, cwd=workspace, env=env, input=prompt, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    return run_registered(
        state,
        run_id,
        provider,
        command,
        cwd=workspace,
        env=env,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        action="provider",
    )


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
    "invalid model",
    "unauthorized",
    "forbidden",
    "permission denied",
    "authentication",
    "model not found",
    "model_not_found",
    "model is not",
    "unsupported model",
    "unknown model",
    "does not exist",
    "bad request",
    "http 400",
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


def _claude_positive_int_env(name: str, default: int, max_value: int = 10) -> int:
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
    import random

    base = _claude_nonnegative_float_value(_claude_adapter_env(provider_id, "CLAUDE_API_RETRY_SLEEP_SECONDS", "5"), 5.0)
    exponential_delay = base * (2 ** max(0, attempt_index - 1))
    capped_delay = min(exponential_delay, 120.0)
    jitter = capped_delay * 0.2 * (random.random() * 2 - 1)
    return max(1.0, capped_delay + jitter)


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


def _codex_jsonl_thread_id(output: str) -> str:
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id.strip()
    return ""


def _codex_event_item(event: dict[str, Any]) -> dict[str, Any]:
    for key in ("item", "payload"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return {}


CODEX_TOOL_CALL_ITEM_TYPES = {
    "command_execution",
    "custom_tool_call",
    "exec",
    "exec_command",
    "function_call",
    "local_shell_call",
    "mcp_tool_call",
    "shell_command",
    "terminal_command",
    "tool_call",
}

CODEX_TOOL_OUTPUT_ITEM_TYPES = {
    "custom_tool_call_output",
    "function_call_output",
    "local_shell_call_output",
    "mcp_tool_call_output",
    "tool_call_output",
}


def _codex_call_id(item: dict[str, Any]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _number_candidates(value: Any, wanted_keys: set[str]) -> list[float]:
    found: list[float] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in wanted_keys and isinstance(nested, (int, float)):
                found.append(float(nested))
            found.extend(_number_candidates(nested, wanted_keys))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_number_candidates(nested, wanted_keys))
    return found


def _codex_context_warning(event: dict[str, Any], threshold: float = 0.8) -> str:
    event_type = str(event.get("type") or "")
    item = _codex_event_item(event)
    item_type = str(item.get("type") or "")
    if event_type not in {"event_msg", "thread_goal_updated", "token_count"} and item_type != "token_count":
        return ""
    window_candidates = _number_candidates(event, {"model_context_window", "context_window", "contextWindow"})
    if not window_candidates:
        return ""
    window = max(window_candidates)
    if window <= 0:
        return ""
    used_candidates = _number_candidates(
        event,
        {
            "tokensUsed",
            "tokens_used",
            "active_tokens",
            "activeTokens",
            "last_token_usage",
            "input_tokens",
            "inputTokens",
        },
    )
    bounded = [value for value in used_candidates if 0 <= value <= window * 1.2]
    if not bounded:
        return ""
    used = max(bounded)
    ratio = used / window
    if ratio < threshold:
        return ""
    return f"Codex 上下文接近上限：约 {int(used)}/{int(window)} tokens（{ratio:.0%}）。本轮可能提前结束；建议任务拆小，或用 /ai 新对话 后继续。"


def _codex_jsonl_run_summary(output: str) -> CodexJsonlRunSummary:
    pending: dict[str, str] = {}
    turn_completed = False
    context_warning = ""
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type") or "")
        if event_type == "turn.completed":
            turn_completed = True
        warning = _codex_context_warning(event)
        if warning:
            context_warning = warning
        item = _codex_event_item(event)
        item_type = str(item.get("type") or "").lower()
        call_id = _codex_call_id(item)
        if not call_id:
            continue
        label = _preview_text(_codex_command_text(item) or _codex_tool_name(item) or item_type or call_id, 120)
        if event_type == "item.started" and item_type in CODEX_TOOL_CALL_ITEM_TYPES:
            pending[call_id] = label
            continue
        if event_type == "item.completed" and call_id in pending:
            pending.pop(call_id, None)
            continue
        if event_type == "response_item" and item_type in CODEX_TOOL_CALL_ITEM_TYPES:
            pending[call_id] = label
            continue
        if event_type == "response_item" and item_type in CODEX_TOOL_OUTPUT_ITEM_TYPES:
            pending.pop(call_id, None)
            continue
    return CodexJsonlRunSummary(
        pending_tool_call_count=len(pending),
        pending_tool_call_labels=tuple(pending.values()),
        turn_completed=turn_completed,
        context_warning=context_warning,
    )


CODEX_TRANSIENT_STREAM_ERROR_MARKERS = (
    "reconnecting...",
    "stream disconnected before completion",
    "websocket closed by server",
    "responses_websocket",
    "failed to connect to websocket",
    "connection reset",
    "connection closed",
    "connection aborted",
    "response.completed",
    "stream timeout",
    "stream error",
)


CODEX_PERMANENT_STREAM_ERROR_MARKERS = (
    "401 unauthorized",
    "403 forbidden",
    "invalid api key",
    "incorrect api key",
    "missing bearer",
    "unauthorized",
    "forbidden",
    "model not found",
    "unsupported model",
    "unknown model",
)


def _codex_error_message(event: dict[str, Any]) -> str:
    message = event.get("message") or event.get("error") or ""
    if isinstance(message, dict):
        nested = message.get("message") or message.get("detail") or message.get("error") or ""
        return str(nested or message)
    return str(message)


def _codex_error_phase(message: str) -> str:
    lowered = message.lower()
    if any(marker in lowered for marker in CODEX_PERMANENT_STREAM_ERROR_MARKERS):
        return "error"
    if any(marker in lowered for marker in CODEX_TRANSIENT_STREAM_ERROR_MARKERS):
        return "warning"
    return "error"


def _codex_stream_error_kind(text: str) -> str:
    lowered = text.lower()
    if any(marker in lowered for marker in CODEX_PERMANENT_STREAM_ERROR_MARKERS):
        return "permanent"
    if any(marker in lowered for marker in CODEX_TRANSIENT_STREAM_ERROR_MARKERS):
        return "transient"
    return ""


def _codex_stream_error_fingerprint(text: str, kind: str) -> str:
    lowered = text.lower()
    if "websocket" in lowered or "responses_websocket" in lowered:
        return f"{kind}:websocket"
    if "reconnecting..." in lowered:
        return f"{kind}:reconnecting"
    if "401" in lowered or "403" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
        return f"{kind}:auth"
    return f"{kind}:{_preview_text(text, 120)}"


def _codex_failure_diagnostic(stdout: str, stderr: str) -> str:
    haystack = "\n".join(part for part in (stdout, stderr) if part)
    kind = _codex_stream_error_kind(haystack)
    if kind == "permanent":
        return (
            "Codex 认证、模型或代理配置被服务端拒绝。请检查 OPENAI_API_KEY、Codex model、"
            "base_url 是否属于同一个 OpenAI/兼容网关；如果错误里出现 401/403，这不是网络抖动。"
        )
    if kind != "transient":
        return ""
    if "websocket" in haystack.lower() or "responses_websocket" in haystack.lower() or "reconnecting" in haystack.lower():
        return (
            "Codex 流式连接在自动重试后中断（Reconnecting... 5/5）。"
            "第三方 OpenAI 兼容代理在 Linux 服务器上常见原因：websocket 长连接不稳定。\n\n"
            "修复方法：\n"
            "1. 运行修复脚本: bash scripts/fix-codex-reconnecting.sh\n"
            "2. 或手动设置: /ai 代理 设置 codex <your-base-url>\n"
            "3. 重启服务: sudo systemctl restart ai-telegram-bot\n\n"
            "技术细节：脚本会配置 wire_api=\"responses\"、supports_websockets=false、"
            "增加重试次数和超时时间，避免 websocket 连接问题。"
        )
    return "Codex 流式连接在自动重试后中断。runner 已按失败处理；请继续或重试该任务。"


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


CODEX_SUBAGENT_ITEM_TYPES = {
    "subagent",
    "sub_agent",
    "child_agent",
    "agent_task",
    "delegated_task",
    "delegate",
    "review_agent",
    "reviewer_agent",
}


def _codex_subagent_status_events_enabled() -> bool:
    raw = os.environ.get("CODEX_SUBAGENT_STATUS_EVENTS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled", "关闭", "关"}


def _codex_subagent_name_from_command(command: str) -> str:
    lowered = command.lower()
    if "run-independent-review.sh" in lowered or "independent-review" in lowered:
        return "独立审查者AI"
    if re.search(r"(^|[/\s])codex(\.exe|\.cmd)?\s+exec\b", lowered):
        return "Codex 子 agent"
    if re.search(r"(^|[/\s])claude(\.exe|\.cmd)?(\s|$)", lowered):
        return "Claude Code 子 agent"
    return ""


def _codex_subagent_label(item: dict[str, Any], event_type: str = "") -> str:
    if not _codex_subagent_status_events_enabled():
        return ""
    item_type = str(item.get("type") or "").lower()
    command = _codex_command_text(item)
    agent_name = _first_item_text(
        item,
        ("agent", "agent_name", "subagent", "sub_agent", "role", "title", "label", "name"),
    )
    detected_name = agent_name if item_type in CODEX_SUBAGENT_ITEM_TYPES else _codex_subagent_name_from_command(command)
    if not detected_name:
        return ""
    detail = _preview_text(command or _codex_tool_name(item), 160)
    prefix = "子 agent 已完成" if event_type == "item.completed" else "子 agent 正在运行"
    message = f"{prefix}：{_preview_text(detected_name, 80)}"
    if detail:
        message = f"{message}；{detail}"
    return message


def _codex_item_label(item: dict[str, Any], event_type: str = "") -> str:
    item_type = str(item.get("type") or "item")
    item_type_normalized = item_type.lower()
    subagent_label = _codex_subagent_label(item, event_type)
    if subagent_label:
        return subagent_label
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
    if _codex_subagent_status_events_enabled() and (
        item_type in CODEX_SUBAGENT_ITEM_TYPES or _codex_subagent_name_from_command(_codex_command_text(item))
    ):
        return "subagent"
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
    context_warning = _codex_context_warning(event)
    if context_warning:
        fingerprint = f"context-warning:{context_warning}"
        if fingerprint not in seen:
            seen.add(fingerprint)
            emit({"run_id": run_id, "provider": "codex", "phase": "warning", "public_message_zh": context_warning})
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
        message = _codex_error_message(event) or "codex_error"
        phase = _codex_error_phase(message)
        payload = {"run_id": run_id, "provider": "codex", "phase": phase}
        if phase == "warning":
            payload["public_message_zh"] = f"Codex 流连接正在重试：{_preview_text(message, 180)}"
        else:
            payload["error"] = message
        emit(payload)
        return
    item = _codex_event_item(event)
    if not item:
        return
    label_event_type = event_type
    if event_type == "response_item":
        item_type = str(item.get("type") or "").lower()
        label_event_type = "item.completed" if item_type in CODEX_TOOL_OUTPUT_ITEM_TYPES else "item.started"
    if event_type not in {"item.started", "item.completed", "item.updated", "response_item"}:
        return
    fingerprint = f"{event_type}:{_codex_call_id(item)}:{item.get('status')}:{item.get('type')}:{item.get('exit_code')}:{item.get('text')}"
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    label = _codex_item_label(item, label_event_type)
    emit({"run_id": run_id, "provider": "codex", "phase": _codex_event_phase(item, label_event_type), "public_message_zh": label})


def _emit_codex_stderr_line(line: str, run_id: str, emit: Callable[[dict[str, Any]], None], seen: set[str]) -> None:
    text = line.strip()
    if not text:
        return
    kind = _codex_stream_error_kind(text)
    if not kind:
        return
    fingerprint = f"stderr-stream:{_codex_stream_error_fingerprint(text, kind)}"
    if fingerprint in seen:
        return
    seen.add(fingerprint)
    if kind == "permanent":
        emit({"run_id": run_id, "provider": "codex", "phase": "error", "error": _preview_text(text, 240)})
        return
    emit(
        {
            "run_id": run_id,
            "provider": "codex",
            "phase": "warning",
            "public_message_zh": f"Codex 流连接正在重试：{_preview_text(text, 180)}",
        }
    )


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
    state: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = _codex_subprocess_env()
    if emit is None:
        if state is None:
            return subprocess.run(command, cwd=workspace, env=env, input=prompt, text=True, capture_output=True, timeout=timeout_seconds, check=False)
        return run_registered(
            state,
            run_id,
            "codex",
            command,
            cwd=workspace,
            env=env,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            action="provider",
        )
    popen_kwargs = popen_process_group_kwargs()
    process = subprocess.Popen(
        command,
        cwd=workspace,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    )
    if state is not None:
        register_process(state, run_id, "codex", process, command, workspace, action="provider")
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    seen_events: set[str] = set()

    def read_stdout() -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                stdout_lines.append(line)
                _emit_codex_jsonl_events(line, run_id, emit, seen_events)
        except (ValueError, OSError):
            return

    def read_stderr() -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for line in stream:
                stderr_lines.append(line)
                _emit_codex_stderr_line(line, run_id, emit, seen_events)
        except (ValueError, OSError):
            return

    stdout_thread = threading.Thread(target=read_stdout, name=f"codex-stdout-{run_id}", daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, name=f"codex-stderr-{run_id}", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass
        process.wait(timeout=timeout_seconds)
        stdout_thread.join(timeout=30)
        stderr_thread.join(timeout=30)
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
        if state is not None:
            unregister_process(state, run_id, int(process.pid))
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
    timeout_seconds: int = 300,
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
        *_claude_timeout_args(provider_id),
        *_claude_max_turn_args(_claude_adapter_env(provider_id, "CLAUDE_MAX_TURNS", "0")),
        "--append-system-prompt",
        instruction_prompt,
    ]
    command = _claude_command_with_budget(command, reserved_usd)
    claude_model = normalize_model_name(provider_id, _claude_adapter_env(provider_id, "CLAUDE_MODEL", CLAUDE_DEFAULT_MODEL)).strip()
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
    state = _state_from_ledger(ledger)
    try:
        result = _run_claude_command(command, workspace, prompt, timeout_seconds, state=state, run_id=actual_run_id, provider=provider_id)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": provider_id, "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, provider_id, "timeout", "", None, -1)
    raw, output_text = _claude_output(result)
    attempt_raws: list[dict[str, Any] | None] = [raw]
    attempt_costs: list[float | None] = [_actual_cost_usd(raw)]
    transient_retry_started = False
    transient_retries = _claude_positive_int_value(_claude_adapter_env(provider_id, "CLAUDE_API_RETRY_ATTEMPTS", "5"), 5, max_value=10)
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
            result = _run_claude_command(command, workspace, prompt, timeout_seconds, state=state, run_id=actual_run_id, provider=provider_id)
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
                retry_result = _run_claude_command(
                    _claude_command_with_budget(command, remaining_budget_usd),
                    workspace,
                    _claude_chat_retry_prompt(prompt),
                    timeout_seconds,
                    state=state,
                    run_id=actual_run_id,
                    provider=provider_id,
                )
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


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def codex_command(
    prompt: str,
    workspace: Path,
    output_file: Path,
    instruction_prompt: str = "",
    native_session_id: str | None = None,
) -> list[str]:
    if not _codex_exec_help_has("--json"):
        raise RuntimeError("codex_json_unavailable")
    if not _codex_exec_help_has("--cd"):
        raise RuntimeError("codex_cd_unavailable")
    if not _codex_exec_help_has("--output-last-message"):
        raise RuntimeError("codex_output_last_message_unavailable")
    use_ephemeral = _env_truthy("CODEX_EXEC_EPHEMERAL", default=False) and _codex_exec_help_has("--ephemeral")
    resume_session_id = (native_session_id or "").strip()
    command = [
        *CODEX_EXEC_TEMPLATE,
        "--cd",
        str(workspace),
        "--output-last-message",
        str(output_file),
    ]
    if use_ephemeral:
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
    if resume_session_id and not use_ephemeral:
        if not _help_has(CODEX_EXEC_RESUME_HELP_COMMAND, "--json"):
            raise RuntimeError("codex_resume_json_unavailable")
        if not _help_has(CODEX_EXEC_RESUME_HELP_COMMAND, "--output-last-message"):
            raise RuntimeError("codex_resume_output_last_message_unavailable")
        command.extend(["resume", resume_session_id, "-"])
    else:
        command.extend(["--", "-"])
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
    native_session_id: str | None = None,
) -> ProviderResult:
    actual_run_id = run_id or str(uuid.uuid4())
    ledger.reserve(actual_run_id, "codex", reserved_usd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    output_file = workspace / f".ai-remote-codex-{actual_run_id}-last-message.txt"
    output_file.unlink(missing_ok=True)
    try:
        command = codex_command(prompt, workspace, output_file, instruction_prompt, native_session_id=native_session_id)
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
    state = _state_from_ledger(ledger)
    try:
        result = _run_codex_command(
            command,
            workspace,
            _codex_effective_prompt(prompt, instruction_prompt),
            timeout_seconds,
            actual_run_id,
            emit,
            state=state,
        )
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
    if result.returncode != 0:
        output_text = _redact_status_text(output_text)
        diagnostic = _codex_failure_diagnostic(result.stdout, result.stderr)
        if diagnostic:
            output_text = f"{diagnostic}\n\n原始错误:\n{output_text.rstrip()}" if output_text.strip() else diagnostic
    jsonl_summary = _codex_jsonl_run_summary(result.stdout)
    codex_thread_id = _codex_jsonl_thread_id(result.stdout)
    if result.stdout.strip():
        raw = {
            "stdout_jsonl": result.stdout,
            "native_session_id": codex_thread_id,
            "jsonl_summary": {
                "pending_tool_call_count": jsonl_summary.pending_tool_call_count,
                "pending_tool_call_labels": list(jsonl_summary.pending_tool_call_labels),
                "turn_completed": jsonl_summary.turn_completed,
                "context_warning": jsonl_summary.context_warning,
            },
        }
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    if result.returncode == 0 and jsonl_summary.pending_tool_call_count:
        details = ", ".join(label for label in jsonl_summary.pending_tool_call_labels[:3] if label)
        suffix = f" 未完成项：{details}" if details else ""
        output_text = f"Codex 进程提前结束：仍有 {jsonl_summary.pending_tool_call_count} 个工具调用没有完成输出。runner 已按中断处理，请继续或重试。{suffix}"
        status = "interrupted"
    elif result.returncode == 0 and not output_text.strip():
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
