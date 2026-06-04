# Communication Platform Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Core Decision

Primary platform: Mattermost.

Fallback platform: Matrix/Synapse.

Secondary alternatives: Zulip, Rocket.Chat.

Telegram MUST NOT be used.

Rationale:

- Mattermost best matches mobile command UX, internal channels, bot/webhook/API support, slash-command-style control, and reproducible VPS deployment.
- Matrix/Synapse is fallback for open protocol, bridges, and multi-client ecosystem.
- Zulip is reserved for topic-threaded AI review discussions.
- Rocket.Chat is reserved for future integration-specific preference.

Example weighted score:

```text
Mattermost: 88
Matrix/Synapse: 80
Zulip: 74
Rocket.Chat: 70
```

## 2. Platform Selection Criteria

Weights:

```text
self-hosted maturity: 20
mobile UX and notifications: 15
bot/API/webhook support: 20
command UX: 10
thread/group collaboration: 10
VPS operations simplicity: 10
basic admin controls: 5
GitHub-reproducible docs: 10
```

The implementation MUST use Mattermost unless a future review produces a P1 blocker.

## 3. VPS Baseline

Minimum:

```text
2 vCPU
4 GB RAM
40 GB disk
Ubuntu 24.04 LTS or Debian 12
public IPv4
domain name
TLS
Docker Engine
Docker Compose plugin
```

Network:

```text
443/tcp public
80/tcp public for ACME redirect only
SSH key-only preferred
database not public
```

Core ops:

- daily database backup;
- daily upload/file backup;
- 7 daily + 4 weekly retention;
- monthly restore test;
- disk usage monitoring;
- TLS expiry monitoring;
- bridge heartbeat monitoring.

## 4. Mattermost Target

Mattermost source:

```text
https://github.com/mattermost/docker
```

Implementation MUST pin release tag or commit in project lock file before public release.

Mattermost setup MUST create:

```text
team: ai-lab
channels:
  town-square
  ai-ops
  ai-status
  ai-reviews
  ai-errors
  ai-archive
```

Accounts:

```text
human-owner
ai-bridge
master-writer-ai
claude-code-ai
codex-ai
reviewer-ai-1
reviewer-ai-2
optional-specialist-ai
```

Mattermost command strategy:

- platform slash trigger: `/ai`;
- Chinese subcommands parsed by bridge;
- normal messages beginning with Chinese slash aliases MAY also be parsed.

Required examples:

```text
/ai 状态
/ai 压缩
/ai 新对话
/ai 每次新对话
/ai 持续对话
/ai 继续
/ai 上下文
/ai 凭据 添加
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
/ai 扩展 列表
/ai 工具 列表
/ai mcp 列表
/ai 帮助
/ai 功能
/ai 说明 生成 <id>
```

## 5. Matrix/Synapse Fallback

Matrix/Synapse MUST be used only if Mattermost is blocked or user overrides.

Matrix target:

- Synapse homeserver;
- PostgreSQL;
- reverse proxy with TLS;
- registration disabled by default;
- bot account or appservice bridge;
- rooms equivalent to Mattermost channels.

Rooms:

```text
#ai-ops
#ai-status
#ai-reviews
#ai-errors
#ai-archive
```

Matrix bridge MUST implement same canonical actions and Chinese aliases as Mattermost.

If mobile client lacks modal UI, credential/instruction workflows MUST use step-by-step DM flow.

## 6. Internal AI Group

The platform MUST support an internal group/channel model with:

- human user;
- master-writer AI;
- Claude Code AI;
- Codex AI;
- reviewer AI 1;
- reviewer AI 2;
- optional specialist AI.

Rules:

- each AI identity MUST be distinguishable;
- every run has `run_id`;
- every proposal/review has input hash and output hash;
- reviewers MUST NOT see each other's review until both complete;
- master-writer merges after both reviews complete;
- human can interrupt using Chinese commands.

## 7. Status Rendering

Status post MUST include:

```text
run id
provider
conversation id
workspace id
phase
current public action
context used percent
budget used
last output chunk
error class
artifact links
```

Preferred rendering:

- one root post per run;
- updates as thread replies or edited root post;
- final output in same thread;
- errors mirrored to `ai-errors`;
- summaries mirrored to `ai-archive`.

Status events MUST be copied from runner events without extra model calls when possible.

Hidden chain-of-thought MUST NOT be rendered.

## 8. Mobile Command UX

The phone UX MUST support:

- Chinese commands;
- English commands;
- command index;
- provider feature index;
- approval confirmations;
- credential capture;
- instruction file editing;
- context display;
- compaction;
- new conversation;
- per-request new conversation;
- optional extension/tool index.

`/` behavior:

- `/` alone MUST return categorized Chinese index.
- `/ ` MUST behave as `/`.
- known command MUST execute canonical mapping.
- unknown command MUST return `未知命令，输入 / 查看索引`.
- fuzzy search MAY match command aliases and Chinese descriptions.

Index MUST show:

```text
Chinese alias
Chinese description
English canonical action
enabled/disabled
requires confirmation
native/emulated/unsupported
short usage
installed source/version when relevant
```

## 9. Credential Capture UX

Credential capture MUST support:

```text
SSH host/IP
SSH username
SSH password
SSH private key
VPS label
API key/token
provider endpoint URL
expiration
allowed agents
allowed actions
```

Accepted flows:

- Mattermost modal/form if available;
- DM to bridge bot followed by immediate broker storage and redaction;
- one-time bridge upload URL;
- local CLI fallback.

Channel output after capture MUST use handle only:

```text
credential_created handle=ssh://vps-us-prod type=ssh_private_key host=203.0.113.10 username=deploy allowed_agents=claude-code,codex
```

Channel output MUST NOT contain:

- private key body;
- password;
- bearer token;
- full API key.

Commands:

```text
/凭据 添加
/凭据 列表
/凭据 测试 <handle>
/凭据 删除 <handle>
```

The platform collects input. The AI runner credential broker stores, decrypts, and executes.

## 10. Instruction File UX

Commands:

```text
/全局 查看
/全局 设置
/全局 追加
/全局 替换
/全局 回滚 <snapshot>
/项目 查看
/项目 设置
/项目 追加
/项目 替换
/项目 回滚 <snapshot>
```

Rules:

- show returns hash + preview;
- set/replace requires confirmation;
- append records snapshot;
- rollback requires confirmation;
- Matrix fallback MUST implement same flow through messages if modals unavailable.

## 11. Command Description UX

Commands:

```text
/说明
/说明 生成 <id>
/说明 编辑 <id>
```

Rules:

- every command/skill/MCP/CLI/provider feature SHOULD have `description_zh`;
- new skill/CLI/MCP install MUST register `description_zh`;
- only metadata is generated;
- installed tool implementation MUST NOT be modified;
- if AI-generated, mark `description_source=generated_ai`.

## 12. Minimum Safety

Privacy/stealth is not a goal.

Minimum safety only:

- admin signup disabled;
- bridge request authentication;
- platform bot token not exposed to AI prompts;
- channel history does not contain long-lived plaintext secrets;
- backups not accidentally public;
- basic admin 2FA SHOULD be enabled when available.

## 13. Acceptance

Platform-ready requires:

- Mattermost installed or explicitly overridden.
- Matrix fallback documented.
- `/ai 状态` reaches runner.
- `/ai 压缩` reaches runner.
- `/ai 新对话` reaches runner.
- `/ai 每次新对话` reaches runner.
- `/ai 全局 查看` reaches runner.
- `/ai 项目 追加` reaches runner.
- `/ai 凭据 添加` starts credential flow.
- `/` index shows Chinese descriptions.
- AI group identities exist.
- status updates appear on phone.
- final output appears on phone.

## 14. Source Anchors

- Mattermost Docker install: https://docs.mattermost.com/deployment-guide/server/containers/install-docker.html
- Mattermost container deployment: https://docs.mattermost.com/deployment-guide/server/deploy-containers.html
- Mattermost slash commands: https://docs.mattermost.com/integrations-guide/slash-commands.html
- Mattermost Docker repository: https://github.com/mattermost/docker
- Matrix Synapse installation: https://github.com/matrix-org/synapse/blob/develop/docs/setup/installation.md

