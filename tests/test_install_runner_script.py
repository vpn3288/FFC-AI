from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class InstallRunnerScriptTests(unittest.TestCase):
    def test_codex_only_install_does_not_require_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            state.mkdir()
            existing_bridge_secret = "A" * 43
            (state / "config.env").write_text(
                "\n".join(
                    [
                        f"AI_BRIDGE_SHARED_SECRET={existing_bridge_secret}",
                        "MATTERMOST_PLATFORM_URL=https://mattermost.example",
                        "MATTERMOST_WEBHOOK_URL=https://mattermost.example/hooks/hook-id",
                        "MATTERMOST_SLASH_TOKEN=slash-token",
                        "AI_BRIDGE_SECRET_TRANSFER_METHOD=ssh",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "env" ]; then
                  shift
                  while [ "$#" -gt 0 ] && [[ "$1" == *=* ]]; do shift; done
                  exec "$@"
                fi
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [ "${1:-}" = "tee" ] && [[ "${2:-}" == /etc/systemd/system/* ]]; then
                  cat > "${FAKE_SYSTEMD_UNIT:?}"
                  exit 0
                fi
                exec "$@"
                """,
            )
            write_executable(
                fakebin / "apt-get",
                """
                #!/usr/bin/env bash
                exit 0
                """,
            )
            write_executable(
                fakebin / "systemctl",
                """
                #!/usr/bin/env bash
                exit 0
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then
                  printf 'codex-cli 0.137.0\\n'
                  exit 0
                fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--sandbox]\\n'
                  exit 0
                fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "-c" ]; then
                  printf 'usage: codex exec [--sandbox]\\n'
                  exit 0
                fi
                exit 0
                """,
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_DEFAULT_PROVIDER": "codex",
                    "OPENAI_API_KEY": "test-openai-key",
                    "CODEX_BASE_URL": "https://example.invalid/v1",
                    "FAKE_SYSTEMD_UNIT": str(root / "ai-remote-runner.service"),
                }
            )
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            env.pop("ANTHROPIC_BASE_URL", None)

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-runner.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("skip Claude Code install; provider not requested", result.stdout)
            self.assertIn("claude auth not required unless claude-code provider is requested", result.stdout)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("AI_RUNNER_PROVIDERS=codex\n", config)
            self.assertIn(f"AI_BRIDGE_SHARED_SECRET={existing_bridge_secret}\n", config)
            self.assertIn("MATTERMOST_PLATFORM_URL=https://mattermost.example\n", config)
            self.assertIn("MATTERMOST_WEBHOOK_URL=https://mattermost.example/hooks/hook-id\n", config)
            self.assertIn("MATTERMOST_SLASH_TOKEN=slash-token\n", config)
            self.assertIn("AI_BRIDGE_SECRET_TRANSFER_METHOD=ssh\n", config)
            provider_selection = json.loads((state / "provider-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(provider_selection, {"provider": "codex"})

    def test_default_provider_must_be_requested(self) -> None:
        env = os.environ.copy()
        env.update({"AI_RUNNER_PROVIDERS": "claude-code", "AI_DEFAULT_PROVIDER": "codex"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AI_DEFAULT_PROVIDER=codex must be included", result.stdout + result.stderr)

    def test_single_requested_provider_becomes_default(self) -> None:
        env = os.environ.copy()
        env.update({"AI_RUNNER_PROVIDERS": "codex"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AI_RUNNER_PROVIDERS=codex", result.stdout + result.stderr)
        self.assertIn("skip Claude Code install; provider not requested", result.stdout)


if __name__ == "__main__":
    unittest.main()
