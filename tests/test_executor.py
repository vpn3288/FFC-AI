from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.commands import parse_command
from ai_remote_runner.executor import RunnerRuntime, execute
from ai_remote_runner.providers import ProviderResult


class ExecutorTests(unittest.TestCase):
    def test_status_command_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 状态")
            response = execute(parsed, {"request_id": "r1", "raw_text": "/ai 状态"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertIn("providers", response["data"])

    def test_instruction_append_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 全局 追加 hello")
            response = execute(parsed, {"request_id": "r2", "raw_text": "/ai 全局 追加 hello"}, runtime)
            self.assertEqual(response["status"], "accepted")
            shown = runtime.instructions.show("global")
            self.assertEqual(shown["preview"], "hello")

    def test_compact_context_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.contexts.add_exchange("default", "claude-code", "old prompt", "old answer")
            parsed = parse_command("/ai 压缩")
            response = execute(parsed, {"request_id": "r3", "raw_text": "/ai 压缩"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertIn("summary_artifact", response["data"])
            self.assertIn("old prompt", Path(response["data"]["summary_artifact"]).read_text(encoding="utf-8"))

    def test_missing_snapshot_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 全局 回滚 missing")
            response = execute(parsed, {"request_id": "r4", "raw_text": "/ai 全局 回滚 missing", "confirmed": True}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "snapshot_not_found")

    def test_confirmation_required_blocks_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 全局 替换 hello")
            response = execute(parsed, {"request_id": "r5", "raw_text": "/ai 全局 替换 hello"}, runtime)
            self.assertEqual(response["status"], "needs_confirmation")

    def test_workspace_create_requires_confirmation_then_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 工作区 创建 demo")
            blocked = execute(parsed, {"request_id": "r6", "raw_text": "/ai 工作区 创建 demo"}, runtime)
            self.assertEqual(blocked["status"], "needs_confirmation")
            response = execute(parsed, {"request_id": "r7", "raw_text": "/ai 工作区 创建 demo", "confirmed": True}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertTrue((runtime.workspaces / "demo").exists())

    def test_task_run_invokes_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                response = execute(parsed, {"request_id": "r8", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["data"]["output"], "done")
            self.assertIn("global_md_sha256", response["data"])
            self.assertIn("project_md_sha256", response["data"])
            invoke.assert_called_once()
            context_file = runtime.state / "contexts" / "default.json"
            self.assertTrue(context_file.exists())

    def test_new_each_request_policy_changes_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 每次新对话"), {"request_id": "p1", "raw_text": "/ai 每次新对话"}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                first = execute(parsed, {"request_id": "p2", "raw_text": "do work"}, runtime)
                second = execute(parsed, {"request_id": "p3", "raw_text": "do work"}, runtime)
            self.assertNotEqual(first["data"]["conversation_id"], second["data"]["conversation_id"])

    def test_extension_and_description_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            ext = execute(parse_command("/ai 扩展 列表"), {"request_id": "e1", "raw_text": "/ai 扩展 列表"}, runtime)
            self.assertEqual(ext["status"], "accepted")
            self.assertGreater(len(ext["data"]["items"]), 0)
            desc = execute(parse_command("/ai 说明 生成 filesystem"), {"request_id": "e2", "raw_text": "/ai 说明 生成 filesystem"}, runtime)
            self.assertEqual(desc["status"], "accepted")
            self.assertEqual(desc["data"]["id"], "filesystem")


if __name__ == "__main__":
    unittest.main()
