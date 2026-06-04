from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .budget import BudgetLedger
from .commands import command_index
from .context import ContextState, estimate_tokens
from .credentials import CredentialBroker
from .context_store import ContextStore
from .events import EventSink, status_event
from .instructions import InstructionStore
from .paths import state_root, workspace_root
from .providers import provider_status
from .providers import build_instruction_prompt, invoke_claude, invoke_codex
from .storage import atomic_write_json


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("anthropic_or_openai_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("bridge_secret_assignment", re.compile(r"\bAI_BRIDGE_SHARED_SECRET\s*=\s*(?!<|\\$|\\{)[A-Za-z0-9_-]{32,}\b")),
)


@dataclass
class RunnerRuntime:
    state: Path
    workspaces: Path
    webhook_url: str | None = None

    @classmethod
    def default(cls) -> "RunnerRuntime":
        return cls(state_root(), workspace_root())

    @property
    def ledger(self) -> BudgetLedger:
        return BudgetLedger(self.state / "budget" / "ledger.json")

    @property
    def instructions(self) -> InstructionStore:
        return InstructionStore(self.state / "instructions" / "global.md", self.workspaces)

    @property
    def credentials(self) -> CredentialBroker:
        return CredentialBroker(self.state / "credentials")

    @property
    def contexts(self) -> ContextStore:
        return ContextStore(self.state / "contexts")

    @property
    def events(self) -> EventSink:
        return EventSink(self.state / "events.jsonl", self.webhook_url)

    @property
    def policy_path(self) -> Path:
        return self.state / "conversation-policy.json"

    def load_policy(self) -> dict[str, Any]:
        default = {"policy": "continue", "conversation_id": "default", "auto_compact_enabled": False, "permission_scope": "chat"}
        if self.policy_path.exists():
            return default | json.loads(self.policy_path.read_text(encoding="utf-8"))
        return default

    def save_policy(self, policy: dict[str, Any]) -> None:
        atomic_write_json(self.policy_path, policy)


def _ok(request_id: str, run_id: str | None, message_zh: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"request_id": request_id, "status": "accepted", "run_id": run_id, "message_zh": message_zh, "data": data or {}, "error": None}


def _error(request_id: str, code: str, detail: str) -> dict[str, Any]:
    return {"request_id": request_id, "status": "error", "run_id": None, "message_zh": "执行失败", "data": {}, "error": {"code": code, "detail": detail}}


def _json_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_state(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _selected_workspace(rt: RunnerRuntime) -> str | None:
    return _json_state(rt.state / "workspace-selection.json").get("workspace_id")


def _selected_provider(rt: RunnerRuntime) -> str | None:
    return _json_state(rt.state / "provider-selection.json").get("provider")


def _provider_from_args(args: list[str]) -> str | None:
    provider = args[0] if args else None
    if provider == "claude":
        provider = "claude-code"
    if provider in {"claude-code", "codex"}:
        return provider
    return None


def _valid_workspace_id(workspace_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", workspace_id))


def _secret_findings(text: str) -> list[str]:
    scrubbed = re.sub(r"\{\{credential://[^}]+\}\}", "{{credential-handle}}", text)
    return [name for name, pattern in SECRET_PATTERNS if pattern.search(scrubbed)]


def _provider_capabilities(provider: str) -> dict[str, Any]:
    status = next((item for item in provider_status() if item.get("provider") == provider), None)
    return status.get("capabilities", {}) if status else {}


def _install_manifest(rt: RunnerRuntime) -> dict[str, Any]:
    path = rt.state / "install-manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _command_index_with_availability(items: list[dict[str, Any]], providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    any_provider_available = any(item.get("available") for item in providers)
    codex_available = any(item.get("provider") == "codex" and item.get("available") for item in providers)
    updated = []
    for item in items:
        row = dict(item)
        action = row.get("canonical_action", "")
        if action in {"provider.select"} and not any_provider_available:
            row["enabled"] = False
            row["unsupported"] = True
        if action == "provider.select" and "codex" in row.get("usage", "") and not codex_available:
            row["enabled"] = False
        updated.append(row)
    return updated


def current_status(runtime: RunnerRuntime) -> dict[str, Any]:
    return {
        "core_ready": False,
        "providers": provider_status(),
        "budget": runtime.ledger.load(),
        "policy": runtime.load_policy(),
        "current_workspace": _selected_workspace(runtime) or "default",
        "default_provider": _selected_provider(runtime) or "claude-code",
        "default_workspace": _selected_workspace(runtime) or "default",
        "state_root": str(runtime.state),
        "workspace_root": str(runtime.workspaces),
    }


def execute(parsed: dict[str, Any], envelope: dict[str, Any], runtime: RunnerRuntime | None = None) -> dict[str, Any]:
    rt = runtime or RunnerRuntime.default()
    request_id = envelope.get("request_id") or str(uuid.uuid4())
    if parsed.get("status") != "accepted":
        return _error(request_id, parsed.get("error", "rejected"), parsed.get("error", "rejected"))
    if parsed.get("requires_confirmation") and not envelope.get("confirmed"):
        return {
            "request_id": request_id,
            "status": "needs_confirmation",
            "run_id": None,
            "message_zh": "此操作需要确认",
            "data": {"canonical_action": parsed.get("canonical_action"), "confirmation_token": str(uuid.uuid4())},
            "error": None,
        }

    action = parsed["canonical_action"]
    args = parsed.get("args", {}).get("tail", [])
    workspace_id = envelope.get("workspace_id") or _selected_workspace(rt) or "default"
    if not _valid_workspace_id(workspace_id):
        return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
    run_id = str(uuid.uuid4())
    rt.events.emit(status_event(run_id, "queued", "正在排队"))

    if action == "status":
        return _ok(request_id, run_id, "状态已生成", current_status(rt))
    if action in {"command_index", "feature_index"}:
        providers = provider_status()
        manifest = _install_manifest(rt)
        feature_data: dict[str, Any] = {"items": _command_index_with_availability(command_index(), providers), "providers": providers}
        codex = next((item for item in providers if item["provider"] == "codex"), None)
        feature_data["codex_ready"] = manifest.get("codex_ready", bool(codex and codex.get("available")))
        feature_data["codex_status"] = manifest.get("codex_status", "external_prerequisite")
        feature_data["codex_remediation_zh"] = "Codex 若不可用，需要按当前 Codex CLI 官方说明手动安装并重新运行 /ai 提供商 列表。"
        if manifest.get("codex_remediation_zh"):
            feature_data["codex_remediation_zh"] = manifest["codex_remediation_zh"]
        if codex and not codex.get("available"):
            feature_data["codex_remediation_zh"] = "Codex 当前需要手动安装。请查看官方安装说明后重新运行 /ai 提供商 列表。"
        return _ok(request_id, run_id, "索引已生成", feature_data)
    if action == "budget_status":
        return _ok(request_id, run_id, "预算已生成", rt.ledger.load())
    if action == "context_status":
        prompt_text = envelope.get("raw_text", "")
        used = estimate_tokens(prompt_text)
        state = ContextState("unknown", envelope.get("provider") or _selected_provider(rt) or "runner", 200000, used)
        return _ok(request_id, run_id, "上下文状态已生成", state.__dict__ | {"context_used_percent": state.context_used_percent})
    if action == "task.run":
        prompt = parsed.get("args", {}).get("prompt") or envelope.get("raw_text", "")
        provider = envelope.get("provider") or _selected_provider(rt) or "claude-code"
        reserved_usd = float(envelope.get("reserved_usd", 1.0))
        budget_ok, budget_reason = rt.ledger.can_reserve(reserved_usd)
        if not budget_ok:
            return _error(request_id, budget_reason, f"Budget preflight failed before provider start: {budget_reason}")
        instruction_prompt = build_instruction_prompt(rt.instructions, workspace_id)
        secret_findings = _secret_findings(instruction_prompt)
        if secret_findings:
            return _error(request_id, "secrets_in_instructions", f"Secret-like material in instructions: {', '.join(secret_findings)}. Move secrets into credential handles.")
        global_info = rt.instructions.show("global", workspace_id)
        project_info = rt.instructions.show("project", workspace_id)
        policy = rt.load_policy()
        if policy.get("permission_scope") == "shell" and not envelope.get("confirmed"):
            return {
                "request_id": request_id,
                "status": "needs_confirmation",
                "run_id": run_id,
                "message_zh": "Shell 模式任务需要逐次确认",
                "data": {"canonical_action": "task.run", "permission_scope": "shell", "confirmation_token": str(uuid.uuid4())},
                "error": None,
            }
        if envelope.get("conversation_id"):
            conversation_id = envelope["conversation_id"]
        elif policy.get("policy") == "new_each_request":
            conversation_id = str(uuid.uuid4())
        else:
            conversation_id = policy.get("conversation_id") or "default"
        existing = rt.contexts.load(conversation_id, provider)
        if existing.get("summary_artifact") and Path(existing["summary_artifact"]).exists():
            instruction_prompt = f"{instruction_prompt}\n\n# Previous Context Summary\n{Path(existing['summary_artifact']).read_text(encoding='utf-8')}\n"
        used = existing["context_used_tokens"] + estimate_tokens(instruction_prompt, prompt)
        context_state = ContextState(conversation_id, provider, existing["context_limit_tokens"], used)
        if context_state.hard_stop:
            return _error(
                request_id,
                "context_hard_stop",
                f"Context usage {context_state.context_used_percent}% exceeds hard limit {context_state.hard_stop_threshold_percent}%. Run /ai 压缩 or /ai 新对话 to continue.",
            )
        if context_state.needs_warning:
            rt.events.emit(status_event(run_id, "warning", "上下文接近上限，请考虑压缩或新对话", provider))
            auto_compact_enabled = (policy.get("policy") != "new_each_request") and (bool(policy.get("auto_compact_enabled")) or str(envelope.get("auto_compact_enabled", "")).lower() in {"1", "true", "yes"})
            if auto_compact_enabled:
                compacted = rt.contexts.compact(conversation_id, provider)
                conversation_id = compacted["new_conversation_id"]
                policy["conversation_id"] = conversation_id
                rt.save_policy(policy)
                instruction_prompt = f"{instruction_prompt}\n\n# Previous Context Summary\n{Path(compacted['summary_artifact']).read_text(encoding='utf-8')}\n"
        workspace = rt.workspaces / workspace_id
        workspace.mkdir(parents=True, exist_ok=True)
        emit = rt.events.emit
        if provider == "codex":
            result = invoke_codex(prompt, workspace, rt.ledger, instruction_prompt=instruction_prompt, run_id=run_id, reserved_usd=reserved_usd, emit=emit)
        else:
            result = invoke_claude(prompt, workspace, instruction_prompt, rt.ledger, run_id=run_id, reserved_usd=reserved_usd, emit=emit, permission_scope=policy.get("permission_scope", "chat"))
        rt.contexts.add_exchange(conversation_id, provider, instruction_prompt, prompt, result.output_text)
        return _ok(
            request_id,
            run_id,
            "任务已完成",
            {
                "provider": result.provider,
                "status": result.status,
                "output": result.output_text,
                "conversation_id": conversation_id,
                "global_md_sha256": global_info["sha256"],
                "project_md_sha256": project_info["sha256"],
            },
        )
    if action == "compact_context":
        policy = rt.load_policy()
        provider = envelope.get("provider") or _selected_provider(rt) or "claude-code"
        capabilities = _provider_capabilities(provider)
        if not (capabilities.get("new_conversation") or capabilities.get("continue_conversation")):
            return _error(request_id, "provider_compaction_unsupported", f"{provider} does not report new or continued conversation support")
        conversation_id = envelope.get("conversation_id") or policy.get("conversation_id") or "default"
        compacted = rt.contexts.compact(conversation_id, provider)
        policy["conversation_id"] = compacted["new_conversation_id"]
        rt.save_policy(policy)
        return _ok(request_id, run_id, "上下文已压缩", compacted)
    if action == "provider.list":
        return _ok(request_id, run_id, "提供商列表已生成", {"providers": provider_status()})
    if action == "provider.select":
        provider = _provider_from_args(args)
        if provider is None:
            return _error(request_id, "invalid_provider", "provider must be claude-code or codex")
        _write_json_state(rt.state / "provider-selection.json", {"provider": provider})
        return _ok(request_id, run_id, "提供商已选择", {"provider": provider})
    if action == "credential.list":
        return _ok(request_id, run_id, "凭据列表已生成", {"credentials": rt.credentials.list_public()})
    if action == "credential.add":
        handle = args[0] if args else f"credential://pending/{uuid.uuid4()}"
        return _ok(
            request_id,
            run_id,
            "凭据添加流程已创建",
            {
                "handle": handle,
                "secret_material": "never send secret material in chat",
                "bridge_upload_url_endpoint": "/bridge/credential-upload-url",
                "bridge_upload_url_method": "POST",
                "local_capture_command": f"python3 -m ai_remote_runner.cli credential-add-secret --metadata-json '{{\"handle\":\"{handle}\"}}'",
                "status": "awaiting_local_secret_capture",
            },
        )
    if action == "credential.test":
        if not args:
            return _error(request_id, "missing_credential_handle", "missing_credential_handle")
        return _ok(request_id, run_id, "凭据测试完成", rt.credentials.test(args[0]))
    if action == "credential.delete":
        if not args:
            return _error(request_id, "missing_credential_handle", "missing_credential_handle")
        return _ok(request_id, run_id, "凭据已删除", rt.credentials.delete(args[0]))
    if action.endswith(".show"):
        scope = "global" if action.startswith("global_") else "project"
        return _ok(request_id, run_id, "指令已读取", rt.instructions.show(scope, workspace_id))
    if action.endswith(".append"):
        scope = "global" if action.startswith("global_") else "project"
        text = " ".join(args)
        return _ok(request_id, run_id, "指令已追加", rt.instructions.write(scope, text, workspace_id, append=True))
    if action.endswith(".rollback"):
        scope = "global" if action.startswith("global_") else "project"
        if not args:
            return _error(request_id, "missing_snapshot", "missing_snapshot")
        try:
            return _ok(request_id, run_id, "指令已回滚", rt.instructions.rollback(scope, args[0], workspace_id))
        except FileNotFoundError:
            return _error(request_id, "snapshot_not_found", args[0])
    if action.endswith(".apply"):
        scope = "global" if action.startswith("global_") else "project"
        shown = rt.instructions.show(scope, workspace_id)
        return _ok(request_id, run_id, "指令已应用", {"scope": scope, "workspace_id": workspace_id, "sha256": shown["sha256"], "provider": _selected_provider(rt) or "claude-code"})
    if action.endswith(".set"):
        scope = "global" if action.startswith("global_") else "project"
        text = " ".join(args)
        return _ok(request_id, run_id, "指令已替换", rt.instructions.write(scope, text, workspace_id, append=False))
    if action in {"new_conversation", "continue_conversation", "set_policy_new_each_request", "set_policy_continue"}:
        data = rt.load_policy()
        if action == "new_conversation":
            data["conversation_id"] = str(uuid.uuid4())
        elif action == "set_policy_new_each_request":
            data["policy"] = "new_each_request"
        elif action == "set_policy_continue":
            data["policy"] = "continue"
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "会话策略已更新", data)
    if action in {"set_auto_compact_enabled", "set_auto_compact_disabled"}:
        data = rt.load_policy()
        data["auto_compact_enabled"] = action == "set_auto_compact_enabled"
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "自动压缩策略已更新", data)
    if action in {"set_permission_chat", "set_permission_edit", "set_permission_shell"}:
        data = rt.load_policy()
        data["permission_scope"] = action.removeprefix("set_permission_")
        data["last_action"] = action
        rt.save_policy(data)
        return _ok(request_id, run_id, "执行权限模式已更新", data)
    if action == "workspace.list":
        rt.workspaces.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区列表已生成", {"workspaces": sorted(path.name for path in rt.workspaces.iterdir() if path.is_dir())})
    if action == "workspace.create":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        if not _valid_workspace_id(args[0]):
            return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
        target = rt.workspaces / args[0]
        target.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区已创建", {"workspace_id": args[0], "path": str(target)})
    if action == "workspace.select":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        if not _valid_workspace_id(args[0]):
            return _error(request_id, "invalid_workspace_id", "workspace_id must contain only letters, numbers, dash, or underscore")
        _write_json_state(rt.state / "workspace-selection.json", {"workspace_id": args[0]})
        return _ok(request_id, run_id, "工作区已选择", {"workspace_id": args[0]})
    if action in {"extension.list", "tool.list", "mcp.list"}:
        return _ok(
            request_id,
            run_id,
            "扩展索引已生成",
            {
                "items": [
                    {"id": "filesystem", "kind": "mcp", "installed": False, "description_zh": "文件系统访问扩展。"},
                    {"id": "git", "kind": "mcp", "installed": False, "description_zh": "Git 仓库操作扩展。"},
                    {"id": "opencli", "kind": "tool", "installed": False, "description_zh": "可选命令行工具。"},
                ]
            },
        )
    if action == "description.generate":
        target = args[0] if args else "unknown"
        return _ok(request_id, run_id, "说明已生成", {"id": target, "description_zh": f"{target} 的中文说明待人工确认。", "description_source": "generated_ai_stub"})
    return _error(request_id, "unsupported_action", action)
