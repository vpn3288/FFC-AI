# AI Iteration Factory Specification

Version: 1.0
Date: 2026-06-04
Audience: master-writer AI, reviewer AIs, implementation AIs.
Style: AI-only executable specification. Human readability is not a goal.

## 1. Objective

The system SHALL iteratively produce, test, review, and refine implementation scripts for a self-hosted mobile-controlled AI operations stack.

The loop MUST run in infinite convergence mode. It MUST NOT stop because a fixed round count was reached. It MAY stop only when every maintained guidance file and every implementation script satisfies the quality gate with open P0 count = 0, open P1 count = 0, open P2 count = 0, missing required functionality = empty, and all required smoke tests passing.

The stack SHALL support:

- local small-host or WSL runner machines;
- VPS-hosted communication platform;
- Claude Code;
- Codex;
- other future AI adapters;
- self-hosted communication platform, default Mattermost, fallback Matrix/Synapse;
- optional Telegram bot channel after explicit pairing;
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

Telegram MAY be used only as an optional runner-side bot channel. It MUST NOT replace Mattermost as the default platform, MUST NOT block core-ready, and MUST require explicit BotFather token plus Telegram ID pairing before executing AI commands.

Privacy/stealth MUST NOT be optimized beyond minimum credential safety, remote-execution safety, budget control, and recoverability.

## 2. Maintained Files

The master-writer AI MUST maintain exactly these guidance output files:

```text
outputs/AI_ITERATION_FACTORY_GUIDE.md
outputs/AI_REMOTE_RUNNER_GUIDE.md
outputs/COMMUNICATION_PLATFORM_GUIDE.md
```

The master-writer AI MUST also maintain implementation artifacts under:

```text
scripts/
src/
tests/
versions.lock
work/iteration-state.json
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
- reject recommendations that make Telegram mandatory, unauthenticated, or a replacement for default Mattermost;
- reject privacy/stealth expansion unless required for credential or remote-execution safety.
- engage reviewer AIs adversarially instead of using them only as defect detectors;
- maintain a separate creative-proposal track for strong non-blocking ideas.

### 3.2 Claude Code Reviewer AI

Claude Code reviewer MUST run in a fresh Claude Code conversation per review.

Claude Code reviewer MUST NOT use `--continue`, `--resume`, or any persisted previous review context.

If the selected Claude Code model hangs or produces unusable review output, the master-writer MAY start a new fresh review conversation with one of these models:

```text
claude-opus-4-6-thinking
claude-opus-4-6
claude-opus-4-7-thinking
claude-opus-4-7
claude-opus-4-8-thinking
claude-opus-4-8
```

Model switching MUST preserve fresh-conversation isolation and MUST NOT use previous failed review context except the current repository files and review instructions.

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

It SHOULD propose aggressive implementation alternatives that improve functionality, mobile UX, extensibility, or implementation quality.

It MUST detect:

- over-compression by the master-writer that removes needed implementation detail;
- unnecessary optimization that adds complexity without improving the user's target effect;
- speculative hardening that distracts from core functionality.

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

It SHOULD challenge master-writer assumptions and propose product-level alternatives.

It MUST detect:

- underspecified areas caused by excessive simplification;
- over-engineered areas caused by unnecessary optimization;
- feature omissions hidden by concise wording;
- constraints that should be relaxed to improve the final product.

Reviewer AIs MUST NOT read each other's review before both reviews finish.

### 3.4 Adversarial Creativity Mode

The loop MUST run in adversarial creativity mode.

Reviewer output MUST be partitioned:

```text
P0
P1
High-value P2
Creative proposals
Rejected constraints to reconsider
Over-compression findings
Over-engineering findings
Must fix
Score
```

Rules:

- P0/P1 findings are mandatory to address.
- Creative proposals MAY exceed current implementation scope.
- Creative proposals MUST NOT be treated as blockers unless they expose P0/P1 risk.
- Master-writer MUST mark each creative proposal as `adopted`, `deferred`, or `rejected`.
- Master-writer MAY relax non-safety constraints when a proposal improves core functionality, mobile UX, extensibility, or implementation quality.
- Master-writer MUST NOT relax: Telegram optional-only, no secrets in AI prompts, no uncontrolled remote execution, no unlimited spend.
- Reviewer AIs SHOULD actively disagree with weak assumptions.
- Reviewer AIs SHOULD propose at least three non-obvious improvements per round unless none are useful.
- Reviewer AIs MUST explicitly state whether the master-writer over-compressed necessary implementation detail.
- Reviewer AIs MUST explicitly state whether the master-writer over-engineered unnecessary optimizations.

## 4. Iteration Procedure

Each round MUST execute:

1. Read the three maintained files.
2. Compute SHA256 for each file.
3. Apply new user requirements.
4. Produce revised guidance files and implementation script changes.
5. Start fresh Claude Code review.
6. Start fresh GPT-5.5 review.
7. Wait for both reviews or record timeout.
8. Merge review findings.
9. Fix P0/P1/P2 findings and missing required functionality.
10. Run script smoke tests.
11. Update `work/iteration-state.json`.
12. Commit the optimization.
13. Push the optimization to GitHub.
14. Continue the next round unless the infinite convergence gate passes.

## 5. Iteration State

The loop MUST maintain:

```json
{
  "round": 0,
  "mode": "infinite_convergence",
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
  "open_p2": 0,
  "missing_user_requirements": [],
  "script_smoke_tests_passing": false,
  "last_github_push": null,
  "stop_reason": null
}
```

## 6. Quality Gate

Pass requires:

- score >= 90;
- open P0 count = 0;
- open P1 count = 0;
- open P2 count = 0;
- missing user requirements = empty;
- implementation scripts exist for runner install, communication install, rollback, bridge, providers, commands, credentials, budget, context, instruction files, and smoke tests;
- implementation smoke tests pass locally;
- latest optimization commit has been pushed to GitHub;
- Telegram optional service present only when explicitly enabled and paired;
- Mattermost selected as primary platform;
- Matrix/Synapse selected as fallback platform;
- AI runner core install not blocked by optional skills/CLI/MCP tools;
- AI runner installer explicitly installs or verifies Claude Code;
- AI runner installer explicitly installs, verifies, or marks Codex as external prerequisite with remediation;
- communication VPS installer explicitly installs Mattermost server by default;
- communication VPS installer explicitly configures team/channels/bots/bridge endpoints;
- bridge integration between phone communication platform and local AI runner has smoke tests;
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
Review all guidance files and implementation scripts in a fresh conversation with no resume/continue context. Report P0/P1 blockers, high-value P2, missing user requirements, creative proposals, over-compression findings, and over-engineering findings. Be adversarial. Challenge weak assumptions. Do not expand privacy/stealth scope. Verify that Telegram is optional-only and requires explicit pairing, Mattermost is primary, Matrix is fallback, mobile commands work in Chinese, credentials use handles, global.md/project.md are supported, optional tools do not block core-ready, command index shows Chinese descriptions, and scripts implement the guidance rather than logging placeholder stages.
```

GPT-5.5 reviewer prompt MUST include:

```text
Review all guidance files and implementation scripts in a fresh conversation. Report P0/P1 blockers, high-value P2, missing user requirements, creative proposals, over-compression findings, and over-engineering findings. Be adversarial. Challenge weak assumptions. Do not expand privacy/stealth scope. Check mobile UX completeness, AI compatibility, communication platform choice, credential handle workflow, context compaction, command index, Chinese descriptions, optional post-install tooling, and whether scripts fully implement the guidance without placeholder-only stages.
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
