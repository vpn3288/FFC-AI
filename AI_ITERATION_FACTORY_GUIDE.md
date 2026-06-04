# AI Iteration Factory Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Objective

The system SHALL iteratively produce and refine implementation guidance for a self-hosted mobile-controlled AI operations stack.

The stack SHALL support:

- local small-host or WSL runner machines;
- VPS-hosted communication platform;
- Claude Code;
- Codex;
- other future AI adapters;
- self-hosted communication platform, default Mattermost, fallback Matrix/Synapse;
- mobile command/control UX;
- mobile status/progress display;
- context usage display;
- manual and automatic context compaction;
- new-conversation and per-request-new-conversation modes;
- Chinese command aliases normalized to canonical English actions;
- credential broker for SSH/VPS/API secrets;
- `global.md` and `project.md` instruction input/edit/apply flows;
- command, skill, MCP, CLI, and feature index with Chinese descriptions;
- optional post-install extension/tool bundle.

Telegram MUST NOT be used.

Privacy/stealth MUST NOT be optimized beyond minimum credential safety, remote-execution safety, budget control, and recoverability.

## 2. Maintained Files

The master-writer AI MUST maintain exactly these output files:

```text
outputs/AI_ITERATION_FACTORY_GUIDE.md
outputs/AI_REMOTE_RUNNER_GUIDE.md
outputs/COMMUNICATION_PLATFORM_GUIDE.md
```

`AI_ITERATION_FACTORY_GUIDE.md` defines the writer/reviewer loop.

`AI_REMOTE_RUNNER_GUIDE.md` defines the local/WSL/small-host runner, provider adapters, commands, context, credentials, instruction files, optional tools, and acceptance tests.

`COMMUNICATION_PLATFORM_GUIDE.md` defines communication platform selection, VPS deployment, mobile UX, group collaboration, command routing, and credential capture UX.

## 3. Roles

### 3.1 Master Writer AI

The master-writer AI MUST:

- preserve user requirements;
- convert requirements into precise implementation specifications;
- remove ambiguity;
- keep scope centered on core working functionality;
- merge reviewer output only after independent reviews finish;
- maintain version hashes and review state;
- reject recommendations that reintroduce Telegram;
- reject privacy/stealth expansion unless required for credential or remote-execution safety.

### 3.2 Claude Code Reviewer AI

Claude Code reviewer MUST run in a fresh Claude Code conversation per review.

It MUST inspect all three files.

It MUST focus on:

- Debian 12 / WSL / VPS feasibility;
- Claude Code CLI feasibility;
- provider adapter correctness;
- command normalization;
- credential broker safety;
- context compaction feasibility;
- install/update/rollback feasibility;
- P0/P1 blockers.

### 3.3 GPT-5.5 Reviewer AI

GPT-5.5 reviewer MUST run in a fresh conversation per review.

It MUST inspect all three files.

It MUST focus on:

- product completeness;
- mobile UX completeness;
- self-hosted communication selection;
- AI collaboration loop;
- Codex compatibility boundaries;
- command index discoverability;
- missing user requirements;
- P0/P1 blockers.

Reviewer AIs MUST NOT read each other's review before both reviews finish.

## 4. Iteration Procedure

Each round MUST execute:

1. Read the three maintained files.
2. Compute SHA256 for each file.
3. Apply new user requirements.
4. Produce revised files.
5. Start fresh Claude Code review.
6. Start fresh GPT-5.5 review.
7. Wait for both reviews or record timeout.
8. Merge review findings.
9. Fix P0/P1 and missing user requirements.
10. Update `work/iteration-state.json`.
11. Stop only if quality gate passes or stopping condition triggers.

## 5. Iteration State

The loop MUST maintain:

```json
{
  "round": 0,
  "max_rounds": 10,
  "min_score": 90,
  "max_usd": 25.0,
  "spent_usd_estimate": 0.0,
  "input_sha256": {
    "AI_ITERATION_FACTORY_GUIDE.md": "",
    "AI_REMOTE_RUNNER_GUIDE.md": "",
    "COMMUNICATION_PLATFORM_GUIDE.md": ""
  },
  "output_sha256": {
    "AI_ITERATION_FACTORY_GUIDE.md": "",
    "AI_REMOTE_RUNNER_GUIDE.md": "",
    "COMMUNICATION_PLATFORM_GUIDE.md": ""
  },
  "review_sha256": {},
  "score_history": [],
  "open_p0": 0,
  "open_p1": 0,
  "missing_user_requirements": [],
  "stop_reason": null
}
```

## 6. Quality Gate

Pass requires:

- score >= 90;
- open P0 count = 0;
- open P1 count = 0;
- missing user requirements = empty;
- Telegram absent except as explicit prohibition;
- Mattermost selected as primary platform;
- Matrix/Synapse selected as fallback platform;
- AI runner core install not blocked by optional skills/CLI/MCP tools;
- phone commands include Chinese aliases;
- `/` index shows Chinese descriptions for commands, skills, MCP extensions, CLI tools, and provider features;
- newly installed skill/CLI/MCP registers Chinese description metadata;
- `global.md` and `project.md` flows include show/set/append/replace/rollback/apply;
- credential broker supports SSH host/IP, username, password, private key, API token, handle-based use, and no plaintext to AI prompts;
- context telemetry includes usage, threshold warnings, manual compaction, automatic compaction, and new-conversation policy;
- Claude Code and Codex support is adapter-based with capability discovery;
- unsupported native features are explicitly reported, not hidden.

## 7. Scoring Rubric

```text
Functional completeness: 30
Implementation feasibility: 25
AI/provider compatibility: 20
Minimum safety: 15
Specification precision: 10
```

Minimum safety means only:

- no secrets in GitHub;
- no secrets in AI prompts;
- no uncontrolled remote shell;
- no unlimited spend;
- recoverable install/update/rollback.

## 8. Severity

P0:

- credential disclosure;
- uncontrolled remote command execution;
- unlimited spend;
- destructive install/update behavior;
- unrecoverable system damage.

P1:

- core install cannot proceed;
- selected communication platform cannot support required UX;
- Claude Code/Codex adapter path invalid;
- phone command layer incomplete;
- credential broker unusable;
- context/new-conversation/compaction requirements missing;
- user requirement omitted.

P2:

- important but non-blocking implementation gap.

P3:

- wording, organization, minor maintainability.

## 9. Review Prompts

Claude Code reviewer prompt MUST include:

```text
Review all three files. Only report P0/P1 blockers, missing user requirements, and necessary fixes. Do not expand privacy/stealth scope. Verify that Telegram is prohibited, Mattermost is primary, Matrix is fallback, mobile commands work in Chinese, credentials use handles, global.md/project.md are supported, optional tools do not block core-ready, and command index shows Chinese descriptions.
```

GPT-5.5 reviewer prompt MUST include:

```text
Review all three files in a fresh conversation. Only report P0/P1 blockers, missing user requirements, and necessary fixes. Do not expand privacy/stealth scope. Check mobile UX completeness, AI compatibility, communication platform choice, credential handle workflow, context compaction, command index, Chinese descriptions, and optional post-install tooling.
```

## 10. Source Anchors

Primary external anchors:

- Claude Code CLI: https://code.claude.com/docs/en/cli-usage
- Claude Code installation: https://code.claude.com/docs/en/installation
- Claude Code LLM Gateway: https://code.claude.com/docs/en/llm-gateway
- Mattermost Docker deployment: https://docs.mattermost.com/deployment-guide/server/containers/install-docker.html
- Mattermost slash commands: https://docs.mattermost.com/integrations-guide/slash-commands.html
- Mattermost Docker repository: https://github.com/mattermost/docker
- Matrix Synapse installation: https://github.com/matrix-org/synapse/blob/develop/docs/setup/installation.md

