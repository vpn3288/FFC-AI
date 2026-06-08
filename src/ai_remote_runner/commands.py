from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CommandSpec:
    canonical_action: str
    description_zh: str
    template_zh: str = ""
    example_zh: str = ""
    requires_confirmation: bool = False
    native: str = "runner"


COMMANDS: dict[tuple[str, ...], CommandSpec] = {
    ("状态",): CommandSpec(
        "status",
        "显示当前运行、上下文、预算、工作区和提供商状态。",
        "/ai 状态",
        "/ai 状态"
    ),
    ("帮助",): CommandSpec(
        "command_index",
        "显示中文命令索引。",
        "/ai 帮助",
        "/ai 帮助"
    ),
    # 已废弃别名：命令、索引（请使用"帮助"）
    ("确认",): CommandSpec(
        "confirm",
        "确认待执行的高风险操作。",
        "/ai 确认 <令牌>",
        "/ai 确认 abc123def456"
    ),
    ("功能",): CommandSpec(
        "feature_index",
        "显示提供商、工具、扩展和能力状态。",
        "/ai 功能",
        "/ai 功能"
    ),
    ("执行",): CommandSpec(
        "local.exec",
        "在当前工作区执行本机shell命令。",
        "/ai 执行 <命令>",
        "/ai 执行 ls -la"
    ),
    ("脚本", "运行"): CommandSpec(
        "local.exec",
        "运行脚本或命令。",
        "/ai 脚本 运行 <脚本路径>",
        "/ai 脚本 运行 scripts/smoke-test.sh"
    ),
    ("诊断",): CommandSpec(
        "codex.doctor",
        "运行本机codex doctor诊断。",
        "/ai 诊断",
        "/ai 诊断"
    ),
    ("压缩",): CommandSpec(
        "compact_context",
        "压缩当前上下文，必要时创建摘要并开启新会话。",
        "/ai 压缩",
        "/ai 压缩"
    ),
    ("新对话",): CommandSpec(
        "new_conversation",
        "创建新的提供商会话。",
        "/ai 新对话",
        "/ai 新对话"
    ),
    ("对话",): CommandSpec(
        "conversation_status",
        "显示并启用长期持续对话模式。",
        "/ai 对话",
        "/ai 对话"
    ),
    ("继续",): CommandSpec(
        "continue_conversation",
        "继续当前会话或使用摘要模拟继续。",
        "/ai 继续",
        "/ai 继续"
    ),
    ("每次新对话",): CommandSpec(
        "set_policy_new_each_request",
        "将策略改为每次请求创建新会话。",
        "/ai 每次新对话",
        "/ai 每次新对话"
    ),
    ("持续对话",): CommandSpec(
        "set_policy_continue",
        "将策略改为持续复用当前会话。",
        "/ai 持续对话",
        "/ai 持续对话"
    ),
    ("上下文",): CommandSpec(
        "context_status",
        "显示上下文用量、阈值和压缩状态。",
        "/ai 上下文",
        "/ai 上下文"
    ),
    ("自动压缩", "开启"): CommandSpec(
        "set_auto_compact_enabled",
        "达到上下文预警阈值时自动压缩。",
        "/ai 自动压缩 开启",
        "/ai 自动压缩 开启"
    ),
    ("自动压缩", "关闭"): CommandSpec(
        "set_auto_compact_disabled",
        "关闭达到上下文预警阈值时的自动压缩。",
        "/ai 自动压缩 关闭",
        "/ai 自动压缩 关闭"
    ),
    ("聊天模式", "开启"): CommandSpec(
        "set_permission_chat",
        "仅允许对话，不允许文件编辑或shell。",
        "/ai 聊天模式 开启",
        "/ai 聊天模式 开启"
    ),
    ("编辑模式", "开启"): CommandSpec(
        "set_permission_edit",
        "允许文件编辑工具。",
        "/ai 编辑模式 开启",
        "/ai 编辑模式 开启"
    ),
    ("终端模式", "开启"): CommandSpec(
        "set_permission_shell",
        "允许shell命令工具。",
        "/ai 终端模式 开启",
        "/ai 终端模式 开启"
    ),
    ("完全访问", "开启"): CommandSpec(
        "set_permission_full",
        "允许Claude Code、VSCode或Codex使用完整工具权限、shell和文件访问。",
        "/ai 完全访问 开启",
        "/ai 完全访问 开启"
    ),
    # 已废弃别名：最高权限、root权限（请使用"完全访问"）
    ("预算",): CommandSpec(
        "budget_status",
        "显示每日、每月和当前运行预算状态。",
        "/ai 预算",
        "/ai 预算"
    ),
    ("预算", "设置"): CommandSpec(
        "budget.set_task_reserved",
        "设置单次任务预留预算；0/无限表示不传原生预算上限。",
        "/ai 预算 设置 <金额或无限>",
        "/ai 预算 设置 0.5"
    ),
    ("预算", "单次"): CommandSpec(
        "budget.set_task_reserved",
        "设置单次任务预留预算。",
        "/ai 预算 单次 <金额或无限>",
        "/ai 预算 单次 无限"
    ),
    ("轮数", "设置"): CommandSpec(
        "claude.max_turns.set",
        "设置Claude后端最大工具轮数；可加claude-code/vscode，0/无限表示不传原生--max-turns。",
        "/ai 轮数 设置 <工具名> <数量或无限>",
        "/ai 轮数 设置 claude-code 20"
    ),
    ("重试", "设置"): CommandSpec(
        "claude.retry.set",
        "设置Claude后端网关/API临时错误自动重试次数；可加claude-code/vscode，范围0-5。",
        "/ai 重试 设置 <工具名> <次数>",
        "/ai 重试 设置 vscode 3"
    ),
    ("模型",): CommandSpec(
        "model.list",
        "查询当前API key可用模型；接口不可用时显示本地fallback列表。",
        "/ai 模型 <工具名>",
        "/ai 模型 codex"
    ),
    ("模型", "列表"): CommandSpec(
        "model.list",
        "查询当前API key可用模型；可加claude-code/codex/vscode。",
        "/ai 模型 列表 <工具名>",
        "/ai 模型 列表 claude-code"
    ),
    ("开源模型", "设置"): CommandSpec(
        "model.select_gpt",
        "切换GPT/开源模型。",
        "/ai 开源模型 设置 <工具名> <模型名>",
        "/ai 开源模型 设置 codex gpt-5.5"
    ),
    ("闭源模型", "设置"): CommandSpec(
        "model.select_claude",
        "切换Claude/闭源模型。",
        "/ai 闭源模型 设置 <工具名> <模型名>",
        "/ai 闭源模型 设置 claude-code claude-opus-4-8"
    ),
    ("模型", "设置"): CommandSpec(
        "model.select",
        "兼容旧命令；推荐改用 /ai 开源模型 设置 或 /ai 闭源模型 设置。",
        "/ai 模型 设置 <工具名> <模型名>",
        "/ai 模型 设置 vscode gpt-4o"
    ),
    ("密钥", "设置"): CommandSpec(
        "provider_config.set_api_key",
        "更新API key。",
        "/ai 密钥 设置 <工具名> <API_KEY>",
        "/ai 密钥 设置 codex sk-xxxxxxxxxxxxxxxx"
    ),
    ("代理", "设置"): CommandSpec(
        "provider_config.set_base_url",
        "更新第三方代理地址。",
        "/ai 代理 设置 <工具名> <API地址>",
        "/ai 代理 设置 claude-code https://api.example.com"
    ),
    ("配置", "查看"): CommandSpec(
        "provider_config.show",
        "查看当前模型、代理地址和API key是否已配置。",
        "/ai 配置 查看 <工具名>",
        "/ai 配置 查看 vscode"
    ),
    ("停止",): CommandSpec(
        "cancel",
        "记录取消标记；当前版本不会强制终止已启动的provider。",
        "/ai 停止",
        "/ai 停止"
    ),
    ("取消",): CommandSpec(
        "cancel",
        "记录取消标记。",
        "/ai 取消",
        "/ai 取消"
    ),
    ("全局", "查看"): CommandSpec(
        "global_instructions.show",
        "显示global.md哈希和预览。",
        "/ai 全局 查看",
        "/ai 全局 查看"
    ),
    ("全局", "设置"): CommandSpec(
        "global_instructions.set",
        "替换global.md，需确认。",
        "/ai 全局 设置 <文本>",
        "/ai 全局 设置 请使用简洁的代码风格",
        True
    ),
    ("全局", "追加"): CommandSpec(
        "global_instructions.append",
        "追加global.md并创建快照。",
        "/ai 全局 追加 <文本>",
        "/ai 全局 追加 优先使用类型提示"
    ),
    ("全局", "回滚"): CommandSpec(
        "global_instructions.rollback",
        "回滚global.md到快照，需确认。",
        "/ai 全局 回滚 <快照ID>",
        "/ai 全局 回滚 snapshot-20260607",
        True
    ),
    ("全局", "应用"): CommandSpec(
        "global_instructions.apply",
        "将global.md应用到选定提供商。",
        "/ai 全局 应用",
        "/ai 全局 应用"
    ),
    ("项目", "查看"): CommandSpec(
        "project_instructions.show",
        "显示当前工作区project.md哈希和预览。",
        "/ai 项目 查看",
        "/ai 项目 查看"
    ),
    ("项目", "设置"): CommandSpec(
        "project_instructions.set",
        "替换project.md，需确认。",
        "/ai 项目 设置 <文本>",
        "/ai 项目 设置 本项目使用Python 3.10+",
        True
    ),
    ("项目", "追加"): CommandSpec(
        "project_instructions.append",
        "追加project.md并创建快照。",
        "/ai 项目 追加 <文本>",
        "/ai 项目 追加 使用pytest进行测试"
    ),
    ("项目", "回滚"): CommandSpec(
        "project_instructions.rollback",
        "回滚project.md到快照，需确认。",
        "/ai 项目 回滚 <快照ID>",
        "/ai 项目 回滚 snapshot-20260607",
        True
    ),
    ("项目", "应用"): CommandSpec(
        "project_instructions.apply",
        "将project.md应用到选定提供商。",
        "/ai 项目 应用",
        "/ai 项目 应用"
    ),
    ("凭据", "添加"): CommandSpec(
        "credential.add",
        "创建凭据句柄，密文不得进入聊天记录。",
        "/ai 凭据 添加 <句柄名>",
        "/ai 凭据 添加 github-token",
        True
    ),
    ("凭据", "列表"): CommandSpec(
        "credential.list",
        "列出凭据句柄和非秘密元数据。",
        "/ai 凭据 列表",
        "/ai 凭据 列表"
    ),
    ("凭据", "测试"): CommandSpec(
        "credential.test",
        "使用句柄运行授权测试，需确认。",
        "/ai 凭据 测试 <句柄名>",
        "/ai 凭据 测试 github-token",
        True
    ),
    ("凭据", "删除"): CommandSpec(
        "credential.delete",
        "删除凭据句柄，需确认。",
        "/ai 凭据 删除 <句柄名>",
        "/ai 凭据 删除 old-token",
        True
    ),
    ("凭据", "授权"): CommandSpec(
        "credential.grant",
        "为凭据句柄授权agent/action，需确认。",
        "/ai 凭据 授权 <句柄名> <agent> <action> <duration>",
        "/ai 凭据 授权 github-token codex push 1h",
        True
    ),
    ("工作区", "列表"): CommandSpec(
        "workspace.list",
        "列出可用工作区。",
        "/ai 工作区 列表",
        "/ai 工作区 列表"
    ),
    ("工作区", "使用"): CommandSpec(
        "workspace.select",
        "选择工作区。",
        "/ai 工作区 使用 <工作区名>",
        "/ai 工作区 使用 demo"
    ),
    ("工作区", "创建"): CommandSpec(
        "workspace.create",
        "创建工作区，需确认。",
        "/ai 工作区 创建 <工作区名>",
        "/ai 工作区 创建 my-project",
        True
    ),
    ("提供商", "列表"): CommandSpec(
        "provider.list",
        "列出Claude Code、Codex和其他适配器状态。",
        "/ai 提供商 列表",
        "/ai 提供商 列表"
    ),
    ("提供商", "使用"): CommandSpec(
        "provider.select",
        "切换默认提供商。",
        "/ai 提供商 使用 <工具名>",
        "/ai 提供商 使用 codex"
    ),
    ("扩展", "列表"): CommandSpec(
        "extension.list",
        "列出可选技能和扩展。",
        "/ai 扩展 列表",
        "/ai 扩展 列表"
    ),
    ("工具", "列表"): CommandSpec(
        "tool.list",
        "列出可选CLI工具。",
        "/ai 工具 列表",
        "/ai 工具 列表"
    ),
    ("MCP", "列表"): CommandSpec(
        "mcp.list",
        "列出MCP扩展。",
        "/ai MCP 列表",
        "/ai MCP 列表"
    ),
    ("说明", "生成"): CommandSpec(
        "description.generate",
        "生成中文说明元数据，不修改工具代码。",
        "/ai 说明 生成 <ID>",
        "/ai 说明 生成 cmd-123"
    ),
    ("说明",): CommandSpec(
        "description.list",
        "显示命令和功能说明元数据。",
        "/ai 说明",
        "/ai 说明"
    ),
    ("说明", "编辑"): CommandSpec(
        "description.edit",
        "记录中文说明元数据编辑请求，需确认。",
        "/ai 说明 编辑 <ID> <内容>",
        "/ai 说明 编辑 cmd-123 更新的说明文本",
        True
    ),
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
            "template_zh": spec.template_zh,
            "example_zh": spec.example_zh,
            "requires_confirmation": spec.requires_confirmation,
            "implemented_by": spec.native,
            "enabled": True,
            "provider": "runner",
            "native": spec.native == "native",
            "emulated": spec.native == "runner",
            "unsupported": False,
            "installed": True,
        }
        for parts, spec in COMMANDS.items()
    ]
