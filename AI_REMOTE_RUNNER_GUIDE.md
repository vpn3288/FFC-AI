# AI Remote Runner Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Core Requirements

The runner MUST provide the core mobile-controlled AI execution layer.

Core install MUST NOT depend on optional skills, MCP extensions, or CLI tools.

Telegram MUST NOT be used.

The runner MUST support:

- Claude Code adapter;
- Codex adapter;
- future AI adapters;
- Mattermost/Matrix communication bridge;
- Chinese command aliases;
- canonical English internal actions;
- status/progress event stream;
- input/output display;
- context telemetry;
- automatic context warning;
- manual context compaction;
- new conversation;
- per-request new conversation;
- credential broker;
- `global.md`;
- `project.md`;
- command index;
- Chinese descriptions for commands, skills, MCP extensions, CLI tools, and provider features;
- optional extension/tool installation after core-ready.

## 2. Architecture

```text
phone client
  -> communication platform
  -> bridge
  -> AI remote runner
  -> provider adapter
       -> Claude Code
       -> Codex
       -> other AI
```

Communication platform MAY run on VPS.

AI runner SHOULD run on local small host, Debian VM, or WSL when source code and local tools are needed.

VPS communication server MUST NOT require AI provider secrets for core operation.

## 3. Provider Adapter Contract

Every provider adapter MUST expose:

```json
{
  "provider": "claude-code|codex|openai|anthropic|custom",
  "capabilities": {
    "new_conversation": true,
    "continue_conversation": true,
    "manual_compact": true,
    "auto_compact": true,
    "context_usage": "native|estimated|unknown",
    "status_events": true,
    "file_edits": true,
    "shell_commands": false
  }
}
```

Adapter MUST return unsupported features explicitly:

```json
{
  "feature": "native_compact",
  "supported": false,
  "reason": "provider adapter cannot verify native command"
}
```

Adapter MUST NOT invent provider APIs.

## 4. Claude Code Adapter

Verified local Claude Code CLI facts:

- `claude -p` runs print mode.
- `--output-format json` returns single result JSON.
- `--output-format stream-json` streams events.
- `--max-budget-usd` limits per-call spend in print mode.
- `--max-turns` limits turns.
- `--permission-mode plan` is valid.
- `--tools ""` disables all built-in tools.
- `--tools default` enables default built-in tools.
- `--allowedTools` means tools allowed without prompting. It MUST NOT be used as the primary restriction mechanism.
- `--disallowedTools` denies tools.
- `--no-session-persistence` disables resume.
- `--continue` and `--resume` require session persistence.
- `--bare` reduces implicit context, hooks, plugin sync, keychain reads, auto-memory, background prefetches, and `CLAUDE.md` auto-discovery.
- `--system-prompt` and `--append-system-prompt` are valid.
- `claude auth status --json` returns JSON containing `loggedIn`, `authMethod`, and `apiProvider`.

Default remote Claude command:

```bash
cd "$RUNNER_WORKSPACE"
env -i \
  HOME="$CLAUDE_RUNNER_HOME" \
  PATH="$SAFE_PATH" \
  ANTHROPIC_BASE_URL="$ANTHROPIC_BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  HTTP_PROXY="$CLAUDE_HTTP_PROXY" \
  HTTPS_PROXY="$CLAUDE_HTTPS_PROXY" \
  NO_PROXY="$NO_PROXY" \
  claude -p --bare \
    --output-format json \
    --max-turns "$CLAUDE_MAX_TURNS" \
    --max-budget-usd "$CLAUDE_MAX_BUDGET_USD" \
    --permission-mode plan \
    --tools "$CLAUDE_TOOLS" \
    --disallowedTools "$CLAUDE_DISALLOWED_TOOLS" \
    --no-session-persistence \
    --append-system-prompt "$RUNNER_INSTRUCTION_PROMPT" \
    "$PROMPT"
```

Default:

```text
CLAUDE_TOOLS=""
CLAUDE_DISALLOWED_TOOLS="Bash,Edit,Write"
```

`Bash` MUST NOT be enabled by default.

## 5. Codex Adapter

Codex support MUST be adapter-based.

Supported Codex surfaces MAY include:

- local CLI;
- desktop/app thread;
- cloud/web;
- IDE extension.

The adapter MUST discover available capabilities at runtime.

The adapter MUST NOT assume private Codex app tools exist on public user servers.

Required adapter operations:

- send task;
- create new conversation if supported;
- continue conversation if supported;
- report status;
- estimate/report context usage;
- compact context natively or emulate;
- expose unsupported features with reason.

## 6. Optional Extension/Tool Bundle

The following items are optional post-core enhancements. They MUST NOT block `core_ready`.

```yaml
skills:
  - id: find-skill
    display: Find-Skill
    required_for_core: false
    source: unresolved
  - id: frontend-design
    display: Frontend-Design
    required_for_core: false
    source: unresolved
  - id: skill-creator
    display: Skill-Creator
    required_for_core: false
    source: local-or-curated
  - id: karpathy-skill
    display: Karpathy skill
    required_for_core: false
    source: unresolved
mcp_extensions:
  - id: filesystem
    required_for_core: false
  - id: git
    required_for_core: false
  - id: github
    required_for_core: false
  - id: browser
    required_for_core: false
  - id: openai-docs
    required_for_core: false
cli_tools:
  - id: opencli
    display: OpenCLI
    required_for_core: false
    source: unresolved
  - id: cli
    display: CLI
    required_for_core: false
    source: unresolved
  - id: gemini-cli
    display: gemini-CLI
    required_for_core: false
    source: unresolved
```

Rules:

- `source: unresolved` MUST trigger user/master-writer source confirmation before install.
- Optional unresolved items MUST NOT block `core_ready`.
- `optional_bundle_ready` requires selected optional items to be resolved, pinned, and installed.
- External tools MUST be pinned by version, checksum, commit, or release tag before automated install.
- Installed optional items MUST appear in `/功能`, `/命令`, and `/`.
- Newly installed items MUST receive Chinese description metadata.

Phone commands:

```text
/扩展 列表
/扩展 安装 <id>
/扩展 启用 <id>
/扩展 禁用 <id>
/工具 列表
/工具 安装 <id>
/mcp 列表
/mcp 启用 <id>
```

## 7. Conversation Policy

Supported policies:

```text
continue
new_each_request
ask_each_request
```

Commands:

```text
/新对话
/new
/继续
/continue
/每次新对话
/mode new_each
/持续对话
/mode continue
```

Rules:

- `new_each_request` MUST create a fresh provider conversation for every user task.
- `continue` MUST reuse the selected conversation only when provider continuation is safe.
- If provider continuation is unavailable, runner MUST emulate continuation using compacted summary context.
- `/status` MUST show current policy, provider, workspace, and conversation id.

## 8. Instruction Files

Canonical files:

```text
/srv/ai-remote/instructions/global.md
/srv/ai-workspaces/<workspace_id>/project.md
```

Phone commands:

```text
/全局 查看
/全局 设置
/全局 追加
/全局 替换
/全局 回滚 <snapshot>
/全局 清空
/项目 查看
/项目 设置
/项目 追加
/项目 替换
/项目 回滚 <snapshot>
/项目 清空
```

Rules:

- `global.md` applies across workspaces/providers.
- `project.md` applies to one workspace.
- Replacing or clearing requires confirmation.
- Appending MAY require no confirmation unless policy demands it.
- Every change MUST create snapshot.
- AI agents MAY propose changes.
- Human approval MUST be required before AI-proposed instruction changes are applied.
- Run status MUST include SHA256 of applied `global.md` and `project.md`.

Provider application:

- Claude Code adapter MAY inject via `--system-prompt`, `--append-system-prompt`, or verified provider memory files.
- If Claude Code uses `--bare`, implicit memory discovery MUST NOT be assumed.
- Codex adapter SHOULD map to `AGENTS.md` or verified Codex instruction mechanism when available.
- If provider mechanism is unavailable, adapter MUST inject instruction text into runner-managed prompt wrapper.

## 9. Command And Feature Index

Commands:

```text
/
/帮助
/命令
/功能
/索引
/说明
/说明 生成 <id>
/说明 编辑 <id>
```

`/` alone MUST show a categorized Chinese index.

Index MUST include:

- canonical action;
- Chinese aliases;
- English aliases;
- Chinese description;
- enabled/disabled;
- native/emulated/unsupported;
- requires confirmation;
- provider;
- installed/missing state for optional items;
- source/version for installed optional items.

Index item schema:

```json
{
  "canonical": "compact_context",
  "aliases": ["/压缩", "/compact"],
  "description_zh": "压缩当前对话上下文，必要时创建摘要并开启新会话。",
  "enabled": true,
  "provider_native": false,
  "implemented_by": "runner",
  "requires_confirmation": false
}
```

Chinese description rules:

- Install of any skill/CLI/MCP extension MUST register `description_zh`.
- Description generation MUST NOT modify the installed code.
- If no metadata exists, index MUST show `说明缺失`.
- `/说明 生成 <id>` MAY generate metadata from README/help/manifest.
- If AI generation is used, mark `description_source=generated_ai`.

## 10. Context Telemetry And Compaction

Context state:

```json
{
  "conversation_id": "string",
  "provider": "claude-code|codex|other",
  "context_limit_tokens": 200000,
  "context_used_tokens": 142000,
  "context_used_percent": 71,
  "measurement": "native|estimated|unknown",
  "auto_compact_threshold_percent": 80,
  "hard_stop_threshold_percent": 95,
  "last_compacted_at": null
}
```

Commands:

```text
/上下文
/context
/压缩
/compact
/整理上下文
```

Rules:

- If native context usage exists, use it.
- If native context usage is unavailable, estimate and mark `estimated`.
- At auto threshold, post phone warning.
- At hard threshold, reject long tasks until compact or new conversation.
- Manual compaction MUST return old conversation id, new conversation id if created, summary artifact path, before/after estimate, and status.
- Native compaction MAY be used if verified.
- Otherwise runner MUST emulate compaction with summary artifact plus new conversation.

## 11. Phone Status Events

Status events MUST be token-free when possible.

Runner MUST emit status from orchestration state, subprocess lifecycle, file operations, command execution, and provider stdout/stderr.

Event schema:

```json
{
  "event_id": "uuid",
  "time": "ISO-8601",
  "run_id": "uuid",
  "provider": "claude-code|codex|other",
  "conversation_id": "string",
  "phase": "queued|thinking|planning|calling_model|reading_files|writing_files|running_command|waiting_review|compacting|sending_output|done|error|cancelled",
  "public_message_zh": "正在读取文件",
  "public_message_en": "Reading files",
  "token_free": true,
  "context_used_percent": 71,
  "last_visible_action": "apply_patch outputs/...",
  "redaction_applied": true
}
```

Allowed visible messages:

```text
正在排队
正在调用 Claude Code
正在调用 Codex
正在读取文件
正在创建文件
正在写入文件
正在运行测试
正在等待审查 AI
正在压缩上下文
正在发送结果
```

Hidden chain-of-thought MUST NOT be displayed.

## 12. Command Normalization

Chinese aliases MUST be normalized before provider invocation.

Mapping MUST include:

```json
{
  "/状态": "status",
  "/status": "status",
  "/压缩": "compact_context",
  "/compact": "compact_context",
  "/新对话": "new_conversation",
  "/new": "new_conversation",
  "/继续": "continue_conversation",
  "/continue": "continue_conversation",
  "/每次新对话": "set_policy_new_each_request",
  "/持续对话": "set_policy_continue",
  "/上下文": "context_status",
  "/帮助": "command_index",
  "/命令": "command_index",
  "/功能": "feature_index",
  "/索引": "command_index",
  "/凭据": "credential",
  "/全局": "global_instructions",
  "/项目": "project_instructions",
  "/说明": "description_metadata"
}
```

Chinese slash commands MUST NOT be passed directly to Claude Code/Codex unless adapter explicitly supports them.

## 13. Credential Broker

The credential broker MUST allow mobile-side secret input and handle-based execution.

Supported secret types:

```text
ssh_host
ssh_password
ssh_private_key
ssh_public_key
api_token
api_key
bearer_token
basic_auth
vps_login
mattermost_token
matrix_token
custom
```

Commands:

```text
/凭据 添加
/凭据 列表
/凭据 测试 <handle>
/凭据 删除 <handle>
/凭据 授权 <handle> <agent> <action> <duration>
```

Record schema:

```json
{
  "handle": "ssh://vps-us-prod",
  "type": "ssh_private_key",
  "host": "203.0.113.10",
  "port": 22,
  "username": "deploy",
  "scope": ["ssh.exec", "ssh.copy"],
  "allowed_agents": ["claude-code", "codex"],
  "allowed_actions": ["ssh.exec"],
  "expires_at": null,
  "storage": "secret-service|pass|age-file|vault|local-encrypted-file",
  "secret_material": "never returned"
}
```

Storage backends:

- `secret-service`: D-Bus Secret Service via `python-keyring` or `libsecret`.
- `pass`: Linux password store using GPG.
- `age-file`: encrypted file using age recipient generated at install.
- `vault`: optional external vault.
- `local-encrypted-file`: fallback; key root-readable only.

Rules:

- AI agents receive handle and metadata only.
- Plaintext MUST NOT appear in AI prompts.
- Plaintext MUST NOT appear in channel history.
- Plaintext MUST NOT appear in logs.
- For SSH private key execution, broker writes temporary `0600` key file, uses it, then deletes it.
- For SSH password execution, `sshpass` MAY be used only if explicitly enabled.
- API tokens are injected only into exact subprocess environment via `env -i`.

Action request:

```json
{
  "action": "ssh.exec",
  "credential_handle": "ssh://vps-us-prod",
  "command": "docker ps",
  "requires_confirmation": true
}
```

## 14. Minimum Safety

Privacy/stealth is not a goal.

Minimum safety MUST cover only:

- secrets not in GitHub;
- secrets not in AI prompts;
- secrets not in ordinary logs/channel history;
- remote shell disabled by default;
- credential handle authorization;
- budget freeze;
- recoverable install/update/rollback.

## 15. Budget Ledger

Service-level budget state:

```json
{
  "daily_usd_limit": 10,
  "monthly_usd_limit": 100,
  "daily_used_usd_estimate": 0,
  "monthly_used_usd_estimate": 0,
  "freeze_on_exceed": true
}
```

If exceeded:

- reject new model calls;
- allow `/状态`, `/上下文`, `/预算`, `/压缩`, `/新对话`;
- post phone alert;
- require admin reset.

## 16. Third-Party API Validation

Anthropic-compatible gateway validation MUST test:

- `/v1/messages`;
- `/v1/messages/count_tokens` if available;
- required headers;
- selected auth mode;
- proxy path if configured.

Proxy rules:

- If `CLAUDE_HTTP_PROXY` or `CLAUDE_HTTPS_PROXY` exists, validate through proxy.
- If proxy absent, validate direct.
- If both are explicitly requested, record which path passed.
- Communication platform proxy MUST NOT be reused for provider validation unless configured.

Expected `/v1/messages` minimum response:

```json
{
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "OK"}],
  "usage": {}
}
```

If `count_tokens` fails:

- mark compatibility `partial`;
- require explicit confirmation;
- require real Claude Code smoke test before automation.

## 17. Acceptance Tests

Core-ready requires:

- `/状态` works from phone.
- `/` returns Chinese index.
- `/压缩` works natively or emulated.
- `/新对话` starts fresh conversation.
- `/每次新对话` changes policy.
- `/上下文` shows native/estimated/unknown context state.
- `/全局 设置` changes `global.md` after confirmation.
- `/项目 追加` changes `project.md`.
- `/凭据 添加` creates handle.
- credential handle can run approved SSH test.
- AI prompt never receives secret plaintext.
- Claude Code adapter uses `--tools`, not `--allowedTools`, for restriction.
- Codex adapter reports unsupported native features.
- optional unresolved tools do not block core-ready.

