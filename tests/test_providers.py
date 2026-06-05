from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.providers import CLAUDE_MODEL_FALLBACKS, codex_command, invoke_claude, invoke_codex
from ai_remote_runner.providers import discover_codex
from ai_remote_runner.budget import BudgetLedger


class ProviderTests(unittest.TestCase):
    def test_codex_command_uses_supported_approval_config(self) -> None:
        command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
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

    def test_codex_command_omits_sandbox_when_flag_unavailable(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=False):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertNotIn("--sandbox", command)

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
            self.assertNotIn("--model", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["input"], "prompt")
            self.assertNotIn("prompt", run.call_args.args[0])
            self.assertAlmostEqual(ledger.load()["daily_used_usd_estimate"], 0.02)

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

    def test_invoke_claude_chat_template_disables_all_tools(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1)
            command = run.call_args.args[0]
            self.assertIn("--tools", command)
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertNotIn("--disallowedTools", command)

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
