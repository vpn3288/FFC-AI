from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.providers import CLAUDE_MODEL_FALLBACKS, codex_command, invoke_claude, invoke_codex
from ai_remote_runner.providers import discover_codex
from ai_remote_runner.budget import BudgetLedger


class ProviderTests(unittest.TestCase):
    def test_codex_command_uses_supported_approval_config(self) -> None:
        def supported(_: list[str], *needles: str) -> bool:
            flags = {
                "--dangerously-bypass-approvals-and-sandbox",
                "--dangerously-bypass-hook-trust",
                "--ignore-rules",
                "--add-dir",
                "--skip-git-repo-check",
            }
            return all(needle in flags for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=supported):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn("network_access=\"enabled\"", command)
        self.assertIn("shell_environment_policy.inherit=all", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("--dangerously-bypass-hook-trust", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--add-dir", command)
        self.assertEqual(command[command.index("--add-dir") + 1], "/")
        self.assertNotIn("--sandbox", command)
        self.assertNotIn("--ask-for-approval", command)
        self.assertNotIn("--ignore-user-config", command)

    def test_codex_command_includes_instruction_prompt(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=True):
            command = codex_command("do work", Path("/tmp/work"), Path("/tmp/out.txt"), "# Global Instructions\nBe careful")
        self.assertIn("# Global Instructions\nBe careful\n\n# User Task\ndo work", command)

    def test_claude_model_fallbacks_are_configured(self) -> None:
        self.assertIn("claude-opus-4-7", CLAUDE_MODEL_FALLBACKS)
        self.assertIn("claude-opus-4-8-thinking", CLAUDE_MODEL_FALLBACKS)

    def test_codex_discovery_reports_approval_config_key(self) -> None:
        capabilities = discover_codex()["capabilities"]
        self.assertIn("approval_config_available", capabilities)
        self.assertIn("sandbox_available", capabilities)
        self.assertIn("full_access_available", capabilities)

    def test_codex_command_fails_when_full_access_flag_unavailable(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "codex_full_access_unavailable"):
                codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))

    def test_codex_command_uses_danger_full_access_when_bypass_flag_unavailable(self) -> None:
        def sandbox_only(_: list[str], *needles: str) -> bool:
            return all(needle == "--sandbox" for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=sandbox_only):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "danger-full-access")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_codex_command_skips_git_repo_trust_check_when_supported(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=True):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("--skip-git-repo-check", command)
        self.assertLess(command.index("--skip-git-repo-check"), command.index("--cd"))

    def test_invoke_codex_returns_stderr_when_last_message_missing(self) -> None:
        import subprocess
        import tempfile

        completed = subprocess.CompletedProcess(["codex"], 1, stdout="", stderr="not inside a trusted directory")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed),
            ):
                result = invoke_codex("noop", Path(tmp), ledger, timeout_seconds=1, reserved_usd=0.01)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output_text, "not inside a trusted directory")

    def test_invoke_codex_timeout_releases_run(self) -> None:
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", side_effect=subprocess.TimeoutExpired(["codex"], 1)),
            ):
                result = invoke_codex("noop", Path(tmp), BudgetLedger(Path(tmp) / "ledger.json"), timeout_seconds=1, reserved_usd=0.01)
            self.assertIn(result.status, {"completed", "failed", "timeout"})

    def test_claude_permission_scope_uses_edit_template_and_actual_cost(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.02}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch.dict("os.environ", {"CLAUDE_MODEL": ""}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1, permission_scope="edit")
            self.assertEqual(result.status, "completed")
            self.assertIn("Read,Grep,Glob,Edit,Write", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--permission-mode") + 1], "bypassPermissions")
            self.assertNotIn("--model", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["input"], "prompt")
            self.assertNotIn("prompt", run.call_args.args[0])
            self.assertAlmostEqual(ledger.load()["daily_used_usd_estimate"], 0.02)

    def test_claude_full_permission_scope_uses_bypass_template(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.02}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1, permission_scope="full")
            command = run.call_args.args[0]
            self.assertEqual(result.status, "completed")
            self.assertIn("--permission-mode", command)
            self.assertEqual(command[command.index("--permission-mode") + 1], "bypassPermissions")
            self.assertIn("--dangerously-skip-permissions", command)
            self.assertIn("--add-dir", command)
            self.assertEqual(command[command.index("--add-dir") + 1], "/")
            self.assertEqual(command[command.index("--tools") + 1], "default")
            self.assertNotIn("--no-session-persistence", command)

    def test_invoke_claude_uses_model_only_when_explicitly_configured(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict("os.environ", {"CLAUDE_MODEL": "sonnet"}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1)
            command = run.call_args.args[0]
            self.assertIn("--model", command)
            self.assertEqual(command[command.index("--model") + 1], "sonnet")

    def test_invoke_claude_chat_template_disables_all_tools_when_requested(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1, permission_scope="chat")
            command = run.call_args.args[0]
            self.assertIn("--tools", command)
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertNotIn("--disallowedTools", command)

    def test_invoke_claude_treats_empty_success_as_error(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": ""}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed):
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "empty_output")
            self.assertIn("空内容", result.output_text)
            self.assertEqual(ledger.load()["runs"][result.run_id]["status"], "empty_output")

    def test_invoke_claude_retries_empty_chat_output_once(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        ok = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "你好，我在。", "total_cost_usd": 0.02}), stderr="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, ok]) as run:
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1, emit=events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_text, "你好，我在。")
            self.assertEqual(run.call_count, 2)
            self.assertIn("上一轮 Claude Code 返回了空字符串", run.call_args.kwargs["input"])
            self.assertIn("收到，我在。", run.call_args.kwargs["input"])
            retry_command = run.call_args.args[0]
            self.assertEqual(retry_command[retry_command.index("--max-budget-usd") + 1], "0.09")
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.03)
            self.assertTrue(any(event.get("phase") == "warning" and "重试" in event.get("public_message_zh", "") for event in events))

    def test_invoke_claude_short_empty_chat_uses_safe_fallback(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=empty) as run:
                result = invoke_claude("你好", Path(tmp), "instructions", ledger, reserved_usd=0.1, emit=events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_text, "收到，我在。")
            self.assertEqual(run.call_count, 1)
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.01)
            self.assertTrue(any("安全回复" in event.get("public_message_zh", "") for event in events))

    def test_invoke_claude_does_not_retry_empty_output_without_cost_visibility(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": ""}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=empty) as run:
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "empty_output")
            self.assertEqual(run.call_count, 1)

    def test_invoke_claude_records_reserved_cost_when_retry_cost_missing(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        ok_without_cost = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "收到，我在。"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, ok_without_cost]):
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "completed")
            self.assertEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.1)

    def test_invoke_claude_records_reserved_cost_when_retry_times_out(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, subprocess.TimeoutExpired(["claude"], 1)]):
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "timeout")
            self.assertEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.1)

    def test_provider_probe_timeout_marks_unavailable(self) -> None:
        import subprocess

        with (
            patch("ai_remote_runner.providers.shutil.which", return_value="/bin/tool"),
            patch("ai_remote_runner.providers.subprocess.run", side_effect=subprocess.TimeoutExpired(["tool"], 1)),
        ):
            from ai_remote_runner.providers import _version

            self.assertEqual(_version("tool")["error"], "probe_timeout")


if __name__ == "__main__":
    unittest.main()
