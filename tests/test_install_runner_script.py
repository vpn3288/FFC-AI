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
    def test_install_always_requires_both_core_providers_and_defaults_full_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            root_home = root / "root-home"
            vscode_wrapper = root / "code-root"
            vscode_root = root / "vscode-root"
            systemctl_calls = root / "systemctl-calls.txt"
            runner_python_calls = root / "runner-python-calls.txt"
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
                  exec env "$@"
                fi
                if [ "${1:-}" = "python3" ] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "venv" ]; then
                  mkdir -p "$4/bin"
                  cat > "$4/bin/python" <<'PY'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${RUNNER_PYTHON_CALLS:?}"
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
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
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
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then
                  printf 'claude-code 1.0.0\\n'
                  exit 0
                fi
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
                  printf '{"loggedIn":true}\\n'
                  exit 0
                fi
                exit 0
                """,
            )
            write_executable(
                fakebin / "code",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then
                  printf '1.100.0\\n'
                  printf 'fakehash\\n'
                  printf 'x64\\n'
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
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_TOOL_HOME": str(root_home),
                    "AI_VSCODE_ROOT_WRAPPER": str(vscode_wrapper),
                    "AI_VSCODE_ROOT_DIR": str(vscode_root),
                    "AI_DEFAULT_PROVIDER": "codex",
                    "OPENAI_API_KEY": "test-openai-key",
                    "CODEX_BASE_URL": "https://example.invalid/v1",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "SYSTEMCTL_CALLS": str(systemctl_calls),
                    "RUNNER_PYTHON_CALLS": str(runner_python_calls),
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
            self.assertIn("stage 03: install or verify requested Claude Code provider", result.stdout)
            self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
            self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("AI_RUNNER_PROVIDERS=claude-code,codex\n", config)
            self.assertIn("AI_PERMISSION_SCOPE=full\n", config)
            self.assertIn("AI_REQUIRE_SHELL_CONFIRMATION=0\n", config)
            self.assertIn(f"HOME={root_home}\n", config)
            self.assertIn(f"CODEX_HOME={root_home / '.codex'}\n", config)
            self.assertIn(f"AI_BRIDGE_SHARED_SECRET={existing_bridge_secret}\n", config)
            self.assertIn("MATTERMOST_PLATFORM_URL=https://mattermost.example\n", config)
            self.assertIn("MATTERMOST_WEBHOOK_URL=https://mattermost.example/hooks/hook-id\n", config)
            self.assertIn("MATTERMOST_SLASH_TOKEN=slash-token\n", config)
            self.assertIn("AI_BRIDGE_SECRET_TRANSFER_METHOD=ssh\n", config)
            policy = json.loads((state / "conversation-policy.json").read_text(encoding="utf-8"))
            self.assertEqual(policy["permission_scope"], "full")
            provider_selection = json.loads((state / "provider-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(provider_selection, {"provider": "codex"})
            runner_unit = (root / "ai-remote-runner.service").read_text(encoding="utf-8")
            self.assertEqual(runner_unit.count("ExecStart="), 1)
            self.assertIn("User=root", runner_unit)
            self.assertIn(str(install / ".venv" / "bin" / "python"), runner_unit)
            self.assertIn("restart ai-remote-runner.service", systemctl_calls.read_text(encoding="utf-8"))
            runner_calls = runner_python_calls.read_text(encoding="utf-8")
            self.assertIn("-m ai_remote_runner.cli providers", runner_calls)
            self.assertIn("-m ai_remote_runner.cli parse /ai 状态", runner_calls)
            codex_config = (root_home / ".codex" / "config.toml").read_text(encoding="utf-8")
            self.assertIn('approval_policy = "never"', codex_config)
            self.assertIn('sandbox_mode = "danger-full-access"', codex_config)
            self.assertIn("dangerously_bypass_approvals_and_sandbox = true", codex_config)
            self.assertIn('inherit = "all"', codex_config)
            self.assertIn("disable_response_storage = false", codex_config)
            self.assertTrue(vscode_wrapper.exists())
            vscode_script = vscode_wrapper.read_text(encoding="utf-8")
            self.assertIn("--no-sandbox", vscode_script)
            self.assertIn("--disable-workspace-trust", vscode_script)
            self.assertIn(str(vscode_root), vscode_script)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["vscode_ready"])
            self.assertEqual(manifest["vscode_root_wrapper"], str(vscode_wrapper))

    def test_enable_telegram_installs_service_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            vscode_wrapper = root / "code-root"
            state.mkdir()
            (state / "config.env").write_text("AI_BRIDGE_SHARED_SECRET=" + "A" * 43 + "\n", encoding="utf-8")

            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "env" ]; then
                  shift
                  exec env "$@"
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
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.137.0\\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--sandbox]\\n'; exit 0; fi
                exit 0
                """,
            )
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'claude-code 1.0.0\\n'; exit 0; fi
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then printf '{"loggedIn":true}\\n'; exit 0; fi
                exit 0
                """,
            )
            write_executable(fakebin / "code", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"--version\" ]; then printf '1.100.0\\n'; fi\nexit 0\n")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_VSCODE_ROOT_WRAPPER": str(vscode_wrapper),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
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
            self.assertIn("User=root", telegram_unit.read_text(encoding="utf-8"))
            self.assertIn("Telegram service installed but not started", result.stdout)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["telegram_enabled"])
            self.assertEqual(manifest["permission_scope"], "full")

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
                  exec env "$@"
                fi
                if [ "${1:-}" = "npm" ] && [ "${2:-}" = "install" ] && [ "${3:-}" = "-g" ]; then
                  printf '%s\n' "$*" >> "${NPM_CALLS:?}"
                  cat > "$(dirname "$0")/codex" <<'CODEX'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.137.0\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--sandbox]\n'; exit 0; fi
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
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "node", "#!/usr/bin/env bash\nprintf 'v20.11.1\\n'\n")
            write_executable(fakebin / "npm", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'claude-code 1.0.0\\n'; exit 0; fi
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then printf '{"loggedIn":true}\\n'; exit 0; fi
                exit 0
                """,
            )
            write_executable(fakebin / "code", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"--version\" ]; then printf '1.100.0\\n'; fi\nexit 0\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_VSCODE_ROOT_WRAPPER": str(root / "code-root"),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
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

    def test_old_node_uses_nodesource_before_codex_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            nodesource_marker = root / "nodesource-ran.txt"
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
                  exec env "$@"
                fi
                if [ "${1:-}" = "npm" ] && [ "${2:-}" = "install" ] && [ "${3:-}" = "-g" ]; then
                  printf '%s\n' "$*" >> "${NPM_CALLS:?}"
                  cat > "$(dirname "$0")/codex" <<'CODEX'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.137.0\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--sandbox]\n'; exit 0; fi
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
            write_executable(
                fakebin / "node",
                """
                #!/usr/bin/env bash
                if [ -f "${NEW_NODE_MARKER:?}" ]; then
                  printf 'v20.11.1\n'
                else
                  printf 'v12.22.9\n'
                fi
                """,
            )
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                output=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "-o" ]; then output="$2"; shift 2; continue; fi
                  shift
                done
                cat > "$output" <<'SETUP'
#!/usr/bin/env bash
set -e
printf 'nodesource\n' > "${NODESOURCE_MARKER:?}"
touch "${NEW_NODE_MARKER:?}"
SETUP
                exit 0
                """,
            )
            write_executable(fakebin / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "npm", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'claude-code 1.0.0\\n'; exit 0; fi
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then printf '{"loggedIn":true}\\n'; exit 0; fi
                exit 0
                """,
            )
            write_executable(fakebin / "code", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"--version\" ]; then printf '1.100.0\\n'; fi\nexit 0\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_VSCODE_ROOT_WRAPPER": str(root / "code-root"),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
                    "AI_RUNNER_PROVIDERS": "codex",
                    "AI_DEFAULT_PROVIDER": "codex",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "NPM_CALLS": str(npm_calls),
                    "NODESOURCE_MARKER": str(nodesource_marker),
                    "NEW_NODE_MARKER": str(root / "new-node"),
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
            self.assertTrue(nodesource_marker.exists())
            self.assertIn("Node.js 20+ is required", result.stdout)
            self.assertIn("npm install -g", npm_calls.read_text(encoding="utf-8"))

    def test_default_provider_can_select_any_core_provider(self) -> None:
        env = os.environ.copy()
        env.update({"AI_RUNNER_PROVIDERS": "claude-code", "AI_DEFAULT_PROVIDER": "codex"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("AI_RUNNER_PROVIDERS=claude-code ignored; core install requires claude-code,codex", result.stdout + result.stderr)

    def test_single_requested_provider_is_ignored_for_core_install(self) -> None:
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
        self.assertIn("AI_RUNNER_PROVIDERS=codex ignored; core install requires claude-code,codex", result.stdout + result.stderr)
        self.assertNotIn("skip Claude Code install; provider not requested", result.stdout)

    def test_dry_run_does_not_execute_real_provider_or_runner_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            write_executable(fakebin / "python3", "#!/usr/bin/env bash\nprintf 'python3 should not run in dry-run\\n' >&2\nexit 99\n")
            write_executable(fakebin / "claude", "#!/usr/bin/env bash\nprintf 'claude should not run in dry-run\\n' >&2\nexit 99\n")
            write_executable(fakebin / "codex", "#!/usr/bin/env bash\nprintf 'codex should not run in dry-run\\n' >&2\nexit 99\n")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_INSTALL_ROOT": str(root / "install"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("[dry-run] root env", result.stdout)
            self.assertNotIn("should not run", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
