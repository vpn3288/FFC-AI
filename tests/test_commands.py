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

    def test_index_has_chinese_descriptions(self) -> None:
        rows = command_index()
        self.assertGreater(len(rows), 10)
        self.assertTrue(all(row["description_zh"] for row in rows))
        self.assertTrue(all("enabled" in row and "provider" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
