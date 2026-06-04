from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PairRunnerScriptTests(unittest.TestCase):
    def test_raw_bridge_secret_argument_is_rejected_without_echoing_secret(self) -> None:
        secret = "A" * 43
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "pair-runner.sh"),
                "--platform-url",
                "https://mattermost.example",
                "--webhook-url",
                "https://mattermost.example/hooks/test",
                "--bot-token",
                "bot-token",
                "--transfer-method",
                "manual-secure",
                "--bridge-secret",
                secret,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(secret, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
