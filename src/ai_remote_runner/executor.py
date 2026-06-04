from __future__ import annotations

import json
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


def _ok(request_id: str, run_id: str | None, message_zh: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"request_id": request_id, "status": "accepted", "run_id": run_id, "message_zh": message_zh, "data": data or {}, "error": None}


def _error(request_id: str, code: str, detail: str) -> dict[str, Any]:
    return {"request_id": request_id, "status": "error", "run_id": None, "message_zh": "执行失败", "data": {}, "error": {"code": code, "detail": detail}}


def current_status(runtime: RunnerRuntime) -> dict[str, Any]:
    return {
        "core_ready": False,
        "providers": provider_status(),
        "budget": runtime.ledger.load(),
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
    workspace_id = envelope.get("workspace_id") or "default"
    run_id = str(uuid.uuid4())
    rt.events.emit(status_event(run_id, "queued", "正在排队"))

    if action == "status":
        return _ok(request_id, run_id, "状态已生成", current_status(rt))
    if action in {"command_index", "feature_index"}:
        providers = provider_status()
        feature_data: dict[str, Any] = {"items": command_index(), "providers": providers}
        codex = next((item for item in providers if item["provider"] == "codex"), None)
        if codex and not codex.get("available"):
            feature_data["codex_remediation_zh"] = "Codex 当前需要手动安装。请查看官方安装说明后重新运行 /ai 提供商 列表。"
            feature_data["codex_status"] = "external_prerequisite"
        return _ok(request_id, run_id, "索引已生成", feature_data)
    if action == "budget_status":
        return _ok(request_id, run_id, "预算已生成", rt.ledger.load())
    if action == "context_status":
        prompt_text = envelope.get("raw_text", "")
        used = estimate_tokens(prompt_text)
        state = ContextState("unknown", envelope.get("provider", "runner"), 200000, used)
        return _ok(request_id, run_id, "上下文状态已生成", state.__dict__ | {"context_used_percent": state.context_used_percent})
    if action == "task.run":
        prompt = parsed.get("args", {}).get("prompt") or envelope.get("raw_text", "")
        instruction_prompt = build_instruction_prompt(rt.instructions, workspace_id)
        conversation_id = envelope.get("conversation_id") or "default"
        existing = rt.contexts.load(conversation_id, envelope.get("provider", "claude-code"))
        used = existing["context_used_tokens"] + estimate_tokens(instruction_prompt, prompt)
        context_state = ContextState(conversation_id, envelope.get("provider", "claude-code"), existing["context_limit_tokens"], used)
        if context_state.hard_stop:
            return _error(request_id, "context_hard_stop", "context_hard_stop")
        workspace = rt.workspaces / workspace_id
        workspace.mkdir(parents=True, exist_ok=True)
        provider = envelope.get("provider", "claude-code")
        emit = rt.events.emit
        if provider == "codex":
            result = invoke_codex(prompt, workspace, rt.ledger, run_id=run_id, emit=emit)
        else:
            result = invoke_claude(prompt, workspace, instruction_prompt, rt.ledger, run_id=run_id, emit=emit)
        rt.contexts.add_exchange(conversation_id, provider, instruction_prompt, prompt, result.output_text)
        return _ok(request_id, run_id, "任务已完成", {"provider": result.provider, "status": result.status, "output": result.output_text})
    if action == "compact_context":
        summary_dir = rt.state / "context-summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        summary_path = summary_dir / f"{run_id}.md"
        summary_path.write_text(f"# Context Summary\n\nrequest_id: {request_id}\nworkspace_id: {workspace_id}\n", encoding="utf-8")
        return _ok(request_id, run_id, "上下文已压缩", {"summary_artifact": str(summary_path), "new_conversation_id": str(uuid.uuid4())})
    if action == "provider.list":
        return _ok(request_id, run_id, "提供商列表已生成", {"providers": provider_status()})
    if action == "credential.list":
        return _ok(request_id, run_id, "凭据列表已生成", {"credentials": rt.credentials.list_public()})
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
    if action.endswith(".set"):
        scope = "global" if action.startswith("global_") else "project"
        text = " ".join(args)
        return _ok(request_id, run_id, "指令已替换", rt.instructions.write(scope, text, workspace_id, append=False))
    if action in {"new_conversation", "continue_conversation", "set_policy_new_each_request", "set_policy_continue"}:
        state_path = rt.state / "conversation-policy.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_action": action, "conversation_id": str(uuid.uuid4()) if action == "new_conversation" else envelope.get("conversation_id")}
        state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return _ok(request_id, run_id, "会话策略已更新", data)
    if action == "workspace.list":
        rt.workspaces.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区列表已生成", {"workspaces": sorted(path.name for path in rt.workspaces.iterdir() if path.is_dir())})
    if action == "workspace.create":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        target = rt.workspaces / args[0]
        target.mkdir(parents=True, exist_ok=True)
        return _ok(request_id, run_id, "工作区已创建", {"workspace_id": args[0], "path": str(target)})
    if action == "workspace.select":
        if not args:
            return _error(request_id, "missing_workspace_id", "missing_workspace_id")
        state_path = rt.state / "workspace-selection.json"
        state_path.write_text(json.dumps({"workspace_id": args[0]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return _ok(request_id, run_id, "工作区已选择", {"workspace_id": args[0]})
    return _error(request_id, "unsupported_action", action)
