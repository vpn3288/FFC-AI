from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    canonical_action: str
    description_zh: str
    requires_confirmation: bool = False
    native: str = "runner"


COMMANDS: dict[tuple[str, ...], CommandSpec] = {
    ("状态",): CommandSpec("status", "显示当前运行、上下文、预算、工作区和提供商状态。"),
    ("status",): CommandSpec("status", "显示当前运行、上下文、预算、工作区和提供商状态。"),
    ("帮助",): CommandSpec("command_index", "显示中文命令索引。"),
    ("确认",): CommandSpec("confirm", "确认待执行的高风险操作。"),
    ("命令",): CommandSpec("command_index", "显示中文命令索引。"),
    ("功能",): CommandSpec("feature_index", "显示提供商、工具、扩展和能力状态。"),
    ("压缩",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("compact",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("整理上下文",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("新对话",): CommandSpec("new_conversation", "创建新的提供商会话。"),
    ("继续",): CommandSpec("continue_conversation", "继续当前会话或使用摘要模拟继续。"),
    ("每次新对话",): CommandSpec("set_policy_new_each_request", "将策略改为每次请求创建新会话。"),
    ("持续对话",): CommandSpec("set_policy_continue", "将策略改为持续复用当前会话。"),
    ("上下文",): CommandSpec("context_status", "显示上下文用量、阈值和压缩状态。"),
    ("context",): CommandSpec("context_status", "显示上下文用量、阈值和压缩状态。"),
    ("自动压缩", "开启"): CommandSpec("set_auto_compact_enabled", "达到上下文预警阈值时自动压缩。"),
    ("自动压缩", "关闭"): CommandSpec("set_auto_compact_disabled", "关闭达到上下文预警阈值时的自动压缩。"),
    ("聊天模式", "开启"): CommandSpec("set_permission_chat", "仅允许对话，不允许文件编辑或 shell。"),
    ("编辑模式", "开启"): CommandSpec("set_permission_edit", "允许文件编辑工具，需确认。", True),
    ("shell模式", "开启"): CommandSpec("set_permission_shell", "允许 shell 命令工具，需确认。", True),
    ("预算",): CommandSpec("budget_status", "显示每日、每月和当前运行预算状态。"),
    ("停止",): CommandSpec("cancel", "取消当前运行。"),
    ("取消",): CommandSpec("cancel", "取消当前运行。"),
    ("全局", "查看"): CommandSpec("global_instructions.show", "显示 global.md 哈希和预览。"),
    ("全局", "设置"): CommandSpec("global_instructions.set", "替换 global.md，需确认。", True),
    ("全局", "追加"): CommandSpec("global_instructions.append", "追加 global.md 并创建快照。"),
    ("全局", "替换"): CommandSpec("global_instructions.set", "替换 global.md，需确认。", True),
    ("全局", "回滚"): CommandSpec("global_instructions.rollback", "回滚 global.md 到快照，需确认。", True),
    ("全局", "应用"): CommandSpec("global_instructions.apply", "将 global.md 应用到选定提供商。"),
    ("项目", "查看"): CommandSpec("project_instructions.show", "显示当前工作区 project.md 哈希和预览。"),
    ("项目", "设置"): CommandSpec("project_instructions.set", "替换 project.md，需确认。", True),
    ("项目", "追加"): CommandSpec("project_instructions.append", "追加 project.md 并创建快照。"),
    ("项目", "替换"): CommandSpec("project_instructions.set", "替换 project.md，需确认。", True),
    ("项目", "回滚"): CommandSpec("project_instructions.rollback", "回滚 project.md 到快照，需确认。", True),
    ("项目", "应用"): CommandSpec("project_instructions.apply", "将 project.md 应用到选定提供商。"),
    ("凭据", "添加"): CommandSpec("credential.add", "创建凭据句柄，密文不得进入聊天记录。", True),
    ("凭据", "列表"): CommandSpec("credential.list", "列出凭据句柄和非秘密元数据。"),
    ("凭据", "测试"): CommandSpec("credential.test", "使用句柄运行授权测试，需确认。", True),
    ("凭据", "删除"): CommandSpec("credential.delete", "删除凭据句柄，需确认。", True),
    ("工作区", "列表"): CommandSpec("workspace.list", "列出可用工作区。"),
    ("工作区", "使用"): CommandSpec("workspace.select", "选择工作区。"),
    ("工作区", "创建"): CommandSpec("workspace.create", "创建工作区，需确认。", True),
    ("提供商", "列表"): CommandSpec("provider.list", "列出 Claude Code、Codex 和其他适配器状态。"),
    ("提供商", "使用"): CommandSpec("provider.select", "切换默认提供商。"),
    ("扩展", "列表"): CommandSpec("extension.list", "列出可选技能和扩展。"),
    ("工具", "列表"): CommandSpec("tool.list", "列出可选 CLI 工具。"),
    ("mcp", "列表"): CommandSpec("mcp.list", "列出 MCP 扩展。"),
    ("说明", "生成"): CommandSpec("description.generate", "生成中文说明元数据，不修改工具代码。"),
}


def parse_command(raw_text: str, allow_bare: bool = False) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("/ai"):
        rest = text[3:].strip()
    elif allow_bare and text == "/":
        return {"status": "rejected", "error": "bare_slash_not_command"}
    elif allow_bare and text.startswith("/"):
        rest = text[1:].strip()
    else:
        return {"status": "rejected", "error": "command_must_start_with_/ai"}

    parts = tuple(part for part in rest.split() if part)
    if not parts:
        parts = ("帮助",)

    for size in range(len(parts), 0, -1):
        head = parts[:size]
        if head in COMMANDS:
            spec = COMMANDS[head]
            return {
                "status": "accepted",
                "canonical_action": spec.canonical_action,
                "args": {"tail": list(parts[size:])},
                "requires_confirmation": spec.requires_confirmation,
            }

    return {"status": "rejected", "error": "unknown_command"}


def command_index() -> list[dict[str, Any]]:
    return [
        {
            "usage": "/ai " + " ".join(parts),
            "canonical_action": spec.canonical_action,
            "description_zh": spec.description_zh,
            "requires_confirmation": spec.requires_confirmation,
            "implemented_by": spec.native,
            "enabled": True,
            "provider": "runner",
            "native": spec.native == "native",
            "emulated": spec.native == "runner",
            "unsupported": False,
            "installed": True,
        }
        for parts, spec in sorted(COMMANDS.items())
    ]
