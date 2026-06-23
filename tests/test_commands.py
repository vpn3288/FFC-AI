from __future__ import annotations

import unittest

from ai_remote_runner.commands import command_index, parse_command


class CommandTests(unittest.TestCase):
    def test_ai_status_maps_to_canonical_action(self) -> None:
        result = parse_command("/ai 状态")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["canonical_action"], "status")

    def test_bare_command_rejected_by_default(self) -> None:
        result = parse_command("/状态")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"], "command_must_start_with_/ai")

    def test_bare_command_allowed_when_enabled(self) -> None:
        result = parse_command("/状态", allow_bare=True)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["canonical_action"], "status")

    def test_compound_command_requires_confirmation(self) -> None:
        result = parse_command("/ai 全局 替换")
        self.assertEqual(result["canonical_action"], "global_instructions.set")
        self.assertTrue(result["requires_confirmation"])

    def test_compound_command_preserves_tail_args(self) -> None:
        result = parse_command("/ai 全局 追加 hello world")
        self.assertEqual(result["canonical_action"], "global_instructions.append")
        self.assertEqual(result["args"]["tail"], ["hello", "world"])

    def test_context_aliases_match_documented_commands(self) -> None:
        self.assertEqual(parse_command("/ai context")["canonical_action"], "context_status")
        self.assertEqual(parse_command("/ai 整理上下文")["canonical_action"], "compact_context")
        self.assertEqual(parse_command("/ai 对话")["canonical_action"], "conversation_status")

    def test_documented_aliases_match_parser(self) -> None:
        cases = {
            "/ai 索引": "command_index",
            "/ai new": "new_conversation",
            "/ai 开启新对话": "new_conversation",
            "/ai continue": "continue_conversation",
            "/ai mode new_each": "set_policy_new_each_request",
            "/ai mode continue": "set_policy_continue",
            "/ai 说明": "description.list",
            "/ai 说明 编辑 demo": "description.edit",
            "/ai 凭据 授权 credential://demo codex ssh.exec 60": "credential.grant",
            "/ai shell pwd": "local.exec",
            "/ai 执行 pwd": "local.exec",
            "/ai 命令 执行 pwd": "local.exec",
            "/ai 脚本 运行 scripts/smoke-test.sh": "local.exec",
            "/ai codex doctor": "codex.doctor",
            "/ai 子agent状态": "codex.subagent_status.show",
            "/ai 子agent状态 开启": "codex.subagent_status.enable",
            "/ai 子agent 关闭": "codex.subagent_status.disable",
            "/ai 子 agent 状态 开启": "codex.subagent_status.enable",
            "/ai jsonl 关闭": "codex.subagent_status.disable",
            "/ai JSONL 开启": "codex.subagent_status.enable",
            "/ai 定时继续": "auto_continue.status",
            "/ai 定时继续 设置 300": "auto_continue.set",
            "/ai 定时继续 关闭": "auto_continue.disable",
            "/ai 强行停止": "task.force_stop",
            "/ai 强制停止": "task.force_stop",
            "/ai 全部停止": "task.force_stop",
        }
        for raw, action in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_command(raw)["canonical_action"], action)

    def test_permission_mode_commands_are_direct_switches(self) -> None:
        edit = parse_command("/ai 编辑模式 开启")
        shell = parse_command("/ai shell模式 开启")
        chat = parse_command("/ai 聊天模式 开启")
        full = parse_command("/ai 完全访问 开启")
        self.assertEqual(edit["canonical_action"], "set_permission_edit")
        self.assertFalse(edit["requires_confirmation"])
        self.assertFalse(chat["requires_confirmation"])
        self.assertFalse(shell["requires_confirmation"])
        self.assertEqual(full["canonical_action"], "set_permission_full")
        self.assertFalse(full["requires_confirmation"])

    def test_model_and_provider_config_commands_parse(self) -> None:
        cases = {
            "/ai 模型 列表": "model.list",
            "/ai GPT模型 设置 vscode gpt-5.5": "model.select_gpt",
            "/ai Claude模型 设置 claude-code claude-opus-4-8": "model.select_claude",
            "/ai 切换 GPT 模型 codex gpt": "model.select_gpt",
            "/ai 切换Claude模型 vscode claude": "model.select_claude",
            "/ai 模型 使用 vscode gpt-5.5": "model.select",
            "/ai 密钥 设置 codex sk-testvalue1234567890": "provider_config.set_api_key",
            "/ai 代理 设置 claude-code https://proxy.example": "provider_config.set_base_url",
            "/ai 配置 查看": "provider_config.show",
            "/ai CC Switch 状态": "cc_switch.status",
            "/ai cc switch 状态": "cc_switch.status",
            "/ai CC Switch 密钥 设置 codex sk-testvalue1234567890": "cc_switch.set_api_key",
            "/ai cc switch apikey 设置 codex sk-testvalue1234567890": "cc_switch.set_api_key",
            "/ai CC Switch 代理 设置 claude-code https://proxy.example": "cc_switch.set_base_url",
            "/ai CC Switch 模型 设置 codex gpt-5.5": "cc_switch.set_model",
            "/ai CC Switch GPT模型 设置 vscode gpt-5.5": "cc_switch.set_gpt_model",
            "/ai CC Switch Claude模型 设置 claude-code claude-opus-4-8": "cc_switch.set_claude_model",
            "/ai 预算 设置 1.00": "budget.set_task_reserved",
            "/ai 轮数 设置 无限": "claude.max_turns.set",
            "/ai 重试 设置 3": "claude.retry.set",
        }
        for raw, action in cases.items():
            with self.subTest(raw=raw):
                parsed = parse_command(raw)
                self.assertEqual(parsed["canonical_action"], action)
                self.assertFalse(parsed["requires_confirmation"])

    def test_bare_slash_returns_command_index_when_enabled(self) -> None:
        result = parse_command("/", allow_bare=True)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["canonical_action"], "command_index")

    def test_index_has_chinese_descriptions(self) -> None:
        rows = command_index()
        self.assertGreater(len(rows), 10)
        self.assertTrue(all(row["description_zh"] for row in rows))
        self.assertTrue(all("enabled" in row and "provider" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
