# AI Remote Runner Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Core Requirements

The runner MUST provide the core mobile-controlled AI execution layer.

Core install MUST NOT depend on optional skills, MCP extensions, or CLI tools.

Telegram is a selectable first-class runner-side phone channel, equal to Mattermost for command and status behavior. It MUST be disabled until paired with a BotFather token and explicit Telegram ID. A deployment MAY select Telegram, Mattermost, or both.

The runner MUST support:

- Claude Code adapter;
- Codex adapter;
- future AI adapters;
- Mattermost/Matrix communication bridge;
- selectable first-class Telegram bot service;
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

## 3. Core Runner Installer Scope

The core runner installer MUST install or verify every explicitly requested runtime component required for phone-controlled Claude Code/Codex/VSCode operation. Full Debian 12 deployments may pass `AI_RUNNER_COMPONENTS=all,telegram` to install Claude Code, Codex, VSCode, the runner, and Telegram on one VM. Focused deployments may still pass `codex,telegram`, `claude-code,telegram`, or `vscode,telegram`. When multiple providers are installed, the runner MUST keep one active default provider at a time and switch explicitly with `/ai 提供商 使用 <provider>`.

Core runner installer MUST execute these stages:

```text
stage 01: detect OS, WSL, architecture, systemd availability, shell, PATH
stage 01b: remove stale provider configs for unrequested AI tools
stage 02: install system packages required by runner
stage 03: install or verify requested Claude Code provider
stage 04: install or verify requested Codex CLI provider
stage 05: install or verify requested VSCode root/full-access tool wrapper
stage 06: create runner directories
stage 07: create runner configuration files
stage 08: create credential broker storage backend
stage 09: install runner bridge service
stage 10: connect runner to communication platform
stage 11: run provider CLI/auth/full-access-flag preflight
stage 12: run phone command smoke tests
stage 13: report core_ready or failed
```

Claude Code installation requirement:

- installer MUST install Claude Code if `claude-code` is requested and `claude` is missing;
- installer MUST verify `claude --version`;
- installer MUST verify `claude auth status --json` command exists;
- installer MUST verify `claude -p --output-format json` works after authentication/API configuration;
- installer MUST use official Claude Code installation source pinned or referenced in release lock file.
- Debian/Ubuntu installer MUST prefer official Claude Code signed apt/native installer from Claude Code installation docs.
- npm fallback MAY be used only when official installer is unavailable.
- Codex CLI MAY be installed globally with `sudo npm install -g` when this is the resolved install source.

Codex installation requirement:

- installer MUST install Codex CLI globally if `codex` is requested and missing;
- installer MUST verify `codex --version`;
- installer MUST verify `codex exec` exists;
- installer MUST verify remote adapter can call Codex non-interactively with full VM access;
- if Codex cannot be installed or cannot expose full VM access, core install MUST fail loudly instead of marking Codex as an optional external prerequisite.

VSCode installation requirement:

- installer MUST install or verify Visual Studio Code with `code --version` when `vscode` is requested;
- Debian/Ubuntu installer SHOULD use Microsoft apt repository so fresh installs receive the current stable `code` package;
- installer MUST create a root wrapper named `code-root` by default at `/usr/local/bin/code-root`;
- `code-root` MUST re-exec through sudo when needed and run VSCode with root user-data/extensions directories, `--no-sandbox`, and `--disable-workspace-trust`;
- VSCode failure MUST be reported in the install manifest and MUST block the full-access workstation setup.

Runner service installation requirement:

- Linux/systemd target MUST install `ai-remote-runner.service`.
- Linux/systemd target MUST run runner and optional Telegram services as `root` by default.
- WSL without systemd MUST generate `run-local.sh`.
- Service MUST load runner config with `HOME=/root` and `CODEX_HOME=/root/.codex` unless explicitly overridden for a non-root lab setup.
- Service MUST expose local health endpoint or health command for communication bridge.

Bridge connection requirement:

- installer MUST accept communication platform endpoint, bot token, team/room/channel identifiers, and bridge shared secret;
- bridge shared secret input MUST use a root-readable file, stdin, or a brokered secure channel; installer and pairing commands MUST NOT accept raw bridge-secret argv values;
- installer MUST test posting status event to communication platform;
- installer MUST test receiving a command from communication platform or run an equivalent loopback test.
- default lab topology MAY use direct `/bridge/command` through same-host networking, VPN, or a reverse SSH tunnel.
- runner-initiated outbound WebSocket or runner-initiated long-poll queue remains the preferred no-inbound topology when no VPN/reverse tunnel exists.
- VPS MUST NOT require unprotected inbound access to a home/WSL runner; reverse SSH tunnel, VPN, same-host deployment, or runner-initiated polling are acceptable protected paths.

Bridge protocol contract:

```http
POST /bridge/command        # direct/VPN mode only
POST /bridge/event          # runner-to-VPS event post
POST /bridge/credential-upload-url
PUT  /bridge/credential-upload/{token}
GET  /bridge/health
GET  /bridge/poll           # runner-initiated polling mode
WS   /bridge/ws             # preferred runner-initiated persistent mode
```

All bridge requests MUST include:

```text
X-AI-Bridge-Timestamp
X-AI-Bridge-Nonce
X-AI-Bridge-Signature
```

Signature:

```text
shared_secret is stored base64url-encoded.
HMAC key is base64url-decode(shared_secret).
signature = base64url(HMAC-SHA256(key, timestamp + "\n" + nonce + "\n" + raw_body_bytes))
```

Signature validation:

- timestamp skew max: 300 seconds;
- nonce TTL: 600 seconds;
- nonce store MUST reject replay;
- `raw_body_bytes` MUST be exact UTF-8 request body bytes before JSON parsing.

Command envelope:

```json
{
  "request_id": "uuid",
  "platform": "mattermost|matrix",
  "room_id": "string",
  "thread_id": "string|null",
  "sender_id": "string",
  "raw_text": "/ai 压缩",
  "canonical_action": "compact_context",
  "args": {},
  "requires_confirmation": false,
  "created_at": "ISO-8601"
}
```

Response envelope:

```json
{
  "request_id": "uuid",
  "status": "accepted|rejected|needs_confirmation|error",
  "run_id": "uuid|null",
  "message_zh": "string",
  "error": {
    "code": "string",
    "detail": "string"
  }
}
```

Bridge MUST be idempotent by `request_id`.

Bridge retry policy MUST use exponential backoff and MUST NOT duplicate accepted commands.

Core smoke tests:

```text
claude_version
claude_auth_or_api_config
claude_print_json
claude_full_access_provider_smoke
codex_version
codex_exec_full_access
codex_full_access_provider_smoke
communication_bridge_post
phone_status_command
phone_slash_index
credential_handle_create
global_md_show
project_md_append
context_status
```

`core_ready` requires every enabled provider on this machine to pass real full-access smoke tests, plus communication bridge readiness, phone command readiness, credential broker readiness, and instruction file readiness. `validate-core-ready.sh` MUST NOT write `core_ready=true` unless all enabled providers can complete their full-access smoke tests. A management-only machine with an empty provider list has no enabled AI provider and MUST validate runner commands plus bridge loopback without probing Claude Code, VSCode, or Codex.

`codex_ready=true` is required for `core_ready` only on machines where Codex is explicitly requested.

`full_ready` requires `core_ready=true`, `codex_ready=true`, and selected optional bundle items installed.

Codex MUST NOT be left as an external prerequisite in the current VM lab requirement. If Codex cannot be installed or cannot run with full VM access, the core install MUST fail loudly.

Rollback contract:

- installer MUST write an install manifest at `/var/lib/ai-remote-runner/install-manifest.json`;
- every stage MUST be idempotent;
- every stage MUST record pre-change state when it mutates system files;
- `rollback-install.sh` MUST restore systemd unit state, runner directory, bridge config, and generated config files;
- rollback MUST NOT delete workspaces or credential store unless explicit destructive confirmation is provided.

## 4. Provider Adapter Contract

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

## 5. Claude Code Adapter

Verified local Claude Code CLI facts:

- `claude -p` runs print mode.
- `--output-format json` returns single result JSON.
- `--output-format stream-json` streams events.
- `--max-budget-usd` limits per-call spend in print mode.
- `--max-turns` limits turns; the runner omits it by default and only adds it when `CLAUDE_MAX_TURNS` is a positive integer.
- `--permission-mode acceptEdits` is the root-compatible full-access permission mode under the current Claude Code CLI.
- `--allowedTools Bash(*)` is required with the full-access template so non-interactive root runs can execute arbitrary Bash commands/scripts without a per-command approval prompt.
- `--permission-mode bypassPermissions` can bypass tool permission prompts but current Claude Code treats it like dangerous skip mode under root/sudo and rejects it.
- `--dangerously-skip-permissions` bypasses all Claude Code permission checks but current Claude Code rejects this flag when run as root/sudo; root full-access runner commands MUST NOT include it by default.
- `--tools default` enables default built-in tools.
- `--add-dir /` allows the provider to access the full VM filesystem.
- `--continue` and `--resume` require session persistence and MUST NOT be used for independent reviewer runs.
- `--system-prompt` and `--append-system-prompt` are valid.
- `claude auth status --json` returns JSON containing `loggedIn`, `authMethod`, and `apiProvider`.

Default Claude mode for this project is `full_access` because the VM is the user-selected security boundary:

```bash
cd "$RUNNER_WORKSPACE"
claude -p \
  --output-format json \
  --add-dir / \
  --max-budget-usd "$CLAUDE_MAX_BUDGET_USD" \
  --permission-mode acceptEdits \
  --tools Bash,Read,Write,Edit,Grep,Glob \
  --allowedTools 'Bash(*)' \
  --append-system-prompt "$RUNNER_INSTRUCTION_PROMPT" \
  -- \
  "$PROMPT"
```

Optional downgrade modes MAY exist for debugging (`chat`, `edit`, `shell`), but installer default and phone-controlled task execution MUST be `full`. `shell` and `full` modes MUST NOT require per-task approval unless `AI_REQUIRE_SHELL_CONFIRMATION=1` is explicitly set for a special test environment. `edit` and `shell` modes MUST use Claude Code permissions that can actually perform the named capability; they MUST NOT be plan-only modes. Codex currently supports only full-access execution in this runner; if a non-full permission scope is active with Codex selected, the runner MUST return an explicit unsupported-scope error instead of silently running full access.

Command construction rules:

- `--tools` is variadic; command MUST terminate options with `--` before prompt or pass prompt through stdin.
- Provider subprocesses MUST inherit the root service environment so proxy, API, package-manager, and tool-chain settings are available.
- Reviewer runs MUST be fresh: no `--continue`, no `--resume`, no persisted previous review context.

## 6. Codex Adapter

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

Default Codex `exec` template MUST use full VM access. Prefer the CLI's explicit dangerous bypass flag when supported; otherwise fall back to `--sandbox danger-full-access`.

```bash
cd "$RUNNER_WORKSPACE"
codex exec \
  -c 'approval_policy="never"' \
  -c 'sandbox_workspace_write.network_access=true' \
  -c 'shell_environment_policy.inherit=all' \
  --json \
  --dangerously-bypass-approvals-and-sandbox \
  --dangerously-bypass-hook-trust \
  --ignore-rules \
  --add-dir / \
  --skip-git-repo-check \
  --cd "$RUNNER_WORKSPACE" \
  --output-last-message "$RUN_OUTPUT_FILE" \
  -- \
  "$PROMPT"
```

Codex config written by the installer MUST target root/global execution by default:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"

[shell_environment_policy]
inherit = "all"

[sandbox_workspace_write]
network_access = true
```

Codex adapter MUST wrap `codex exec` with service-level budget reservation, timeout, kill, and output-size caps because verified local `codex exec --help` does not expose a universal `--max-budget-usd`.

## 7. Optional Extension/Tool Bundle

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
- Installed optional items MUST appear in `/ai 功能`, `/ai 命令`, and `/ai 帮助`.
- Newly installed items MUST receive Chinese description metadata.

Phone commands:

```text
/ai 扩展 列表
/ai 工具 列表
/ai mcp 列表
```

## 8. Conversation Policy

Supported policies:

```text
continue
new_each_request
ask_each_request
```

Commands:

```text
/ai 新对话
/ai new
/ai 继续
/ai continue
/ai 每次新对话
/ai mode new_each
/ai 持续对话
/ai mode continue
```

Rules:

- `new_each_request` MUST create a fresh provider conversation for every user task.
- `continue` MUST reuse the selected conversation only when provider continuation is safe.
- If provider continuation is unavailable, runner MUST emulate continuation using compacted summary context.
- `/ai 状态` MUST show current policy, provider, workspace, and conversation id.
- Telegram task execution MUST show visible queued/calling/running status, use Telegram typing chat action when possible, and keep sending heartbeat messages while the provider is active so phone users can distinguish thinking/running/network wait from a disconnected bot.
- Telegram pairing MUST trigger the same `validate-core-ready.sh` full-access provider smoke path as Mattermost pairing. Telegram-primary deployments MUST NOT remain permanently `core_ready=false` after successful pairing unless a real provider/full-access smoke fails.

## 9. Instruction Files

Canonical files:

```text
/srv/ai-remote/instructions/global.md
/srv/ai-workspaces/<workspace_id>/project.md
```

Phone commands:

```text
/ai 全局 查看
/ai 全局 设置
/ai 全局 追加
/ai 全局 替换
/ai 全局 回滚 <snapshot>
/ai 项目 查看
/ai 项目 设置
/ai 项目 追加
/ai 项目 替换
/ai 项目 回滚 <snapshot>
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

## 10. Command And Feature Index

Commands:

```text
/ai
/ai 帮助
/ai 确认 <token>
/ai 命令
/ai 功能
/ai 索引
/ai 说明
/ai 说明 生成 <id>
/ai 说明 编辑 <id>
```

`/ai 帮助`, `/ai 命令`, and `/ai 索引` MUST show the Chinese command index. `/ai 说明` MUST show description metadata for the implemented command table. `/ai 说明 编辑 <id>` MUST at minimum record a metadata edit request and MUST NOT modify tool code without a separate confirmed implementation path.

Bare `/` MAY show the same index only when the selected communication platform can deliver normal-message shortcuts to the bridge.

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
- `/ai 说明 生成 <id>` MAY generate metadata from README/help/manifest.
- If AI generation is used, mark `description_source=generated_ai`.

## 11. Context Telemetry And Compaction

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
/ai 上下文
/ai context
/ai 压缩
/ai compact
/ai 整理上下文
/ai 自动压缩 开启
/ai 自动压缩 关闭
/ai 聊天模式 开启
/ai 编辑模式 开启
/ai shell模式 开启
```

Rules:

- If native context usage exists, use it.
- If native context usage is unavailable, estimate as `ceil((utf8_bytes(messages + injected_instructions + attached_text_artifacts) / 4) * 1.20)` and mark `estimated`.
- At auto threshold, post phone warning.
- If `auto_compact_enabled=true`, runner MUST compact before accepting the next long task after threshold.
- Runner MAY compact inline before starting the current provider call when the threshold is already exceeded at request acceptance time.
- If conversation policy is `new_each_request`, runner MUST NOT auto-compact; it MUST create a fresh provider conversation for each task instead.
- If a task is running when threshold is crossed, runner MUST finish the current provider call, compact, then continue only if conversation policy permits.
- If compaction requires approval, runner MUST enter `compaction_pending_approval` and reject new long tasks until approved or skipped.
- At hard threshold, reject long tasks until compact or new conversation.
- Manual compaction MUST return old conversation id, new conversation id if created, summary artifact path, before/after estimate, and status.
- Native compaction MAY be used if verified.
- Otherwise runner MUST emulate compaction with summary artifact plus new conversation.

## 12. Phone Status Events

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

Minimum redaction rules:

- Redact API keys, bearer tokens, private key blocks, passwords, and credential values.
- Redaction applies before communication platform posting and before runner-visible logs.
- General chat-content privacy is out of scope.

## 13. Command Normalization

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

Compound command grammar:

```text
/ai <verb> [object] [args...]
```

Required compound mappings:

```json
{
  "/ai 状态": {"canonical_action": "status"},
  "/ai 压缩": {"canonical_action": "compact_context"},
  "/ai 新对话": {"canonical_action": "new_conversation"},
  "/ai 每次新对话": {"canonical_action": "set_policy_new_each_request"},
  "/ai 持续对话": {"canonical_action": "set_policy_continue"},
  "/ai 继续": {"canonical_action": "continue_conversation"},
  "/ai 帮助": {"canonical_action": "command_index"},
  "/ai 确认": {"canonical_action": "confirm"},
  "/ai 功能": {"canonical_action": "feature_index"},
  "/ai 上下文": {"canonical_action": "context_status"},
  "/ai 预算": {"canonical_action": "budget_status"},
  "/ai 停止": {"canonical_action": "cancel"},
  "/ai 取消": {"canonical_action": "cancel"},
  "/ai 全局 查看": {"canonical_action": "global_instructions.show"},
  "/ai 全局 设置": {"canonical_action": "global_instructions.set", "requires_confirmation": true},
  "/ai 全局 追加": {"canonical_action": "global_instructions.append"},
  "/ai 全局 替换": {"canonical_action": "global_instructions.set", "requires_confirmation": true},
  "/ai 全局 回滚": {"canonical_action": "global_instructions.rollback", "requires_confirmation": true},
  "/ai 全局 应用": {"canonical_action": "global_instructions.apply"},
  "/ai 项目 查看": {"canonical_action": "project_instructions.show"},
  "/ai 项目 设置": {"canonical_action": "project_instructions.set", "requires_confirmation": true},
  "/ai 项目 追加": {"canonical_action": "project_instructions.append"},
  "/ai 项目 替换": {"canonical_action": "project_instructions.set", "requires_confirmation": true},
  "/ai 项目 回滚": {"canonical_action": "project_instructions.rollback", "requires_confirmation": true},
  "/ai 项目 应用": {"canonical_action": "project_instructions.apply"},
  "/ai 凭据 添加": {"canonical_action": "credential.add"},
  "/ai 凭据 列表": {"canonical_action": "credential.list"},
  "/ai 凭据 测试": {"canonical_action": "credential.test"},
  "/ai 工作区 列表": {"canonical_action": "workspace.list"},
  "/ai 工作区 使用": {"canonical_action": "workspace.select"},
  "/ai 工作区 创建": {"canonical_action": "workspace.create", "requires_confirmation": true},
  "/ai 提供商 列表": {"canonical_action": "provider.list"},
  "/ai GPT模型 设置": {"canonical_action": "model.select_gpt"},
  "/ai Claude模型 设置": {"canonical_action": "model.select_claude"},
  "/ai 模型 使用": {"canonical_action": "model.select"},
  "/ai 提供商 使用": {"canonical_action": "provider.select"},
  "/ai 自动压缩 开启": {"canonical_action": "set_auto_compact_enabled"},
  "/ai 自动压缩 关闭": {"canonical_action": "set_auto_compact_disabled"},
  "/ai 聊天模式 开启": {"canonical_action": "set_permission_chat"},
  "/ai 编辑模式 开启": {"canonical_action": "set_permission_edit"},
  "/ai shell模式 开启": {"canonical_action": "set_permission_shell"},
  "/ai 完全访问 开启": {"canonical_action": "set_permission_full"},
  "/ai 最高权限 开启": {"canonical_action": "set_permission_full"},
  "/ai root权限 开启": {"canonical_action": "set_permission_full"},
  "/ai 扩展 列表": {"canonical_action": "extension.list"},
  "/ai 工具 列表": {"canonical_action": "tool.list"},
  "/ai mcp 列表": {"canonical_action": "mcp.list"}
}
```

Mattermost guaranteed trigger is `/ai`. Bare Chinese aliases MAY be parsed only as optional normal-message shortcuts.

## 14. Credential Broker

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
/ai 凭据 添加
/ai 凭据 列表
/ai 凭据 测试 <handle>
/ai 凭据 删除 <handle>
/ai 凭据 授权 <handle> <agent> <action> <duration>
```

`/ai 凭据 授权` updates non-secret credential metadata (`allowed_agents`, `allowed_actions`, optional expiry) and MUST NOT reveal plaintext secret material.

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
- SSH password execution MUST NOT pass password via process arguments.
- SSH password credentials MUST authorize `ssh.exec.password`; generic `ssh.exec` authorization alone MUST NOT permit password-based SSH.
- Password-based SSH MAY use broker-controlled `sshpass -e` or stdin/pty helper only after explicit approval; the password MUST be supplied through broker-controlled environment or stdin, never through argv or chat.
- API tokens MAY be injected into the root service environment or exact subprocess environment according to provider requirements; they MUST NOT be committed to GitHub or written into AI prompts.

Action request:

```json
{
  "action": "ssh.exec",
  "credential_handle": "ssh://vps-us-prod",
  "command": "docker ps",
  "requires_confirmation": true
}
```

Remote exec approval preview MUST include:

```text
credential_handle
target_host
username
command
working_directory
timeout_seconds
one_time_approval_token
```

## 15. Minimum Safety

Privacy/stealth is not a goal.

Minimum safety MUST cover only:

- secrets not in GitHub;
- secrets not in AI prompts;
- secrets not in ordinary logs/channel history;
- remote shell enabled by default inside the VM for Claude Code and Codex full-access tasks;
- credential handle authorization for externally supplied secrets;
- budget freeze;
- recoverable install/update/rollback.

## 16. Budget Ledger

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

Run reservation schema:

```json
{
  "run_id": "uuid",
  "provider": "claude-code|codex|other",
  "reserved_usd": 1.0,
  "actual_usd": null,
  "timeout_seconds": 1800,
  "max_output_bytes": 200000,
  "status": "reserved|running|completed|failed|killed"
}
```

Budget enforcement:

- runner MUST reserve estimated cost before starting provider process.
- runner MUST enforce max concurrent provider runs. Default: 1.
- runner MUST enforce timeout and kill provider subprocess on timeout.
- runner MUST enforce output byte cap.
- runner MUST update ledger after completion or failure.
- if actual cost is unavailable, runner MUST commit conservative estimate.
- Codex and non-Claude providers MUST have per-run reservation caps because they may lack native budget flags. If a provider lacks a native money cap, the runner MUST enforce the reservation by preflight budget checks, timeout, output byte cap, and process termination on timeout.

If exceeded:

- reject new model calls;
- allow `/ai 状态`, `/ai 上下文`, `/ai 预算`, `/ai 压缩`, `/ai 新对话`;
- post phone alert;
- require admin reset.

Budget preflight:

- runner MUST check daily/monthly budget before every provider call;
- if budget is exhausted, provider process MUST NOT start;
- per-call provider budget flags do not replace service-level budget preflight.

## 17. Third-Party API Validation

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

## 18. Acceptance Tests

Core-ready requires:

- `/ai 状态` works from phone.
- `/ai 帮助` returns Chinese index.
- Telegram, when enabled and paired, shows queued/calling/running heartbeat status during long AI tasks.
- `/ai 压缩` works natively or emulated.
- `/ai 新对话` starts fresh conversation.
- `/ai 每次新对话` changes policy.
- `/ai 上下文` shows native/estimated/unknown context state.
- `/ai 全局 设置` changes `global.md` after confirmation.
- `/ai 项目 追加` changes `project.md`.
- `/ai 凭据 添加` creates handle.
- credential handle can run approved SSH test.
- AI prompt never receives secret plaintext.
- Claude Code adapter uses `--tools` to declare available built-in tools and `--allowedTools Bash(*)` to auto-allow full-access Bash execution in the dedicated VM.
- Claude Code and Codex run in root/full VM access by default.
- VSCode `code-root` exists and starts VSCode with root/full-access settings.
- Codex adapter reports unsupported native features.
- optional unresolved tools do not block core-ready.
