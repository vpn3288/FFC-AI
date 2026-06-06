from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner import cli
from ai_remote_runner.providers import ProviderResult


class CliTests(unittest.TestCase):
    def test_provider_smoke_reads_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_file = root / "prompt.txt"
            prompt_file.write_text("PROMPT_FROM_FILE", encoding="utf-8")
            workspace = root / "workspace"
            seen: dict[str, object] = {}

            def fake_invoke(prompt, workspace_arg, instruction_prompt, ledger, **kwargs):
                seen["prompt"] = prompt
                seen["workspace"] = workspace_arg
                seen["instruction_prompt"] = instruction_prompt
                seen["reserved_usd"] = kwargs["reserved_usd"]
                return ProviderResult("run-id", "claude-code", "completed", "ok", None, 0)

            argv = [
                "ai-remote-runner",
                "provider-smoke",
                "--provider",
                "claude-code",
                "--workspace",
                str(workspace),
                "--prompt-file",
                str(prompt_file),
                "--reserved-usd",
                "0.33",
            ]
            with (
                patch("sys.argv", argv),
                patch("ai_remote_runner.cli.state_root", return_value=root / "state"),
                patch("ai_remote_runner.cli.invoke_claude", side_effect=fake_invoke),
            ):
                result = cli.main()

            self.assertEqual(result, 0)
            self.assertEqual(seen["prompt"], "PROMPT_FROM_FILE")
            self.assertEqual(seen["workspace"], workspace)
            self.assertEqual(seen["reserved_usd"], 0.33)


if __name__ == "__main__":
    unittest.main()
