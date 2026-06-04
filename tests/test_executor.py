from __future__ import annotations

import json
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

    def test_task_run_rejects_secret_like_instruction_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            fake_key = "sk-" + "a" * 24
            runtime.instructions.write("global", f"api_key={fake_key}")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                response = execute(parsed, {"request_id": "sec1", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "secrets_in_instructions")
            invoke.assert_not_called()

    def test_budget_preflight_blocks_provider_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            data = runtime.ledger.load()
            data["daily_usd_limit"] = 0.5
            data["monthly_usd_limit"] = 0.5
            runtime.ledger.save(data)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                response = execute(parsed, {"request_id": "bud1", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "daily_budget_exceeded")
            invoke.assert_not_called()

    def test_context_hard_stop_includes_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            old_state = runtime.contexts.load("default", "claude-code", limit=100)
            old_state["context_used_tokens"] = 94
            old_state["context_limit_tokens"] = 100
            runtime.contexts.path("default").write_text(json.dumps(old_state), encoding="utf-8")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            response = execute(parsed, {"request_id": "ctx1", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "context_hard_stop")
            self.assertIn("/ai 压缩", response["error"]["detail"])

    def test_provider_selection_persists_and_drives_task_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            selected = execute(parse_command("/ai 提供商 使用 codex"), {"request_id": "ps1", "raw_text": "/ai 提供商 使用 codex"}, runtime)
            self.assertEqual(selected["status"], "accepted")

            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "codex", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_codex", return_value=fake) as invoke:
                response = execute(parsed, {"request_id": "ps2", "raw_text": "do work"}, runtime)
            self.assertEqual(response["data"]["provider"], "codex")
            invoke.assert_called_once()

    def test_workspace_selection_persists_and_drives_task_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 工作区 使用 demo"), {"request_id": "ws1", "raw_text": "/ai 工作区 使用 demo"}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                response = execute(parsed, {"request_id": "ws2", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(invoke.call_args.args[1], runtime.workspaces / "demo")

    def test_auto_compact_policy_persists_and_updates_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 自动压缩 开启"), {"request_id": "ac1", "raw_text": "/ai 自动压缩 开启"}, runtime)
            old_state = runtime.contexts.load("default", "claude-code", limit=100)
            old_state["context_used_tokens"] = 79
            old_state["context_limit_tokens"] = 100
            runtime.contexts.path("default").write_text(json.dumps(old_state), encoding="utf-8")

            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "x"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                response = execute(parsed, {"request_id": "ac2", "raw_text": "x"}, runtime)
            self.assertNotEqual(response["data"]["conversation_id"], "default")
            self.assertEqual(runtime.load_policy()["conversation_id"], response["data"]["conversation_id"])

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

    def test_instruction_apply_and_credential_add_are_implemented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            applied = execute(parse_command("/ai 全局 应用"), {"request_id": "ia1", "raw_text": "/ai 全局 应用"}, runtime)
            self.assertEqual(applied["status"], "accepted")
            self.assertEqual(applied["data"]["scope"], "global")

            pending = execute(
                parse_command("/ai 凭据 添加 credential://github/main"),
                {"request_id": "ca1", "raw_text": "/ai 凭据 添加 credential://github/main", "confirmed": True},
                runtime,
            )
            self.assertEqual(pending["status"], "accepted")
            self.assertEqual(pending["data"]["secret_material"], "never send secret material in chat")

    def test_permission_scope_persists_and_reaches_claude_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 编辑模式 开启"), {"request_id": "pm1", "raw_text": "/ai 编辑模式 开启", "confirmed": True}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "edit work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "pm2", "raw_text": "edit work"}, runtime)
            self.assertEqual(invoke.call_args.kwargs["permission_scope"], "edit")


if __name__ == "__main__":
    unittest.main()
