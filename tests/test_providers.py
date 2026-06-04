from __future__ import annotations

import unittest
from pathlib import Path

from ai_remote_runner.providers import CLAUDE_MODEL_FALLBACKS, codex_command


class ProviderTests(unittest.TestCase):
    def test_codex_command_uses_supported_approval_config(self) -> None:
        command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
        self.assertNotIn("--ask-for-approval", command)

    def test_claude_model_fallbacks_are_configured(self) -> None:
        self.assertIn("claude-opus-4-7", CLAUDE_MODEL_FALLBACKS)
        self.assertIn("claude-opus-4-8-thinking", CLAUDE_MODEL_FALLBACKS)


if __name__ == "__main__":
    unittest.main()
