# Communication Platform Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Core Decision

Primary platform: Mattermost.

Fallback platform: Matrix/Synapse.

Optional direct bot channel: Telegram.

Telegram MAY be enabled as an optional runner-side bot after core install. It MUST NOT replace the default Mattermost communication platform, MUST NOT be required for core-ready, and MUST require explicit BotFather token plus Telegram ID pairing before executing AI commands.

Rationale:

- Mattermost best matches mobile command UX, internal channels, bot/webhook/API support, slash-command-style control, and reproducible VPS deployment.
- Matrix/Synapse is fallback for open protocol, bridges, and multi-client ecosystem.
- Telegram is useful as a lightweight optional direct mobile channel when the user supplies a bot token and Telegram ID.

Example weighted score:

```text
Mattermost: 88
Matrix/Synapse: 80
Zulip: 74
Rocket.Chat: 70
Telegram direct bot: optional
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

The runner implementation MAY additionally support Telegram long polling as an optional service installed by `scripts/install-runner.sh --enable-telegram` and paired by `scripts/pair-telegram.sh`.

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

## 4. VPS Communication Server Installer Scope

The VPS communication installer MUST install, configure, and validate the self-hosted communication server.

Default target MUST be Mattermost.

Installer stages:

```text
stage 01: detect VPS OS, CPU, memory, disk, public IP
stage 02: install Docker Engine and Docker Compose plugin
stage 03: configure domain and TLS
stage 04: clone or vendor Mattermost Docker deployment
stage 05: pin Mattermost release tag or commit
stage 06: configure Mattermost environment
stage 07: start Mattermost stack
stage 08: create team and channels
stage 09: create bot identities
stage 10: configure slash-command or command bridge
stage 11: configure incoming status webhook or bot token
stage 12: configure bridge shared secret
stage 13: connect VPS communication platform to AI remote runner
stage 14: run phone command smoke tests
stage 15: run backup smoke test
```

Stages 08-11 MAY delegate Mattermost team, channel, bot, slash-command, and webhook creation to `scripts/bootstrap-mattermost.sh`; the parent installer MUST still validate the resulting objects before setting `platform_ready=true`.

Fresh Mattermost bootstrap MUST obtain an admin personal access token through initial web login or an equivalent `mmctl --local` admin bootstrap before REST slash-command and webhook creation. `scripts/mattermost-first-admin.sh` creates or confirms the first system admin, then the operator MUST create a personal access token and export `MATTERMOST_ADMIN_TOKEN`. Installer MUST fail loudly, not mark platform ready, when `MATTERMOST_ADMIN_TOKEN` is absent.

Bridge shared secret:

```text
format: base64url
entropy: >= 256 bits
generation: cryptographically secure random generator
storage: VPS bridge env + local runner env
transport: transferred through SSH, credential broker, or another encrypted channel; MUST NOT be printed in chat or logs
rotation: supported by pairing command
```

Pairing commands MUST accept bridge shared secrets only through a protected file, stdin, or brokered secure transfer. They MUST NOT accept raw bridge-secret argv values because command-line arguments can be captured by shell history or process listings.

Mattermost server install MUST produce:

```text
Mattermost URL
admin account bootstrap state
team ai-lab
channels ai-ops, ai-status, ai-reviews, ai-errors, ai-archive
bot identity ai-bridge
bot identities for Claude Code, Codex, master-writer, reviewers
command endpoint /ai
status posting endpoint
bridge shared secret
backup path
restore instructions
```

Communication bridge integration MUST validate:

```text
/ai 状态
/ai 帮助
/ai 新对话
/ai 压缩
/ai 上下文
/ai 自动压缩 开启
/ai 继续
/ai 每次新对话
/ai 全局 查看
/ai 全局 替换
/ai 项目 追加
/ai 项目 替换
/ai 凭据 添加
/ai 工作区 使用
/ai 提供商 使用
/ai 扩展 列表
```

This smoke list MUST match AI runner bridge smoke tests.

If Mattermost install fails with a P1 blocker, installer MAY switch to Matrix/Synapse fallback only after recording reason.

## 5. Mattermost Target

Mattermost source:

```text
https://github.com/mattermost/docker
```

Implementation MUST keep database/Caddy image refs pinned and MUST select a Mattermost version compatible with current mobile clients.

Mattermost release pinning:

```text
versions.lock MUST contain mattermost_image_repository, mattermost_version, mattermost_min_version, mattermost_db_image, mattermost_caddy_image, and mattermost_docker_ref.
mattermost_version SHOULD default to `latest`; installer resolves it from the official Mattermost GitHub latest release.
mattermost_min_version MUST be 10.11.0 or newer so mobile clients do not reject the server.
mattermost_db_image and mattermost_caddy_image MUST be explicit digest-pinned image refs, not `latest`.
mattermost_docker_ref MUST be a commit or release reference from https://github.com/mattermost/docker.
Installer MUST fail if database/Caddy pins are absent or the resolved Mattermost version is below mattermost_min_version.
```

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

Mattermost bootstrap method:

- installer SHOULD use `mmctl --local` inside Mattermost container when available;
- installer MAY use Mattermost REST API when `mmctl --local` is unavailable;
- installer MUST be idempotent: existing team/channel/bot/slash-command objects are reused and corrected, not duplicated;
- installer MUST create slash command trigger `/ai`;
- installer MUST create incoming status webhook or bot token for status events;
- installer MUST record created object IDs in install manifest.

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

Bot authentication:

- each bot identity MUST have a distinct platform token or webhook identity;
- tokens MUST NOT be shared across Claude Code AI, Codex AI, reviewers, and bridge;
- bridge MAY post on behalf of identities only through explicit identity mapping;
- identity mapping MUST be stored in bridge config.

Mattermost command strategy:

- platform slash trigger: `/ai`;
- Chinese subcommands parsed by bridge;
- `/ai` is the only guaranteed Mattermost trigger;
- normal messages beginning with Chinese slash aliases MAY be parsed only as optional shortcuts.

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
/ai 上下文
/ai 继续
/ai 全局 替换
/ai 全局 应用
/ai 项目 替换
/ai 项目 应用
/ai 预算
/ai 停止
/ai 取消
/ai 工作区 列表
/ai 工作区 使用
/ai 提供商 列表
/ai 提供商 使用
/ai 聊天模式 开启
/ai 编辑模式 开启
/ai shell模式 开启
```

## 6. Matrix/Synapse Fallback

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

If mobile client lacks modal UI, non-secret credential metadata and instruction workflows MAY use step-by-step DM flow. Secret values MUST use one-time bridge upload URL or local CLI.

## 7. Internal AI Group

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

## 8. Status Rendering

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

## 9. Mobile Command UX

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

- In Mattermost, `/ai` and `/ai 帮助` MUST return categorized Chinese index.
- Bare `/` and `/ ` MAY return index only if the platform client/bridge can observe normal messages.
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

## 10. Credential Capture UX

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
- one-time bridge upload URL;
- local CLI fallback.

DM plaintext credential capture MUST NOT be used.

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
/ai 凭据 添加
/ai 凭据 列表
/ai 凭据 测试 <handle>
/ai 凭据 删除 <handle>
```

The platform collects input through modal, one-time upload URL, or local CLI fallback. `/bridge/credential-upload-url` MUST mint a short-lived token; `/bridge/credential-upload/{token}` MUST accept the secret once and return only public credential metadata. The AI runner credential broker stores, decrypts, and executes.

General chat privacy is out of scope. Credential plaintext in ordinary chat history is still prohibited.

## 11. Instruction File UX

Commands:

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

- show returns hash + preview;
- set/replace requires confirmation;
- append records snapshot;
- rollback requires confirmation;
- Matrix fallback MAY implement non-secret instruction flow through messages if modals unavailable. Secret values MUST use one-time bridge upload URL or local CLI.

## 12. Command Description UX

Commands:

```text
/ai 说明
/ai 说明 生成 <id>
/ai 说明 编辑 <id>
```

Rules:

- every command/skill/MCP/CLI/provider feature SHOULD have `description_zh`;
- new skill/CLI/MCP install MUST register `description_zh`;
- only metadata is generated;
- installed tool implementation MUST NOT be modified;
- if AI-generated, mark `description_source=generated_ai`.

## 13. Minimum Safety

Privacy/stealth is not a goal.

Minimum safety only:

- admin signup disabled;
- bridge request authentication;
- platform bot token not exposed to AI prompts;
- channel history does not contain long-lived plaintext secrets;
- backups not accidentally public;
- basic admin 2FA SHOULD be enabled when available.

## 14. Acceptance

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

## 15. Source Anchors

- Mattermost Docker install: https://docs.mattermost.com/deployment-guide/server/containers/install-docker.html
- Mattermost container deployment: https://docs.mattermost.com/deployment-guide/server/deploy-containers.html
- Mattermost slash commands: https://docs.mattermost.com/integrations-guide/slash-commands.html
- Mattermost Docker repository: https://github.com/mattermost/docker
- Matrix Synapse installation: https://github.com/matrix-org/synapse/blob/develop/docs/setup/installation.md
