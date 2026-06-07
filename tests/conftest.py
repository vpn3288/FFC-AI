from __future__ import annotations

import os

import pytest


HOST_CONFIG_ENV_VARS = (
    "AI_BRIDGE_SHARED_SECRET",
    "AI_DEFAULT_PROVIDER",
    "AI_MATTERMOST_HEARTBEAT_SECONDS",
    "AI_PERMISSION_SCOPE",
    "AI_REMOTE_STATE",
    "AI_REQUIRE_SHELL_CONFIRMATION",
    "AI_RUNNER_COMPONENTS",
    "AI_RUNNER_PROVIDERS",
    "AI_TASK_RESERVED_USD",
    "AI_TOOL_HOME",
    "AI_WORKSPACE_ROOT",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_MAX_TURNS",
    "CLAUDE_MODEL",
    "VSCODE_CLAUDE_API_RETRY_ATTEMPTS",
    "VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS",
    "VSCODE_CLAUDE_MAX_TURNS",
    "VSCODE_CLAUDE_MODEL",
    "CODEX_BASE_URL",
    "CODEX_CI",
    "CODEX_HOME",
    "CODEX_MANAGED_BY_NPM",
    "CODEX_MANAGED_PACKAGE_ROOT",
    "CODEX_MODEL",
    "CODEX_MODEL_PROVIDER",
    "CODEX_REVIEW_API_KEY",
    "CODEX_REVIEW_BASE_URL",
    "MATTERMOST_SLASH_TOKEN",
    "MATTERMOST_WEBHOOK_URL",
    "OPENAI_API_KEY",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_API_BASE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_RESERVED_USD",
    "TELEGRAM_STATUS_INTERVAL_SECONDS",
)


def _clear_host_ai_config() -> None:
    for name in HOST_CONFIG_ENV_VARS:
        os.environ.pop(name, None)


_clear_host_ai_config()


def pytest_runtest_setup(item: pytest.Item) -> None:
    _clear_host_ai_config()


@pytest.fixture(autouse=True)
def isolate_host_ai_config() -> None:
    _clear_host_ai_config()
