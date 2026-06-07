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
            "/ai 模型 使用 vscode gpt-5.5": "model.select",
            "/ai 密钥 设置 codex sk-testvalue1234567890": "provider_config.set_api_key",
            "/ai 代理 设置 claude-code https://proxy.example": "provider_config.set_base_url",
            "/ai 配置 查看": "provider_config.show",
            "/ai 预算 设置 1.00": "budget.set_task_reserved",
            "/ai 轮数 设置 无限": "claude.max_turns.set",
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
