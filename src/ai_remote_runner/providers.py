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


SAFE_ENV_KEYS = [
    "PATH",
    "HOME",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
]


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
        "shell_commands": False,
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
    base["provider"] = "codex"
    base["capabilities"] = {
        "new_conversation": True,
        "continue_conversation": False,
        "manual_compact": False,
        "auto_compact": False,
        "context_usage": "estimated",
        "status_events": True,
        "file_edits": True,
        "shell_commands": False,
        "exec_available": exec_available,
        "approval_config_available": approval_config_available,
        "sandbox_available": sandbox_available,
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
    "plan",
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
    "plan",
    "--tools",
    "Read,Grep,Glob,Edit,Write,Bash",
    "--no-session-persistence",
]


CODEX_EXEC_TEMPLATE = [
    "codex",
    "exec",
    "-c",
    'approval_policy="never"',
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "workspace-write",
]


CLAUDE_DEFAULT_MODEL = "claude-opus-4-6-20260130"
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
    permission_scope: str = "chat",
) -> ProviderResult:
    actual_run_id = run_id or str(uuid.uuid4())
    ledger.reserve(actual_run_id, "claude-code", reserved_usd, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes)
    template = {
        "chat": CLAUDE_CHAT_ONLY_TEMPLATE,
        "edit": CLAUDE_EDIT_APPROVED_TEMPLATE,
        "shell": CLAUDE_SHELL_APPROVED_TEMPLATE,
    }.get(permission_scope, CLAUDE_CHAT_ONLY_TEMPLATE)
    command = [
        *template,
        "--model",
        os.environ.get("CLAUDE_MODEL", CLAUDE_DEFAULT_MODEL),
        "--max-turns",
        os.environ.get("CLAUDE_MAX_TURNS", "12"),
        "--max-budget-usd",
        str(reserved_usd),
        "--append-system-prompt",
        instruction_prompt,
        "--",
        prompt,
    ]
    if emit:
        emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "calling_model"})
    env = {key: value for key in SAFE_ENV_KEYS if (value := os.environ.get(key))}
    try:
        result = subprocess.run(command, cwd=workspace, env=env, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        if emit:
            emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "error", "error": "timeout"})
        return ProviderResult(actual_run_id, "claude-code", "timeout", "", None, -1)
    raw: dict[str, Any] | None = None
    output_text = result.stdout
    try:
        raw = json.loads(result.stdout)
        output_text = str(raw.get("result") or raw.get("message") or result.stdout)
    except json.JSONDecodeError:
        pass
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    ledger.complete(actual_run_id, _actual_cost_usd(raw), status="completed" if result.returncode == 0 else "failed")
    if emit:
        emit({"run_id": actual_run_id, "provider": "claude-code", "phase": "done" if result.returncode == 0 else "error"})
    return ProviderResult(actual_run_id, "claude-code", "completed" if result.returncode == 0 else "failed", output_text, raw, result.returncode)


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
    if not _help_has(["codex", "exec", "--help"], "--sandbox"):
        sandbox_index = command.index("--sandbox")
        if sandbox_index + 1 >= len(command) or not command[sandbox_index + 1].startswith("workspace-"):
            raise RuntimeError("unexpected_codex_sandbox_template")
        del command[sandbox_index : sandbox_index + 2]
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
    command = codex_command(prompt, workspace, output_file, instruction_prompt)
    if emit:
        emit({"run_id": actual_run_id, "provider": "codex", "phase": "calling_model"})
    try:
        result = subprocess.run(command, cwd=workspace, text=True, capture_output=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        ledger.complete(actual_run_id, None, status="timeout")
        return ProviderResult(actual_run_id, "codex", "timeout", "", None, -1)
    output_text = output_file.read_text(encoding="utf-8") if output_file.exists() else result.stdout
    if len(output_text.encode("utf-8")) > max_output_bytes:
        output_text = output_text.encode("utf-8")[:max_output_bytes].decode("utf-8", errors="ignore")
    ledger.complete(actual_run_id, None, status="completed" if result.returncode == 0 else "failed")
    return ProviderResult(actual_run_id, "codex", "completed" if result.returncode == 0 else "failed", output_text, None, result.returncode)
