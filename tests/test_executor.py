from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_remote_runner.commands import parse_command
from ai_remote_runner.executor import RunnerRuntime, execute


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
            parsed = parse_command("/ai 压缩")
            response = execute(parsed, {"request_id": "r3", "raw_text": "/ai 压缩"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertIn("summary_artifact", response["data"])

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


if __name__ == "__main__":
    unittest.main()
