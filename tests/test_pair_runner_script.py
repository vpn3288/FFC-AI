from __future__ import annotations

import subprocess
import tempfile
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

    def test_pair_runner_restarts_bridge_service_after_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "calls.txt"
            secret_file = root / "secret"
            slash_file = root / "slash"
            secret_file.write_text("A" * 43, encoding="utf-8")
            slash_file.write_text("slash-token", encoding="utf-8")
            (bin_dir / "sudo").write_text(
                "#!/usr/bin/env bash\nexec \"$@\"\n",
                encoding="utf-8",
            )
            (bin_dir / "systemctl").write_text(
                f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {calls}\n"
                "if [ \"$1\" = list-unit-files ]; then exit 0; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            for path in bin_dir.iterdir():
                path.chmod(0o755)
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-runner.sh"),
                    "--platform-url",
                    "https://mattermost.example",
                    "--webhook-url",
                    "https://mattermost.example/hooks/test",
                    "--transfer-method",
                    "manual-secure",
                    "--bridge-secret-file",
                    str(secret_file),
                    "--slash-token-file",
                    str(slash_file),
                ],
                text=True,
                capture_output=True,
                check=False,
                env={
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "PAIR_RUNNER_SKIP_VALIDATE": "true",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("restart ai-remote-runner.service", calls.read_text(encoding="utf-8"))
            self.assertIn("ai-remote-runner.service restarted", result.stdout)


if __name__ == "__main__":
    unittest.main()
