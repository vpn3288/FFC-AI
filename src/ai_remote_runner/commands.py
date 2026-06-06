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
    ("索引",): CommandSpec("command_index", "显示中文命令索引。"),
    ("功能",): CommandSpec("feature_index", "显示提供商、工具、扩展和能力状态。"),
    ("压缩",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("compact",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("整理上下文",): CommandSpec("compact_context", "压缩当前上下文，必要时创建摘要并开启新会话。"),
    ("新对话",): CommandSpec("new_conversation", "创建新的提供商会话。"),
    ("new",): CommandSpec("new_conversation", "创建新的提供商会话。"),
    ("对话",): CommandSpec("conversation_status", "显示并启用长期持续对话模式。"),
    ("长期对话",): CommandSpec("conversation_status", "显示并启用长期持续对话模式。"),
    ("继续",): CommandSpec("continue_conversation", "继续当前会话或使用摘要模拟继续。"),
    ("continue",): CommandSpec("continue_conversation", "继续当前会话或使用摘要模拟继续。"),
    ("每次新对话",): CommandSpec("set_policy_new_each_request", "将策略改为每次请求创建新会话。"),
    ("持续对话",): CommandSpec("set_policy_continue", "将策略改为持续复用当前会话。"),
    ("mode", "new_each"): CommandSpec("set_policy_new_each_request", "将策略改为每次请求创建新会话。"),
    ("mode", "continue"): CommandSpec("set_policy_continue", "将策略改为持续复用当前会话。"),
    ("上下文",): CommandSpec("context_status", "显示上下文用量、阈值和压缩状态。"),
    ("context",): CommandSpec("context_status", "显示上下文用量、阈值和压缩状态。"),
    ("自动压缩", "开启"): CommandSpec("set_auto_compact_enabled", "达到上下文预警阈值时自动压缩。"),
    ("自动压缩", "关闭"): CommandSpec("set_auto_compact_disabled", "关闭达到上下文预警阈值时的自动压缩。"),
    ("聊天模式", "开启"): CommandSpec("set_permission_chat", "仅允许对话，不允许文件编辑或 shell。"),
    ("编辑模式", "开启"): CommandSpec("set_permission_edit", "允许文件编辑工具。"),
    ("shell模式", "开启"): CommandSpec("set_permission_shell", "允许 shell 命令工具。"),
    ("完全访问", "开启"): CommandSpec("set_permission_full", "允许 Claude/Codex 使用完整工具权限、shell 和文件访问。"),
    ("最高权限", "开启"): CommandSpec("set_permission_full", "允许 Claude/Codex 使用完整工具权限、shell 和文件访问。"),
    ("root权限", "开启"): CommandSpec("set_permission_full", "允许 Claude/Codex 使用完整工具权限、shell 和文件访问。"),
    ("预算",): CommandSpec("budget_status", "显示每日、每月和当前运行预算状态。"),
    ("预算", "设置"): CommandSpec("budget.set_task_reserved", "设置 Telegram/AI 单次任务预留预算，例如 /ai 预算 设置 1.00。"),
    ("预算", "单次"): CommandSpec("budget.set_task_reserved", "设置 Telegram/AI 单次任务预留预算，例如 /ai 预算 单次 1.00。"),
    ("budget", "set"): CommandSpec("budget.set_task_reserved", "设置 Telegram/AI 单次任务预留预算。"),
    ("模型",): CommandSpec("model.list", "查询当前 API key 可用模型；接口不可用时显示本地 fallback 列表。"),
    ("模型", "列表"): CommandSpec("model.list", "查询当前 API key 可用模型；可加 claude-code/codex/vscode。"),
    ("模型", "查看"): CommandSpec("model.list", "查询当前 API key 可用模型；可加 claude-code/codex/vscode。"),
    ("models",): CommandSpec("model.list", "查询当前 API key 可用模型。"),
    ("model", "list"): CommandSpec("model.list", "查询当前 API key 可用模型。"),
    ("模型", "使用"): CommandSpec("model.select", "切换目标模型，例如 /ai 模型 使用 vscode claude-opus-4-6。"),
    ("模型", "设置"): CommandSpec("model.select", "切换目标模型，例如 /ai 模型 设置 codex gpt-5.5。"),
    ("model", "set"): CommandSpec("model.select", "切换目标模型。"),
    ("model", "use"): CommandSpec("model.select", "切换目标模型。"),
    ("密钥", "设置"): CommandSpec("provider_config.set_api_key", "更新 API key，例如 /ai 密钥 设置 codex sk-...。"),
    ("apikey", "设置"): CommandSpec("provider_config.set_api_key", "更新 API key。"),
    ("api_key", "set"): CommandSpec("provider_config.set_api_key", "更新 API key。"),
    ("api-key", "set"): CommandSpec("provider_config.set_api_key", "更新 API key。"),
    ("代理", "设置"): CommandSpec("provider_config.set_base_url", "更新第三方代理地址，例如 /ai 代理 设置 claude-code https://...。"),
    ("第三方代理", "设置"): CommandSpec("provider_config.set_base_url", "更新第三方代理地址。"),
    ("base_url", "set"): CommandSpec("provider_config.set_base_url", "更新第三方代理地址。"),
    ("base-url", "set"): CommandSpec("provider_config.set_base_url", "更新第三方代理地址。"),
    ("配置", "查看"): CommandSpec("provider_config.show", "查看当前模型、代理地址和 API key 是否已配置。"),
    ("config", "show"): CommandSpec("provider_config.show", "查看当前模型、代理地址和 API key 是否已配置。"),
    ("停止",): CommandSpec("cancel", "记录取消标记；当前版本不会强制终止已启动的 provider。"),
    ("取消",): CommandSpec("cancel", "记录取消标记；当前版本不会强制终止已启动的 provider。"),
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
    ("凭据", "授权"): CommandSpec("credential.grant", "为凭据句柄授权 agent/action，需确认。", True),
    ("工作区", "列表"): CommandSpec("workspace.list", "列出可用工作区。"),
    ("工作区", "使用"): CommandSpec("workspace.select", "选择工作区。"),
    ("工作区", "创建"): CommandSpec("workspace.create", "创建工作区，需确认。", True),
    ("提供商", "列表"): CommandSpec("provider.list", "列出 Claude Code、Codex 和其他适配器状态。"),
    ("提供商", "使用"): CommandSpec("provider.select", "切换默认提供商。"),
    ("扩展", "列表"): CommandSpec("extension.list", "列出可选技能和扩展。"),
    ("工具", "列表"): CommandSpec("tool.list", "列出可选 CLI 工具。"),
    ("mcp", "列表"): CommandSpec("mcp.list", "列出 MCP 扩展。"),
    ("说明", "生成"): CommandSpec("description.generate", "生成中文说明元数据，不修改工具代码。"),
    ("说明",): CommandSpec("description.list", "显示命令和功能说明元数据。"),
    ("说明", "编辑"): CommandSpec("description.edit", "记录中文说明元数据编辑请求，需确认。", True),
}


def parse_command(raw_text: str, allow_bare: bool = False) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("/ai"):
        rest = text[3:].strip()
    elif allow_bare and text == "/":
        rest = "帮助"
    elif allow_bare and text.startswith("/"):
        rest = text[1:].strip()
    else:
        if allow_bare and text:
            return {
                "status": "accepted",
                "canonical_action": "task.run",
                "args": {"prompt": text, "tail": []},
                "requires_confirmation": False,
            }
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
