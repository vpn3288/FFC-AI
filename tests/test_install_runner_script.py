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
            systemctl_calls = root / "systemctl-calls.txt"
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
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "venv" ]; then
                  mkdir -p "$4/bin"
                  cat > "$4/bin/python" <<'PY'
#!/usr/bin/env bash
exit 0
PY
                  chmod +x "$4/bin/python"
                  exit 0
                fi
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [ "${1:-}" = "tee" ] && [[ "${2:-}" == /etc/systemd/system/* ]]; then
                  cat > "${FAKE_SYSTEMD_DIR:?}/$(basename "$2")"
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
                if [ -n "${SYSTEMCTL_CALLS:-}" ]; then
                  printf '%s\n' "$*" >> "$SYSTEMCTL_CALLS"
                fi
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
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_DEFAULT_PROVIDER": "codex",
                    "OPENAI_API_KEY": "test-openai-key",
                    "CODEX_BASE_URL": "https://example.invalid/v1",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "SYSTEMCTL_CALLS": str(systemctl_calls),
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
            runner_unit = (root / "ai-remote-runner.service").read_text(encoding="utf-8")
            self.assertEqual(runner_unit.count("ExecStart="), 1)
            self.assertIn(str(install / ".venv" / "bin" / "python"), runner_unit)
            self.assertIn("restart ai-remote-runner.service", systemctl_calls.read_text(encoding="utf-8"))

    def test_enable_telegram_installs_service_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            state.mkdir()
            (state / "config.env").write_text("AI_BRIDGE_SHARED_SECRET=" + "A" * 43 + "\n", encoding="utf-8")

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
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "venv" ]; then
                  mkdir -p "$4/bin"
                  cat > "$4/bin/python" <<'PY'
#!/usr/bin/env bash
exit 0
PY
                  chmod +x "$4/bin/python"
                  exit 0
                fi
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [ "${1:-}" = "tee" ] && [[ "${2:-}" == /etc/systemd/system/* ]]; then
                  cat > "${FAKE_SYSTEMD_DIR:?}/$(basename "$2")"
                  exit 0
                fi
                exec "$@"
                """,
            )
            write_executable(fakebin / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.137.0\\n'; fi
                exit 0
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_PROVIDERS": "codex",
                    "AI_DEFAULT_PROVIDER": "codex",
                    "FAKE_SYSTEMD_DIR": str(root),
                }
            )
            env.pop("TELEGRAM_BOT_TOKEN", None)

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--enable-telegram"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            telegram_unit = root / "ai-telegram-bot.service"
            self.assertTrue(telegram_unit.exists())
            self.assertIn("ai_remote_runner.cli telegram", telegram_unit.read_text(encoding="utf-8"))
            self.assertIn(str(install / ".venv" / "bin" / "python"), telegram_unit.read_text(encoding="utf-8"))
            self.assertIn("Telegram service installed but not started", result.stdout)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["telegram_enabled"])

    def test_missing_codex_installs_through_sudo_npm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            npm_calls = root / "npm-calls.txt"
            state.mkdir()
            (state / "config.env").write_text("AI_BRIDGE_SHARED_SECRET=" + "A" * 43 + "\n", encoding="utf-8")

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
                if [ "${1:-}" = "npm" ] && [ "${2:-}" = "install" ] && [ "${3:-}" = "-g" ]; then
                  printf '%s\n' "$*" >> "${NPM_CALLS:?}"
                  cat > "$(dirname "$0")/codex" <<'CODEX'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.137.0\n'; exit 0; fi
if [ "${1:-}" = "exec" ]; then exit 0; fi
exit 0
CODEX
                  chmod +x "$(dirname "$0")/codex"
                  exit 0
                fi
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "venv" ]; then
                  mkdir -p "$4/bin"
                  cat > "$4/bin/python" <<'PY'
#!/usr/bin/env bash
exit 0
PY
                  chmod +x "$4/bin/python"
                  exit 0
                fi
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [ "${1:-}" = "tee" ] && [[ "${2:-}" == /etc/systemd/system/* ]]; then
                  cat > "${FAKE_SYSTEMD_DIR:?}/$(basename "$2")"
                  exit 0
                fi
                exec "$@"
                """,
            )
            write_executable(fakebin / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_PROVIDERS": "codex",
                    "AI_DEFAULT_PROVIDER": "codex",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "NPM_CALLS": str(npm_calls),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-runner.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("npm install -g", npm_calls.read_text(encoding="utf-8"))

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
