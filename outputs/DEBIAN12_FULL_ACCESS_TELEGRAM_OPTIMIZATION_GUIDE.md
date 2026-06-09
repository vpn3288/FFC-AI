# Debian 12 Full-Access Telegram Optimization Guide

This guide translates the latest user request into implementation rules for FFC-AI. It is a working contract for installer, runner, Telegram, and validation changes.

## Goal

FFC-AI must let a beginner install Claude Code, Codex, VSCode, the remote runner, and Telegram integration on Debian 12 with one copied GitHub command. The installed AI tools run as root inside the user's VM or VPS and can use their native full-access modes. FFC-AI must not patch, fork, monkey-patch, or rewrite the AI tools themselves. All optimization happens in the host environment, wrapper scripts, config files, systemd units, runtime validation, and Telegram command layer.

## Permission Model

- Debian 12 VM/VPS root is the intended security boundary.
- `ai-remote-runner.service` and `ai-telegram-bot.service` run as `root` by default.
- Codex runs with `approval_policy=never`, `sandbox_mode=danger-full-access`, network enabled, `--add-dir /`, and available full-access exec flags.
- Claude Code runs with official CLI flags for full file and shell access. Avoid flags known to be rejected by root mode unless official CLI supports them.
- VSCode installs globally and exposes `code-root`, using root user-data and extension directories.
- Telegram can trigger the same runner permissions as local root, but only for explicitly paired chat IDs.
- High-risk remote actions must remain parseable and auditable. Do not add hidden shell backdoors or raw unauthenticated HTTP control paths.

## Installation Requirements

- `scripts/install-runner.sh` must support:
  - `AI_RUNNER_COMPONENTS=all,telegram`: install Claude Code, Codex, VSCode, runner, and Telegram service.
  - `AI_RUNNER_COMPONENTS=codex,telegram`, `claude-code,telegram`, and `vscode,telegram`: focused installs still work.
  - `AI_RUNNER_COMPONENTS=vscode`: install VSCode/root wrapper without runner services.
- `all`, `full`, and `core` are no longer rejected. They mean "install all primary tools".
- Multiple providers may be configured on one machine. The runner still chooses one default provider at a time and can switch with `/ai 提供商 使用 <provider>`.
- Default provider order for multi-provider installs is `codex`, then `claude-code`, then `vscode`, unless `AI_DEFAULT_PROVIDER` is supplied.
- The installer must preserve existing Telegram/Mattermost pairing values and previous secrets unless the user explicitly replaces them.
- Dependencies should prefer stable/LTS upstreams:
  - Debian 12 apt packages for Python, git, curl, certificates, gpg, systemd, and bubblewrap.
  - Existing Node.js must be an even-major stable/LTS release at least 22; fresh Debian 12 installs should use Node.js 24.x LTS from NodeSource.
  - official Claude Code npm package/version pinned by `versions.lock`, using the npm `stable` dist-tag unless the lock is intentionally updated.
  - Codex package/version from `versions.lock`.
  - VSCode stable apt repository from Microsoft.

## API And Model Configuration

Telegram `/ai` configuration commands must update provider-specific surfaces:

- Codex/OpenAI-compatible:
  - API key goes to Codex `auth.json` as `OPENAI_API_KEY` and runner `config.env`.
  - Base URL goes to Codex `config.toml` as `openai_base_url` and runner `CODEX_BASE_URL`.
  - Model goes to Codex `config.toml` and runner `CODEX_MODEL`.
- Claude Code:
  - API key goes to Claude settings/env as `ANTHROPIC_AUTH_TOKEN` and runner `config.env`.
  - Base URL goes to `ANTHROPIC_BASE_URL`.
  - Model goes to `CLAUDE_MODEL`.
- VSCode Claude backend:
  - API key/base URL use the same Anthropic gateway keys.
  - Model uses `VSCODE_CLAUDE_MODEL`, not `CLAUDE_MODEL`.

Validation rules:

- Reject empty API keys, whitespace-containing keys, and malformed base URLs.
- Reject obvious key-family mixups when detection is reliable.
- Never echo full secrets in Telegram, status files, logs, or tests.
- `/ai 配置 查看 <provider>` may show whether a key is configured and the redacted shape, never the full key.

## Optional CC Switch Integration

CC Switch may be supported as an optional profile manager for users who also want a desktop/config-switching UI. It must not become a required dependency for PVE/VPS/headless Telegram operation.

- For users with third-party Claude-compatible and OpenAI-compatible proxy endpoints, CC Switch is a recommended optional profile layer because it can keep multiple base URL/API key/model profiles understandable to humans.
- Telegram `/ai` commands remain the primary remote control path and must work without CC Switch installed.
- The bootstrap installer must guide beginners through whether to install CC Switch. Default to skip on noninteractive/headless installs unless `AI_INSTALL_CC_SWITCH=true` or `AI_RUNNER_COMPONENTS=cc-switch` is supplied.
- `scripts/install-runner.sh` may install CC Switch from the official GitHub Releases Linux `.deb` on Debian/Ubuntu x86_64 or arm64.
- If CC Switch is installed, FFC-AI may import/export the active CC Switch profile only through documented local config files or an official CLI if available.
- For Claude Code, CC Switch-compatible changes must map to `~/.claude/settings.json` env values such as `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL`.
- For Codex, CC Switch-compatible changes must map to `$CODEX_HOME/auth.json` and `$CODEX_HOME/config.toml`, plus runner `config.env`.
- For VSCode in this project, CC Switch may manage the shared Claude/Anthropic API key and base URL, but the runner must keep `VSCODE_CLAUDE_MODEL` separate from `CLAUDE_MODEL` so VSCode backend model changes do not accidentally change Claude Code.
- `/ai 密钥 设置`, `/ai 代理 设置`, and model commands must validate provider family before writing, even when a future CC Switch bridge is enabled.
- Do not invoke GUI-only CC Switch flows from systemd services or Telegram commands.
- Telegram must expose CC Switch-compatible commands for status, API key, base URL, and model changes. These commands must reuse the same provider-family validation as the normal `/ai 密钥 设置`, `/ai 代理 设置`, and model commands.

Recommended remote workflow:

- Use CC Switch locally or through any documented CLI to choose human-friendly profiles for third-party gateways.
- Use `/ai 配置 查看 <provider>` from Telegram to verify what the headless runner will actually use.
- Use `/ai 密钥 设置`, `/ai 代理 设置`, `/ai GPT模型 设置`, and `/ai Claude模型 设置` when away from the machine; these commands must update the same effective provider surfaces and never rely on GUI state.
- Use `/ai CC Switch 状态`, `/ai CC Switch 密钥 设置 <provider> <key>`, `/ai CC Switch 代理 设置 <provider> <url>`, and `/ai CC Switch GPT模型/Claude模型 设置 <provider> <model>` when the user wants the action recorded as CC Switch-compatible.

## Telegram Commands

Existing `/ai` commands must stay stable. Add or preserve these user-facing controls:

- `/ai 继续`: preserve long conversation policy and allow the user to continue manually.
- `/ai 定时继续`: show auto-continue status for the current Telegram chat.
- `/ai 定时继续 设置 <seconds>`: periodically send the plain prompt `继续` for that chat.
- `/ai 定时继续 关闭`: disable periodic continue.
- `/ai 停止` or `/ai 取消`: request cancellation and record intent without killing unrelated processes.
- `/ai 强行停止`: terminate active provider/local-command process trees that were started and registered by this runner.

Auto-continue must not start a second task while the same Telegram chat already has an active unfinished task. It is a recovery mechanism for broken or finished long tasks, not a concurrency amplifier.

Force-stop must only target the runner's active process registry. It must not scan broad process names such as `codex`, `claude`, `node`, or `python`.

## Runtime Stability

- Telegram polling must recover from transient network disconnects and keep running.
- Long provider calls must emit visible heartbeat/status messages.
- Codex JSONL events should surface subagent, command, file-edit, web-search, and context warnings.
- Provider calls should use process groups/sessions where possible so force-stop can terminate child commands.
- Runner should save task status to state files so `/ai 状态` can show recent and running tasks.
- Codex phone runs should default to ephemeral execution so Codex CLI session history does not grow without bound; runner-managed context remains the long-memory layer.
- Auto-compaction and hard context stops must not silently discard user instructions.

## Safety Boundaries

- Do not modify the installed AI software packages.
- Do not store secrets in GitHub, public docs, prompts, status messages, or review artifacts.
- Do not add unauthenticated network endpoints.
- Do not make `/ai` config commands write arbitrary files. They must only write known provider config files and runner state files.
- Do not "optimize" by disabling the AI tools' native ability to run long tasks, shell commands, edits, subagents, or browser/network work.

## Acceptance Checklist

- `AI_RUNNER_COMPONENTS=all,telegram scripts/install-runner.sh --dry-run` succeeds and shows Claude Code, Codex, VSCode, runner, and Telegram stages.
- `AI_RUNNER_COMPONENTS=codex,telegram` still installs only Codex plus runner/Telegram.
- `/ai 密钥 设置 codex ...`, `/ai 密钥 设置 claude-code ...`, and `/ai 密钥 设置 vscode ...` update different provider-specific config keys.
- `/ai 代理 设置 ...` rejects non-HTTP URLs and whitespace.
- `/ai 定时继续 设置 300` persists a chat-scoped schedule.
- The Telegram scheduler sends `继续` only when no task in that chat is currently running.
- `/ai 强行停止` terminates only registered active processes and reports what was stopped.
- `AI_INSTALL_CC_SWITCH=true AI_RUNNER_COMPONENTS=codex,telegram scripts/install-runner.sh --dry-run` shows the CC Switch install stage.
- `/ai CC Switch 密钥 设置 codex ...` writes Codex live config, rejects Anthropic key-family mistakes, and records `cc-switch-sync.json`.
- Tests cover commands, executor behavior, Telegram scheduler behavior, installer multi-provider behavior, and config validation.
