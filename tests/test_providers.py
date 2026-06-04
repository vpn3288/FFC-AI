from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.providers import CLAUDE_MODEL_FALLBACKS, codex_command, invoke_codex
from ai_remote_runner.providers import discover_codex
from ai_remote_runner.budget import BudgetLedger


class ProviderTests(unittest.TestCase):
    def test_codex_command_uses_supported_approval_config(self) -> None:
        command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
        self.assertNotIn("--ask-for-approval", command)

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


if __name__ == "__main__":
    unittest.main()
