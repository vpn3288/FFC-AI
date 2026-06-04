from __future__ import annotations

import shutil
import subprocess
from typing import Any


def _version(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return {"available": False, "path": None, "version": None}
    result = subprocess.run([command, "--version"], text=True, capture_output=True, check=False)
    return {"available": result.returncode == 0, "path": path, "version": result.stdout.strip() or result.stderr.strip()}


def discover_claude() -> dict[str, Any]:
    base = _version("claude")
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
    }
    return base


def discover_codex() -> dict[str, Any]:
    base = _version("codex")
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
    }
    return base


def provider_status() -> list[dict[str, Any]]:
    return [discover_claude(), discover_codex()]


CLAUDE_CHAT_ONLY_TEMPLATE = [
    "claude",
    "-p",
    "--bare",
    "--model",
    "claude-opus-4-6-20260130",
    "--output-format",
    "json",
    "--permission-mode",
    "plan",
    "--tools",
    "",
    "--no-session-persistence",
]


CODEX_EXEC_TEMPLATE = [
    "codex",
    "exec",
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--sandbox",
    "workspace-write",
    "--ask-for-approval",
    "never",
]
