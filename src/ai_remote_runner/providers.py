from __future__ import annotations

import shutil
import json
import os
import subprocess
import threading
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
        "output_last_message_available": output_last_message_available,
        "json_available": json_available,
        "cd_available": cd_available,
        "add_dir_available": add_dir_available,
        "skip_git_repo_check_available": skip_git_repo_check_available,
        "ephemeral_available": _codex_exec_help_has("--ephemeral"),
        "dangerously_bypass_hook_trust_available": _codex_exec_help_has("--dangerously-bypass-hook-trust"),
        "ignore_rules_available": _codex_exec_help_has("--ignore-rules"),
    }
    return base


def provider_status() -> list[dict[str, Any]]:
    configured_raw = os.environ.get("AI_RUNNER_PROVIDERS")
    if configured_raw is None:
        configured = None
    else:
        configured = {("claude-code" if item.strip() == "claude" else item.strip()) for item in configured_raw.split(",") if item.strip()}
    if configured is None:
        return [discover_claude(), discover_codex()]

    providers: list[dict[str, Any]] = []
    for name, discover in (("claude-code", discover_claude), ("codex", discover_codex)):
        if name in configured:
            provider = discover()
            provider["configured"] = True
        else:
            provider = {
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


def _preview_text(text: str, max_chars: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 6].rstrip() + " ..."


def _codex_item_label(item: dict[str, Any], event_type: str = "") -> str:
    item_type = str(item.get("type") or "item")
    if item_type == "command_execution":
        command = item.get("command")
        command_text = _preview_text(str(command), 180) if command else ""
        if event_type == "item.completed":
            exit_code = item.get("exit_code")
            if exit_code is not None:
                return f"命令已完成：exit={exit_code} {command_text}".strip()
            return f"命令已完成：{command_text}".strip() if command_text else "命令已完成。"
        return f"运行命令：{command_text}" if command_text else "正在运行命令。"
    if item_type == "file_change":
        path = item.get("path") or item.get("file")
        if event_type == "item.completed":
            return f"文件修改已完成：{path}" if path else "文件修改已完成。"
        return f"正在修改文件：{path}" if path else "正在修改文件。"
    if item_type == "reasoning":
        return "正在推理和规划。"
    if item_type == "mcp_tool_call":
        name = item.get("name") or item.get("tool")
        if event_type == "item.completed":
            return f"MCP 工具调用已完成：{name}" if name else "MCP 工具调用已完成。"
        return f"正在调用 MCP 工具：{name}" if name else "正在调用 MCP 工具。"
    if item_type == "web_search":
        if event_type == "item.completed":
            return "联网检索已完成。"
        return "正在联网检索。"
    if item_type == "plan_update":
        return "正在更新执行计划。"
    if item_type == "agent_message":
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return f"正在整理最终回复：{_preview_text(text)}"
        return "正在生成回复。"
    return f"正在处理 {item_type}。"


def _codex_event_phase(item: dict[str, Any], event_type: str = "") -> str:
    item_type = str(item.get("type") or "")
    if item_type == "command_execution":
        return "running_command"
    if item_type == "file_change":
        return "writing_files"
    if item_type == "reasoning":
        return "thinking"
    if item_type == "web_search":
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
