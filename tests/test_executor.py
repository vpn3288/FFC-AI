from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.commands import parse_command
from ai_remote_runner.commands import COMMANDS
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

    def test_command_matrix_has_no_unsupported_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            for parts, spec in COMMANDS.items():
                parsed = parse_command("/ai " + " ".join(parts))
                envelope = {"request_id": ".".join(parts), "raw_text": "/ai " + " ".join(parts)}
                if spec.requires_confirmation:
                    envelope["confirmed"] = True
                response = execute(parsed, envelope, runtime)
                error = response.get("error") or {}
                self.assertNotEqual(error.get("code"), "unsupported_action", parts)

    def test_status_includes_current_workspace_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 工作区 使用 demo"), {"request_id": "sw1", "raw_text": "/ai 工作区 使用 demo"}, runtime)
            response = execute(parse_command("/ai 状态"), {"request_id": "sw2", "raw_text": "/ai 状态"}, runtime)
            self.assertEqual(response["data"]["current_workspace"], "demo")

    def test_status_includes_recent_run_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.events.emit({"run_id": "run-active", "provider": "claude-code", "phase": "running", "public_message_zh": "仍在运行", "time": 123})
            response = execute(parse_command("/ai 状态"), {"request_id": "sw-events", "raw_text": "/ai 状态"}, runtime)
            active = next(item for item in response["data"]["recent_runs"] if item["run_id"] == "run-active")
            self.assertEqual(active["phase"], "running")

    def test_status_reads_core_ready_from_install_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.state.mkdir(parents=True)
            (runtime.state / "install-manifest.json").write_text(
                json.dumps(
                    {
                        "core_ready": True,
                        "core_ready_status": "validated",
                        "bridge_loopback_validated": True,
                        "integration_ready_status": "validated",
                        "mattermost_command_validated": True,
                    }
                ),
                encoding="utf-8",
            )
            response = execute(parse_command("/ai 状态"), {"request_id": "sw3", "raw_text": "/ai 状态"}, runtime)
            self.assertTrue(response["data"]["core_ready"])
            self.assertEqual(response["data"]["core_ready_status"], "validated")
            self.assertTrue(response["data"]["bridge_loopback_validated"])
            self.assertEqual(response["data"]["integration_ready_status"], "validated")
            self.assertTrue(response["data"]["mattermost_command_validated"])

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

    def test_compact_context_checks_provider_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.contexts.add_exchange("default", "claude-code", "old prompt", "old answer")
            parsed = parse_command("/ai 压缩")
            status = [{"provider": "claude-code", "capabilities": {"new_conversation": False, "continue_conversation": False}}]
            with patch("ai_remote_runner.executor.provider_status", return_value=status):
                response = execute(parsed, {"request_id": "r3b", "raw_text": "/ai 压缩"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "provider_compaction_unsupported")

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

    def test_invalid_workspace_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai 工作区 创建 ../../etc")
            response = execute(parsed, {"request_id": "wbad", "raw_text": "/ai 工作区 创建 ../../etc", "confirmed": True}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "invalid_workspace_id")

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
            context_file = runtime.state / "contexts" / "default.claude-code.json"
            self.assertTrue(context_file.exists())

    def test_task_prompt_always_requests_visible_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "你好"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "收到", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "prompt1", "raw_text": "你好"}, runtime)
            provider_prompt = invoke.call_args.args[0]
            self.assertIn("# 当前用户消息", provider_prompt)
            self.assertIn("你好", provider_prompt)
            self.assertIn("不要返回空内容", provider_prompt)

    def test_task_run_default_budget_reservation_is_chat_sized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "r8b", "raw_text": "do work"}, runtime)
            self.assertEqual(invoke.call_args.kwargs["reserved_usd"], 0.20)

    def test_default_permission_scope_is_full_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "perm-default", "raw_text": "do work"}, runtime)
            self.assertEqual(invoke.call_args.kwargs["permission_scope"], "full")

    def test_continue_policy_injects_recent_transcript_into_provider_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.contexts.add_exchange("default", "claude-code", "instructions", "remember alpha", "alpha saved")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "what did I ask?"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "hist1", "raw_text": "what did I ask?"}, runtime)
            provider_prompt = invoke.call_args.args[0]
            self.assertIn("remember alpha", provider_prompt)
            self.assertIn("alpha saved", provider_prompt)
            self.assertIn("不要返回空内容", provider_prompt)

    def test_conversation_command_enables_long_memory_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 每次新对话"), {"request_id": "conv1", "raw_text": "/ai 每次新对话"}, runtime)
            response = execute(parse_command("/ai 对话"), {"request_id": "conv2", "raw_text": "/ai 对话"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["data"]["policy"]["policy"], "continue")
            self.assertEqual(response["data"]["auto_compact_threshold_percent"], 80)

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

    def test_instruction_secret_scan_allows_handles_and_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.instructions.write("global", "Use {{credential://api/prod}} and AI_BRIDGE_SHARED_SECRET=<generated>")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                response = execute(parsed, {"request_id": "sec2", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "accepted")

    def test_budget_preflight_blocks_provider_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            data = runtime.ledger.load()
            data["daily_usd_limit"] = 0.5
            data["monthly_usd_limit"] = 0.5
            runtime.ledger.save(data)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                response = execute(parsed, {"request_id": "bud1", "raw_text": "do work", "reserved_usd": 1.0}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "daily_budget_exceeded")
            invoke.assert_not_called()

    def test_management_only_task_rejects_before_queued_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            with patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": ""}, clear=False):
                response = execute(parsed, {"request_id": "mgmt-task", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "ai_provider_not_configured")
            events_path = runtime.state / "events.jsonl"
            self.assertFalse(events_path.exists(), events_path.read_text(encoding="utf-8") if events_path.exists() else "")

    def test_model_and_provider_config_commands_update_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            env = {
                "AI_TOOL_HOME": str(root / "root-home"),
                "CODEX_HOME": str(root / "root-home" / ".codex"),
                "AI_RUNNER_PROVIDERS": "",
            }
            fake_key = "sk-" + "a" * 24
            with patch.dict("os.environ", env, clear=False):
                model = execute(parse_command("/ai 模型 使用 vscode claude-opus-4-6"), {"request_id": "model-vscode", "raw_text": "/ai 模型 使用 vscode claude-opus-4-6"}, runtime)
                key = execute(parse_command(f"/ai 密钥 设置 codex {fake_key}"), {"request_id": "key-codex", "raw_text": "/ai 密钥 设置 codex <redacted>"}, runtime)
                proxy = execute(parse_command("/ai 代理 设置 claude-code https://cc-vibe.com"), {"request_id": "proxy-claude", "raw_text": "/ai 代理 设置 claude-code https://cc-vibe.com"}, runtime)
                budget = execute(parse_command("/ai 预算 设置 1.25"), {"request_id": "budget-set", "raw_text": "/ai 预算 设置 1.25"}, runtime)

            self.assertEqual(model["status"], "accepted")
            self.assertEqual(key["status"], "accepted")
            self.assertNotIn(fake_key, json.dumps(key, ensure_ascii=False))
            self.assertEqual(proxy["data"]["base_url"], "https://cc-vibe.com")
            self.assertEqual(budget["data"]["task_reserved_usd"], 1.25)
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("VSCODE_CLAUDE_MODEL=claude-opus-4-6", config_env)
            self.assertIn("CLAUDE_MODEL=claude-opus-4-6", config_env)
            self.assertIn("AI_TASK_RESERVED_USD=1.25", config_env)
            settings = json.loads((root / "root-home" / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://cc-vibe.com")
            self.assertEqual(settings["env"]["CLAUDE_MODEL"], "claude-opus-4-6")
            auth = json.loads((root / "root-home" / ".codex" / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["OPENAI_API_KEY"], fake_key)

    def test_model_list_uses_fallback_when_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            with patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "codex", "CODEX_HOME": str(Path(tmp) / "codex-home")}, clear=False):
                response = execute(parse_command("/ai 模型 列表 codex"), {"request_id": "models", "raw_text": "/ai 模型 列表 codex"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["data"]["source"], "fallback_unverified")
            self.assertIn("gpt-5.5", response["data"]["models"])

    def test_context_hard_stop_includes_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            old_state = runtime.contexts.load("default", "claude-code", limit=100)
            old_state["context_used_tokens"] = 94
            old_state["context_limit_tokens"] = 100
            runtime.contexts.path("default", "claude-code").write_text(json.dumps(old_state), encoding="utf-8")
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

    def test_codex_rejects_non_full_permission_scope_without_misleading_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 提供商 使用 codex"), {"request_id": "codex-scope-1", "raw_text": "/ai 提供商 使用 codex"}, runtime)
            execute(parse_command("/ai 聊天模式 开启"), {"request_id": "codex-scope-2", "raw_text": "/ai 聊天模式 开启"}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            with patch("ai_remote_runner.executor.invoke_codex") as invoke:
                response = execute(parsed, {"request_id": "codex-scope-3", "raw_text": "do work"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "codex_permission_scope_unsupported")
            invoke.assert_not_called()

    def test_provider_switch_keeps_provider_specific_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai 新对话"), {"request_id": "nc1", "raw_text": "/ai 新对话", "provider": "claude-code"}, runtime)
            claude_id = runtime.load_policy()["provider_conversations"]["claude-code"]

            execute(parse_command("/ai 提供商 使用 codex"), {"request_id": "ps-codex", "raw_text": "/ai 提供商 使用 codex"}, runtime)
            execute(parse_command("/ai 新对话"), {"request_id": "nc2", "raw_text": "/ai 新对话"}, runtime)
            policy = runtime.load_policy()
            codex_id = policy["provider_conversations"]["codex"]
            self.assertNotEqual(claude_id, codex_id)

            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "continue"}, "requires_confirmation": False}
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                response = execute(parsed, {"request_id": "task-claude", "raw_text": "continue", "provider": "claude-code"}, runtime)
            self.assertEqual(response["data"]["conversation_id"], claude_id)

    def test_default_provider_conversation_is_initialized_before_other_provider_new_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "remember default"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                first = execute(parsed, {"request_id": "default-claude", "raw_text": "remember default", "provider": "claude-code"}, runtime)
            self.assertEqual(first["data"]["conversation_id"], "default")
            self.assertEqual(runtime.load_policy()["provider_conversations"]["claude-code"], "default")

            execute(parse_command("/ai 提供商 使用 codex"), {"request_id": "ps-codex2", "raw_text": "/ai 提供商 使用 codex"}, runtime)
            execute(parse_command("/ai 新对话"), {"request_id": "new-codex2", "raw_text": "/ai 新对话"}, runtime)

            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                second = execute(parsed, {"request_id": "return-claude", "raw_text": "continue", "provider": "claude-code"}, runtime)
            self.assertEqual(second["data"]["conversation_id"], "default")

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
            old_state = runtime.contexts.load("default", "claude-code", limit=1000)
            old_state["context_used_tokens"] = 790
            old_state["context_limit_tokens"] = 1000
            runtime.contexts.path("default", "claude-code").write_text(json.dumps(old_state), encoding="utf-8")

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

    def test_new_each_request_policy_skips_auto_compact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.save_policy({"policy": "new_each_request", "conversation_id": "default", "auto_compact_enabled": True, "permission_scope": "chat"})
            old_state = runtime.contexts.load("default", "claude-code", limit=1000)
            old_state["context_used_tokens"] = 790
            old_state["context_limit_tokens"] = 1000
            runtime.contexts.path("default", "claude-code").write_text(json.dumps(old_state), encoding="utf-8")

            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "x"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with (
                patch("ai_remote_runner.executor.invoke_claude", return_value=fake),
                patch("ai_remote_runner.context_store.ContextStore.compact") as compact,
            ):
                response = execute(parsed, {"request_id": "neac1", "raw_text": "x", "conversation_id": "default"}, runtime)
            self.assertEqual(response["status"], "accepted")
            compact.assert_not_called()

    def test_compacted_summary_is_injected_into_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.contexts.add_exchange("default", "claude-code", "important previous context")
            compacted = runtime.contexts.compact("default", "claude-code")
            runtime.save_policy({"policy": "continue", "conversation_id": compacted["new_conversation_id"], "auto_compact_enabled": False, "permission_scope": "chat"})
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "continue"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "sum1", "raw_text": "continue"}, runtime)
            self.assertIn("important previous context", invoke.call_args.args[2])

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

    def test_shell_permission_scope_runs_without_per_task_confirmation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai shell模式 开启"), {"request_id": "sh1", "raw_text": "/ai shell模式 开启"}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "run shell"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                response = execute(parsed, {"request_id": "sh2", "raw_text": "run shell"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(invoke.call_args.kwargs["permission_scope"], "shell")

    def test_shell_permission_scope_can_require_confirmation_when_env_enables_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            execute(parse_command("/ai shell模式 开启"), {"request_id": "sh3", "raw_text": "/ai shell模式 开启"}, runtime)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "run shell"}, "requires_confirmation": False}
            with patch.dict("os.environ", {"AI_REQUIRE_SHELL_CONFIRMATION": "1"}, clear=False):
                response = execute(parsed, {"request_id": "sh4", "raw_text": "run shell"}, runtime)
            self.assertEqual(response["status"], "needs_confirmation")
            self.assertEqual(response["data"]["permission_scope"], "shell")

    def test_full_access_permission_command_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            response = execute(parse_command("/ai 完全访问 开启"), {"request_id": "full1", "raw_text": "/ai 完全访问 开启"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(runtime.load_policy()["permission_scope"], "full")


if __name__ == "__main__":
    unittest.main()
