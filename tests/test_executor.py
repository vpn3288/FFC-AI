from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.commands import parse_command
from ai_remote_runner.commands import COMMANDS
from ai_remote_runner.executor import RunnerRuntime, execute
from ai_remote_runner.providers import ProviderResult
from ai_remote_runner.runtime_config import apply_base_url, apply_model, config_summary


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict("os.environ", {}, clear=False)
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        for key in (
            "AI_RUNNER_PROVIDERS",
            "AI_PERMISSION_SCOPE",
            "AI_REQUIRE_SHELL_CONFIRMATION",
            "AI_TASK_RESERVED_USD",
            "TELEGRAM_RESERVED_USD",
            "OPENAI_API_KEY",
            "CODEX_BASE_URL",
            "CODEX_MODEL",
            "CODEX_HOME",
            "CODEX_SUBAGENT_STATUS_EVENTS",
            "AI_CODEX_HOME",
            "AI_TOOL_HOME",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_MODEL",
            "VSCODE_CLAUDE_API_RETRY_ATTEMPTS",
            "VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS",
            "VSCODE_CLAUDE_MAX_TURNS",
            "VSCODE_CLAUDE_MODEL",
        ):
            os.environ.pop(key, None)

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

    def test_status_includes_telegram_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            runtime.state.mkdir(parents=True)
            (runtime.state / "telegram-tasks.json").write_text(
                json.dumps({"task-1": {"task_id": "task-1", "provider": "codex", "done": False, "started_at": 123}}),
                encoding="utf-8",
            )
            response = execute(parse_command("/ai 状态"), {"request_id": "telegram-tasks", "raw_text": "/ai 状态"}, runtime)
            self.assertEqual(response["data"]["telegram_tasks"][0]["task_id"], "task-1")

    def test_local_exec_runs_command_and_emits_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai shell printf local-ok")
            response = execute(parsed, {"request_id": "local-exec", "raw_text": "/ai shell printf local-ok"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["data"]["exit_code"], 0)
            self.assertIn("local-ok", response["data"]["output"])
            events = (runtime.state / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("running_command", events)
            self.assertIn("done", events)

    def test_local_exec_registers_process_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            completed = subprocess.CompletedProcess("printf registered", 0, "registered", "")
            with patch("ai_remote_runner.executor.run_registered", return_value=completed) as run:
                response = execute(parse_command("/ai shell printf registered"), {"request_id": "local-registered"}, runtime)

            self.assertEqual(response["status"], "accepted")
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs["action"], "local.exec")

    def test_local_exec_reports_nonzero_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = parse_command("/ai shell bash -lc 'echo bad >&2; exit 7'")
            response = execute(parsed, {"request_id": "local-fail", "raw_text": "/ai shell bash -lc 'echo bad >&2; exit 7'"}, runtime)
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "local_exec_exit_7")
            self.assertIn("bad", response["error"]["detail"])

    def test_codex_doctor_uses_overridable_local_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            with patch.dict("os.environ", {"AI_CODEX_DOCTOR_COMMAND": "printf codex-doctor-ok"}, clear=False):
                response = execute(parse_command("/ai codex doctor"), {"request_id": "codex-doctor", "raw_text": "/ai codex doctor"}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertIn("codex-doctor-ok", response["data"]["output"])

    def test_codex_base_url_runtime_config_uses_current_codex_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            codex_home = root / "codex-home"
            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                result = apply_base_url(runtime.state, "codex", "https://proxy.example/v1")
                config = (codex_home / "config.toml").read_text(encoding="utf-8")
                summary = config_summary("codex")
            self.assertEqual(result["base_url"], "https://proxy.example/v1")
            self.assertIn('model_provider = "openai"', config)
            self.assertIn('openai_base_url = "https://proxy.example/v1"', config)
            self.assertIn("[sandbox_workspace_write]", config)
            self.assertIn("network_access = true", config)
            self.assertNotIn('network_access = "enabled"', config)
            self.assertNotIn("dangerously_bypass_approvals_and_sandbox", config)
            self.assertNotIn("[model_providers.OpenAI]", config)
            self.assertEqual(summary["base_url"], "https://proxy.example/v1")

    def test_codex_base_url_runtime_config_updates_active_custom_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        'model_provider = "proxy"',
                        'model = "gpt-5.5"',
                        'openai_base_url = "https://old-openai.example/v1"',
                        "",
                        "[model_providers.other]",
                        'base_url = "https://other.example/v1"',
                        "",
                        "[model_providers.proxy]",
                        'name = "proxy"',
                        'base_url = "https://old-proxy.example/v1"',
                        'wire_api = "responses"',
                        'env_key = "OPENAI_API_KEY"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                result = apply_base_url(runtime.state, "codex", "https://new-proxy.example/v1")
                config = (codex_home / "config.toml").read_text(encoding="utf-8")
                summary = config_summary("codex")

            self.assertEqual(result["base_url"], "https://new-proxy.example/v1")
            self.assertIn('openai_base_url = "https://new-proxy.example/v1"', config)
            self.assertIn('[model_providers.proxy]\nname = "proxy"\nbase_url = "https://new-proxy.example/v1"', config)
            self.assertIn('[model_providers.other]\nbase_url = "https://other.example/v1"', config)
            self.assertEqual(summary["base_url"], "https://new-proxy.example/v1")

    def test_codex_base_url_runtime_config_uses_top_level_model_provider_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        'model_provider = "openai"',
                        'openai_base_url = "https://old-openai.example/v1"',
                        "",
                        "[model_providers.proxy]",
                        'model_provider = "not-top-level"',
                        'base_url = "https://proxy.example/v1"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                apply_base_url(runtime.state, "codex", "https://new-openai.example/v1")
                config = (codex_home / "config.toml").read_text(encoding="utf-8")
                summary = config_summary("codex")

            self.assertIn('openai_base_url = "https://new-openai.example/v1"', config)
            self.assertIn('base_url = "https://proxy.example/v1"', config)
            self.assertNotIn("[model_providers.not-top-level]", config)
            self.assertEqual(summary["base_url"], "https://new-openai.example/v1")

    def test_codex_config_summary_uses_active_provider_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                "\n".join(
                    [
                        'model_provider = "proxy"',
                        'model = "gpt-5.5"',
                        'openai_base_url = "https://openai.example/v1"',
                        "",
                        "[model_providers.other]",
                        'base_url = "https://other.example/v1"',
                        "",
                        "[model_providers.proxy]",
                        'base_url = "https://proxy.example/v1"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"CODEX_HOME": str(codex_home)}, clear=False):
                summary = config_summary("codex")

            self.assertEqual(summary["model"], "gpt-5.5")
            self.assertEqual(summary["base_url"], "https://proxy.example/v1")

    def test_vscode_config_summary_does_not_fall_back_to_claude_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            with patch.dict("os.environ", {"AI_REMOTE_STATE": str(runtime.state), "CLAUDE_MODEL": "claude-only"}, clear=False):
                self.assertEqual(config_summary("vscode")["model"], "")
                apply_model(runtime.state, "vscode", "vscode-model")
                self.assertEqual(config_summary("vscode")["model"], "vscode-model")

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

    def test_task_run_default_budget_reservation_is_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                execute(parsed, {"request_id": "r8b", "raw_text": "do work"}, runtime)
            self.assertEqual(invoke.call_args.kwargs["reserved_usd"], 0.0)

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

    def test_unlimited_budget_preflight_allows_provider_after_daily_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            data = runtime.ledger.load()
            data["daily_usd_limit"] = 0.5
            data["monthly_usd_limit"] = 0.5
            data["daily_used_usd_estimate"] = 5.0
            data["monthly_used_usd_estimate"] = 50.0
            runtime.ledger.save(data)
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
            fake = ProviderResult("run", "claude-code", "completed", "done", None, 0)
            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                response = execute(parsed, {"request_id": "bud-unlimited", "raw_text": "do work", "reserved_usd": 0.0}, runtime)
            self.assertEqual(response["status"], "accepted")
            self.assertEqual(invoke.call_args.kwargs["reserved_usd"], 0.0)

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

    def test_codex_subagent_status_commands_update_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")

            disabled = execute(parse_command("/ai 子agent状态 关闭"), {"request_id": "subagent-off", "raw_text": "/ai 子agent状态 关闭"}, runtime)
            shown_disabled = execute(parse_command("/ai 子agent状态"), {"request_id": "subagent-show-off", "raw_text": "/ai 子agent状态"}, runtime)
            self.assertEqual(disabled["status"], "accepted")
            self.assertFalse(disabled["data"]["enabled"])
            self.assertEqual(shown_disabled["data"]["status_zh"], "关闭")
            self.assertEqual(os.environ["CODEX_SUBAGENT_STATUS_EVENTS"], "0")
            self.assertIn("CODEX_SUBAGENT_STATUS_EVENTS=0", (runtime.state / "config.env").read_text(encoding="utf-8"))

            enabled = execute(parse_command("/ai 子agent 开启"), {"request_id": "subagent-on", "raw_text": "/ai 子agent 开启"}, runtime)
            shown_enabled = execute(parse_command("/ai 子 agent 状态"), {"request_id": "subagent-show-on", "raw_text": "/ai 子 agent 状态"}, runtime)

            self.assertEqual(os.environ["CODEX_SUBAGENT_STATUS_EVENTS"], "1")
            self.assertTrue(enabled["data"]["enabled"])
            self.assertEqual(shown_enabled["data"]["status_zh"], "开启")
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("CODEX_SUBAGENT_STATUS_EVENTS=1", config_env)

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
                model = execute(parse_command("/ai GPT模型 设置 vscode gpt-5.5"), {"request_id": "model-vscode", "raw_text": "/ai GPT模型 设置 vscode gpt-5.5"}, runtime)
                key = execute(parse_command(f"/ai 密钥 设置 codex {fake_key}"), {"request_id": "key-codex", "raw_text": "/ai 密钥 设置 codex <redacted>"}, runtime)
                proxy = execute(parse_command("/ai 代理 设置 claude-code https://cc-vibe.com"), {"request_id": "proxy-claude", "raw_text": "/ai 代理 设置 claude-code https://cc-vibe.com"}, runtime)
                budget = execute(parse_command("/ai 预算 设置 1.25"), {"request_id": "budget-set", "raw_text": "/ai 预算 设置 1.25"}, runtime)
                max_turns = execute(parse_command("/ai 轮数 设置 40"), {"request_id": "max-turns-set", "raw_text": "/ai 轮数 设置 40"}, runtime)
                retry = execute(parse_command("/ai 重试 设置 3"), {"request_id": "retry-set", "raw_text": "/ai 重试 设置 3"}, runtime)

            self.assertEqual(model["status"], "accepted")
            self.assertEqual(model["message_zh"], "GPT 模型已更新")
            self.assertEqual(model["data"]["model_family"], "gpt")
            self.assertEqual(model["data"]["config_key"], "VSCODE_CLAUDE_MODEL")
            self.assertEqual(key["status"], "accepted")
            self.assertNotIn(fake_key, json.dumps(key, ensure_ascii=False))
            self.assertEqual(proxy["data"]["base_url"], "https://cc-vibe.com")
            self.assertEqual(budget["data"]["task_reserved_usd"], 1.25)
            self.assertEqual(max_turns["data"]["claude_max_turns"], 40)
            self.assertEqual(retry["data"]["claude_api_retry_attempts"], 3)
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("VSCODE_CLAUDE_MODEL=gpt-5.5", config_env)
            self.assertNotIn("CLAUDE_MODEL=gpt-5.5", config_env.splitlines())
            self.assertIn("AI_TASK_RESERVED_USD=1.25", config_env)
            self.assertIn("CLAUDE_MAX_TURNS=40", config_env)
            self.assertIn("CLAUDE_API_RETRY_ATTEMPTS=3", config_env)
            unlimited = execute(parse_command("/ai 预算 设置 无限"), {"request_id": "budget-unlimited", "raw_text": "/ai 预算 设置 无限"}, runtime)
            self.assertEqual(unlimited["data"]["task_reserved_usd"], 0.0)
            self.assertTrue(unlimited["data"]["budget_unlimited"])
            unlimited_turns = execute(parse_command("/ai 轮数 设置 无限"), {"request_id": "turns-unlimited", "raw_text": "/ai 轮数 设置 无限"}, runtime)
            self.assertEqual(unlimited_turns["data"]["claude_max_turns"], 0)
            self.assertTrue(unlimited_turns["data"]["max_turns_unlimited"])
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("AI_TASK_RESERVED_USD=0", config_env)
            self.assertIn("CLAUDE_MAX_TURNS=0", config_env)
            settings = json.loads((root / "root-home" / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://cc-vibe.com")
            self.assertNotIn("CLAUDE_MODEL", settings["env"])
            auth = json.loads((root / "root-home" / ".codex" / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["OPENAI_API_KEY"], fake_key)

    def test_gpt_and_claude_model_commands_normalize_short_aliases_per_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            env = {
                "AI_TOOL_HOME": str(root / "root-home"),
                "CODEX_HOME": str(root / "root-home" / ".codex"),
            }
            with patch.dict("os.environ", env, clear=False):
                claude = execute(parse_command("/ai Claude模型 设置 claude claude"), {"request_id": "model-claude", "raw_text": "/ai Claude模型 设置 claude claude"}, runtime)
                vscode_gpt = execute(parse_command("/ai GPT模型 设置 vscode gpt"), {"request_id": "model-vscode-gpt", "raw_text": "/ai GPT模型 设置 vscode gpt"}, runtime)
                vscode_claude = execute(parse_command("/ai Claude模型 设置 vscode claude"), {"request_id": "model-vscode-claude", "raw_text": "/ai Claude模型 设置 vscode claude"}, runtime)
                codex_gpt = execute(parse_command("/ai GPT模型 设置 codex codex"), {"request_id": "model-codex-gpt", "raw_text": "/ai GPT模型 设置 codex codex"}, runtime)
                codex_claude = execute(parse_command("/ai Claude模型 设置 codex claude-opus-4-8"), {"request_id": "model-codex-claude", "raw_text": "/ai Claude模型 设置 codex claude-opus-4-8"}, runtime)

            self.assertEqual(claude["status"], "accepted")
            self.assertEqual(claude["message_zh"], "Claude 模型已更新")
            self.assertEqual(claude["data"]["target"], "claude-code")
            self.assertEqual(claude["data"]["requested_model"], "claude")
            self.assertEqual(claude["data"]["model"], "opus")
            self.assertEqual(claude["data"]["config_key"], "CLAUDE_MODEL")
            self.assertEqual(vscode_gpt["message_zh"], "GPT 模型已更新")
            self.assertEqual(vscode_gpt["data"]["model"], "gpt-5.5")
            self.assertEqual(vscode_gpt["data"]["config_key"], "VSCODE_CLAUDE_MODEL")
            self.assertIn("ANTHROPIC_BASE_URL", vscode_gpt["data"]["note_zh"])
            self.assertEqual(vscode_claude["data"]["model"], "opus")
            self.assertEqual(vscode_claude["data"]["model_family"], "claude")
            self.assertEqual(codex_gpt["data"]["model"], "gpt-5.5")
            self.assertEqual(codex_gpt["data"]["config_key"], "CODEX_MODEL")
            self.assertEqual(codex_claude["data"]["model"], "claude-opus-4-8")
            self.assertEqual(codex_claude["data"]["model_family"], "claude")
            self.assertIn("CODEX_BASE_URL", codex_claude["data"]["note_zh"])
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("CLAUDE_MODEL=opus", config_env)
            self.assertIn("VSCODE_CLAUDE_MODEL=opus", config_env)
            self.assertIn("CODEX_MODEL=claude-opus-4-8", config_env)
            settings = json.loads((root / "root-home" / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["CLAUDE_MODEL"], "opus")

    def test_model_family_commands_reject_obvious_wrong_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            gpt_wrong = execute(parse_command("/ai GPT模型 设置 claude-code claude"), {"request_id": "model-wrong-gpt", "raw_text": "/ai GPT模型 设置 claude-code claude"}, runtime)
            claude_wrong = execute(parse_command("/ai Claude模型 设置 codex gpt"), {"request_id": "model-wrong-claude", "raw_text": "/ai Claude模型 设置 codex gpt"}, runtime)

            self.assertEqual(gpt_wrong["status"], "error")
            self.assertEqual(gpt_wrong["error"]["code"], "wrong_model_family")
            self.assertIn("Claude 模型请使用", gpt_wrong["error"]["detail"])
            self.assertEqual(claude_wrong["status"], "error")
            self.assertEqual(claude_wrong["error"]["code"], "wrong_model_family")
            self.assertIn("GPT 模型请使用", claude_wrong["error"]["detail"])

    def test_legacy_model_select_still_works_for_existing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            env = {"AI_TOOL_HOME": str(root / "root-home")}
            with patch.dict("os.environ", env, clear=False):
                response = execute(parse_command("/ai 模型 使用 claude-code code claude-opus-4-8"), {"request_id": "legacy-model", "raw_text": "/ai 模型 使用 claude-code code claude-opus-4-8"}, runtime)

            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["message_zh"], "模型已更新")
            self.assertEqual(response["data"]["model"], "claude-opus-4-8")

    def test_model_select_handles_split_provider_aliases_without_polluting_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            env = {
                "AI_TOOL_HOME": str(root / "root-home"),
                "AI_REMOTE_STATE": str(runtime.state),
            }
            with patch.dict("os.environ", env, clear=False):
                split_claude = execute(
                    parse_command("/ai 模型 使用 claude code claude-opus-4-8"),
                    {"request_id": "model-split-claude", "raw_text": "/ai 模型 使用 claude code claude-opus-4-8"},
                    runtime,
                )
                legacy_prefix = execute(
                    parse_command("/ai 模型 使用 claude-code code claude-opus-4-8"),
                    {"request_id": "model-legacy-prefix", "raw_text": "/ai 模型 使用 claude-code code claude-opus-4-8"},
                    runtime,
                )
                invalid = execute(
                    parse_command("/ai 模型 使用 claude-code bad model"),
                    {"request_id": "model-invalid", "raw_text": "/ai 模型 使用 claude-code bad model"},
                    runtime,
                )

            self.assertEqual(split_claude["status"], "accepted")
            self.assertEqual(split_claude["data"]["target"], "claude-code")
            self.assertEqual(split_claude["data"]["model"], "claude-opus-4-8")
            self.assertEqual(legacy_prefix["status"], "accepted")
            self.assertEqual(legacy_prefix["data"]["model"], "claude-opus-4-8")
            self.assertEqual(invalid["status"], "error")
            self.assertEqual(invalid["error"]["code"], "invalid_model")
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("CLAUDE_MODEL=claude-opus-4-8", config_env)
            self.assertNotIn("CLAUDE_MODEL=code claude-opus-4-8", config_env)

    def test_vscode_provider_selection_invokes_vscode_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            with patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "vscode"}, clear=False):
                selected = execute(parse_command("/ai 提供商 使用 code"), {"request_id": "ps-vscode", "raw_text": "/ai 提供商 使用 code"}, runtime)
                self.assertEqual(selected["status"], "accepted")
                self.assertEqual(selected["data"]["provider"], "vscode")

                parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "do work"}, "requires_confirmation": False}
                fake = ProviderResult("run", "vscode", "completed", "done", None, 0)
                with patch("ai_remote_runner.executor.invoke_vscode", return_value=fake) as invoke:
                    response = execute(parsed, {"request_id": "task-vscode", "raw_text": "do work"}, runtime)

            self.assertEqual(response["status"], "accepted")
            self.assertEqual(response["data"]["provider"], "vscode")
            invoke.assert_called_once()
            self.assertTrue((runtime.state / "contexts" / "default.vscode.json").exists())

    def test_claude_controls_can_target_vscode_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            max_turns = execute(parse_command("/ai 轮数 设置 vscode 无限"), {"request_id": "turns-vscode", "raw_text": "/ai 轮数 设置 vscode 无限"}, runtime)
            retry = execute(parse_command("/ai 重试 设置 code 3"), {"request_id": "retry-vscode", "raw_text": "/ai 重试 设置 code 3"}, runtime)

            self.assertEqual(max_turns["status"], "accepted")
            self.assertEqual(max_turns["data"]["target"], "vscode")
            self.assertEqual(max_turns["data"]["config_key"], "VSCODE_CLAUDE_MAX_TURNS")
            self.assertEqual(retry["data"]["config_key"], "VSCODE_CLAUDE_API_RETRY_ATTEMPTS")
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("VSCODE_CLAUDE_MAX_TURNS=0", config_env)
            self.assertIn("VSCODE_CLAUDE_API_RETRY_ATTEMPTS=3", config_env)

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

    def test_provider_config_rejects_wrong_key_family_and_bad_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            with patch.dict("os.environ", {"CODEX_HOME": str(root / "codex-home")}, clear=False):
                wrong_key = execute(
                    parse_command("/ai 密钥 设置 codex sk-ant-" + "x" * 24),
                    {"request_id": "bad-key", "raw_text": "/ai 密钥 设置 codex sk-ant-redacted"},
                    runtime,
                )
                bad_url = execute(
                    parse_command("/ai 代理 设置 codex ftp://proxy.example/v1"),
                    {"request_id": "bad-url", "raw_text": "/ai 代理 设置 codex ftp://proxy.example/v1"},
                    runtime,
                )

            self.assertEqual(wrong_key["status"], "error")
            self.assertEqual(wrong_key["error"]["code"], "wrong_api_key_family")
            self.assertEqual(bad_url["status"], "error")
            self.assertEqual(bad_url["error"]["code"], "invalid_base_url")

    def test_cc_switch_commands_update_live_configs_and_record_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            env = {
                "AI_TOOL_HOME": str(root / "root-home"),
                "CODEX_HOME": str(root / "root-home" / ".codex"),
                "CC_SWITCH_HOME": str(root / ".cc-switch"),
            }
            fake_key = "sk-" + "c" * 24
            with patch.dict("os.environ", env, clear=False):
                status = execute(parse_command("/ai CC Switch 状态"), {"request_id": "ccs-status", "raw_text": "/ai CC Switch 状态"}, runtime)
                key = execute(parse_command(f"/ai CC Switch 密钥 设置 codex {fake_key}"), {"request_id": "ccs-key", "raw_text": "/ai CC Switch 密钥 设置 codex <redacted>"}, runtime)
                proxy = execute(parse_command("/ai CC Switch 代理 设置 codex https://proxy.example/v1"), {"request_id": "ccs-url", "raw_text": "/ai CC Switch 代理 设置 codex https://proxy.example/v1"}, runtime)
                model = execute(parse_command("/ai CC Switch GPT模型 设置 vscode gpt"), {"request_id": "ccs-model", "raw_text": "/ai CC Switch GPT模型 设置 vscode gpt"}, runtime)
                bad = execute(parse_command("/ai CC Switch 密钥 设置 codex sk-ant-" + "x" * 24), {"request_id": "ccs-bad", "raw_text": "/ai CC Switch 密钥 设置 codex sk-ant-redacted"}, runtime)

            self.assertEqual(status["status"], "accepted")
            self.assertFalse(status["data"]["db_write_supported"])
            self.assertEqual(key["status"], "accepted")
            self.assertNotIn(fake_key, json.dumps(key, ensure_ascii=False))
            self.assertEqual(proxy["data"]["base_url"], "https://proxy.example/v1")
            self.assertEqual(model["data"]["model"], "gpt-5.5")
            self.assertEqual(model["data"]["config_key"], "VSCODE_CLAUDE_MODEL")
            self.assertEqual(bad["status"], "error")
            self.assertEqual(bad["error"]["code"], "wrong_api_key_family")
            auth = json.loads((root / "root-home" / ".codex" / "auth.json").read_text(encoding="utf-8"))
            self.assertEqual(auth["OPENAI_API_KEY"], fake_key)
            config_env = (runtime.state / "config.env").read_text(encoding="utf-8")
            self.assertIn("CODEX_BASE_URL=https://proxy.example/v1", config_env)
            self.assertIn("VSCODE_CLAUDE_MODEL=gpt-5.5", config_env)
            sync = json.loads((runtime.state / "cc-switch-sync.json").read_text(encoding="utf-8"))
            self.assertTrue(sync["providers"]["codex"]["live_config_written"])
            self.assertFalse(sync["providers"]["codex"]["cc_switch_db_written"])

    def test_auto_continue_command_persists_chat_scoped_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            response = execute(
                parse_command("/ai 定时继续 设置 5m"),
                {"request_id": "auto-1", "raw_text": "/ai 定时继续 设置 5m", "chat_id": "123"},
                runtime,
            )
            self.assertEqual(response["status"], "accepted")
            schedule = response["data"]["schedule"]
            self.assertEqual(schedule["interval_seconds"], 300)
            self.assertEqual(schedule["prompt"], "继续")
            saved = json.loads((runtime.state / "telegram-auto-continue.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["chats"]["123"]["enabled"])

            status = execute(parse_command("/ai 定时继续"), {"request_id": "auto-2", "raw_text": "/ai 定时继续", "chat_id": "123"}, runtime)
            self.assertEqual(status["data"]["schedule"]["interval_seconds"], 300)

            disabled = execute(parse_command("/ai 定时继续 关闭"), {"request_id": "auto-3", "raw_text": "/ai 定时继续 关闭", "chat_id": "123"}, runtime)
            self.assertFalse(disabled["data"]["schedule"]["enabled"])

    def test_auto_continue_requires_telegram_chat_and_valid_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            no_chat = execute(parse_command("/ai 定时继续 设置 300"), {"request_id": "auto-no-chat"}, runtime)
            too_short = execute(
                parse_command("/ai 定时继续 设置 10"),
                {"request_id": "auto-short", "raw_text": "/ai 定时继续 设置 10", "chat_id": "123"},
                runtime,
            )
            self.assertEqual(no_chat["error"]["code"], "telegram_chat_required")
            self.assertEqual(too_short["error"]["code"], "interval_out_of_range")

    def test_force_stop_delegates_to_registered_process_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            fake_result = {"matched": 0, "terminated": 0, "killed_after_grace": 0, "missing": 0, "records": [], "remaining": []}
            with patch("ai_remote_runner.executor.terminate_active_processes", return_value=fake_result) as terminate:
                response = execute(parse_command("/ai 强行停止"), {"request_id": "stop-1", "raw_text": "/ai 强行停止"}, runtime)
            self.assertEqual(response["status"], "accepted")
            terminate.assert_called_once_with(runtime.state, target_run_id=None, grace_seconds=1.0)
            self.assertTrue((runtime.state / "stop-request.json").exists())


if __name__ == "__main__":
    unittest.main()
