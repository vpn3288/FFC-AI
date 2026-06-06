from __future__ import annotations

import shutil
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .budget import BudgetLedger
from .instructions import InstructionStore


PROBE_TIMEOUT_SECONDS = 30
AUTH_PROBE_TIMEOUT_SECONDS = 60


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
        "full_access_available": _help_has(["claude", "-p", "--help"], "--dangerously-skip-permissions", "bypassPermissions"),
        "auth_check_available": auth_check,
        "print_json_available": base["available"],
        "bare_flag_available": _help_has(["claude", "-p", "--help"], "--bare"),
        "append_system_prompt_available": _help_has(["claude", "-p", "--help"], "--append-system-prompt"),
    }
    return base


def discover_codex() -> dict[str, Any]:
    base = _version("codex")
    exec_available = _returns_ok(["codex", "exec", "--help"])
    approval_config_available = _returns_ok(["codex", "exec", "-c", 'approval_policy="never"', "--help"])
    sandbox_available = _help_has(["codex", "exec", "--help"], "--sandbox")
    bypass_available = _help_has(["codex", "exec", "--help"], "--dangerously-bypass-approvals-and-sandbox")
    base["provider"] = "codex"
    base["capabilities"] = {
        "new_conversation": True,
        "continue_conversation": False,
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
        "full_access_available": bypass_available or sandbox_available,
    }
    return base


def provider_status() -> list[dict[str, Any]]:
    return [discover_claude(), discover_codex()]


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
    "bypassPermissions",
    "--dangerously-skip-permissions",
    "--tools",
    "default",
]


CODEX_EXEC_TEMPLATE = [
    "codex",
    "exec",
    "-c",
    'approval_policy="never"',
    "-c",
    "network_access=\"enabled\"",
    "-c",
    "shell_environment_policy.inherit=all",
    "--json",
]


CLAUDE_DEFAULT_MODEL = ""
CLAUDE_MODEL_FALLBACKS = [
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


def _claude_command_with_budget(command: list[str], budget_usd: float) -> list[str]:
    updated = list(command)
    index = updated.index("--max-budget-usd")
    updated[index + 1] = f"{max(0.0, budget_usd):.6f}".rstrip("0").rstrip(".") or "0"
    return updated


def _claude_recorded_cost(first_cost_usd: float | None, retry_cost_usd: float | None, retry_started: bool, reserved_usd: float) -> float | None:
    if retry_started and (retry_cost_usd is None or retry_cost_usd <= 0):
        return reserved_usd
    if first_cost_usd is not None and retry_cost_usd is not None:
        return first_cost_usd + retry_cost_usd
    if retry_cost_usd is not None:
        return retry_cost_usd
    if first_cost_usd is not None:
        return first_cost_usd
    return None


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
    actual_run_id = run_id or str(uuid.uuid4())
    ledger.reserve(actual_run_id, "claude-code", reserved_usd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    template = {
        "chat": CLAUDE_CHAT_ONLY_TEMPLATE,
        "edit": CLAUDE_EDIT_APPROVED_TEMPLATE,
        "shell": CLAUDE_SHELL_APPROVED_TEMPLATE,
        "full": CLAUDE_FULL_ACCESS_TEMPLATE,
    }.get(permission_scope, CLAUDE_FULL_ACCESS_TEMPLATE)
    command = [
        *template,
        "--max-turns",
        os.environ.get("CLAUDE_MAX_TURNS", "12"),
        "--max-budget-usd",
        str(reserved_usd),
        "--append-system-prompt",
        instruction_prompt,
    ]
    claude_model = os.environ.get("CLAUDE_MODEL", CLAUDE_DEFAULT_MODEL).strip()
    if claude_model:
        command.extend(["--model", claude_model])
    if emit:
        emit(
            {
                "run_id": actual_run_id,
                "provider": "claude-code",
                "phase": "calling_model",
                "public_message_zh": "正在调用 Claude Code：模型思考、工具执行或联网等待中。",
            }
        )
    try:
        result = _run_claude_command(command, workspace, prompt, timeout_seconds)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, "claude-code", "timeout", "", None, -1)
    raw, output_text = _claude_output(result)
    first_cost_usd = _actual_cost_usd(raw)
    retry_cost_usd: float | None = None
    retry_started = False
    if result.returncode == 0 and permission_scope in {"chat", "full"} and not output_text.strip():
        fallback = _short_chat_fallback(prompt)
        if fallback:
            output_text = fallback
            if emit:
                emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "warning", "public_message_zh": "模型返回空内容，已使用短消息安全回复。"})
        remaining_budget_usd = reserved_usd - first_cost_usd if first_cost_usd is not None else 0.0
        if not fallback and remaining_budget_usd > 0:
            if emit:
                emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "warning", "public_message_zh": "模型返回空内容，正在自动重试一次。"})
            try:
                retry_started = True
                retry_result = _run_claude_command(_claude_command_with_budget(command, remaining_budget_usd), workspace, _claude_chat_retry_prompt(prompt), timeout_seconds)
                retry_raw, retry_output = _claude_output(retry_result)
                retry_cost_usd = _actual_cost_usd(retry_raw)
                result = retry_result
                raw = {"first_attempt": raw, "retry_attempt": retry_raw}
                output_text = retry_output
            except subprocess.TimeoutExpired:
                ledger.complete(actual_run_id, _claude_recorded_cost(first_cost_usd, None, True, reserved_usd), status="timeout")
                if emit:
                    emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "error", "error": "timeout_after_empty_retry"})
                return ProviderResult(actual_run_id, "claude-code", "timeout", "", raw, -1)
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    if result.returncode == 0 and not output_text.strip():
        output_text = "Claude Code 返回了空内容；runner 已按失败处理。请重试，或把问题描述得更具体。"
        status = "empty_output"
    else:
        status = "completed" if result.returncode == 0 else "failed"
    actual_cost_usd = _claude_recorded_cost(first_cost_usd, retry_cost_usd, retry_started, reserved_usd)
    if actual_cost_usd is None:
        actual_cost_usd = _actual_cost_usd(raw)
    ledger.complete(actual_run_id, actual_cost_usd, status=status)
    if emit:
        event = {"run_id": actual_run_id, "provider": "claude-code", "phase": "done" if status == "completed" else "error"}
        if status != "completed":
            event["error"] = output_text or f"returncode={result.returncode}"
        emit(event)
    return ProviderResult(actual_run_id, "claude-code", status, output_text, raw, result.returncode)


def codex_command(prompt: str, workspace: Path, output_file: Path, instruction_prompt: str = "") -> list[str]:
    effective_prompt = f"{instruction_prompt}\n\n# User Task\n{prompt}" if instruction_prompt else prompt
    command = [
        *CODEX_EXEC_TEMPLATE,
        "--cd",
        str(workspace),
        "--output-last-message",
        str(output_file),
        "--",
        effective_prompt,
    ]
    if _help_has(["codex", "exec", "--help"], "--dangerously-bypass-approvals-and-sandbox"):
        command.insert(command.index("--cd"), "--dangerously-bypass-approvals-and-sandbox")
    elif _help_has(["codex", "exec", "--help"], "--sandbox"):
        command[command.index("--cd") : command.index("--cd")] = ["--sandbox", "danger-full-access"]
    else:
        raise RuntimeError("codex_full_access_unavailable")
    if _help_has(["codex", "exec", "--help"], "--dangerously-bypass-hook-trust"):
        command.insert(command.index("--cd"), "--dangerously-bypass-hook-trust")
    if _help_has(["codex", "exec", "--help"], "--ignore-rules"):
        command.insert(command.index("--cd"), "--ignore-rules")
    if _help_has(["codex", "exec", "--help"], "--add-dir"):
        command[command.index("--cd") : command.index("--cd")] = ["--add-dir", "/"]
    if _help_has(["codex", "exec", "--help"], "--skip-git-repo-check"):
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
    output_file = workspace / ".ai-remote-codex-last-message.txt"
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
        result = subprocess.run(command, cwd=workspace, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": "codex", "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, "codex", "timeout", "", None, -1)
    if output_file.exists():
        output_text = output_file.read_text(encoding="utf-8")
    else:
        output_text = result.stdout or result.stderr
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    status = "completed" if result.returncode == 0 else "failed"
    ledger.complete(actual_run_id, None, status=status)
    if emit:
        event = {"run_id": actual_run_id, "provider": "codex", "phase": "done" if status == "completed" else "error"}
        if status != "completed":
            event["error"] = output_text or f"returncode={result.returncode}"
        emit(event)
    return ProviderResult(actual_run_id, "codex", status, output_text, None, result.returncode)
