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


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("ANTHROPIC_") or key.startswith("CODEX_") or key.startswith("CLAUDE_") or key.startswith("TELEGRAM_"):
            env.pop(key, None)
    env.pop("OPENAI_API_KEY", None)
    env.pop("AI_BRIDGE_SHARED_SECRET", None)
    env.pop("AI_INSTALL_CC_SWITCH", None)
    return env


class InstallRunnerScriptTests(unittest.TestCase):
    def test_codex_runner_defaults_full_root_and_preserves_platform_config(self) -> None:
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
                  printf 'codex-cli 0.138.0\\n'
                  exit 0
                fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "resume" ] && [ "${3:-}" = "--help" ]; then printf 'usage: codex exec resume [--json] [--output-last-message] [SESSION_ID] [PROMPT]\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--ephemeral] [--cd] [--output-last-message] [--output-schema] [--sandbox] [--add-dir] [--skip-git-repo-check]\\n'
                  exit 0
                fi
                if [ "${1:-}" = "exec" ] && printf ' %s ' "$*" | grep -q ' --strict-config '; then
                  printf 'Failed to read output schema file /missing: No such file or directory (os error 2)\\n' >&2
                  exit 1
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

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "codex,telegram",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_TOOL_HOME": str(root_home),
                    "AI_VSCODE_ROOT_WRAPPER": str(vscode_wrapper),
                    "AI_VSCODE_ROOT_DIR": str(vscode_root),
                    "AI_DEFAULT_PROVIDER": "codex",
                    "CODEX_API_KEY": "test-openai-key",
                    "CODEX_MODEL": "codex",
                    "CODEX_MODEL_PROVIDER": "openai",
                    "CODEX_OPENAI_BASE_URL": "https://example.invalid/v1",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "SYSTEMCTL_CALLS": str(systemctl_calls),
                    "RUNNER_PYTHON_CALLS": str(runner_python_calls),
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
            self.assertIn("stage 03: skip Claude Code provider because AI_RUNNER_COMPONENTS does not request it", result.stdout)
            self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
            self.assertIn("stage 05: skip VSCode because AI_RUNNER_COMPONENTS does not request it", result.stdout)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("AI_RUNNER_PROVIDERS=codex\n", config)
            self.assertIn("OPENAI_API_KEY=test-openai-key\n", config)
            self.assertIn("CODEX_MODEL=gpt-5.5\n", config)
            self.assertIn("CODEX_MODEL_PROVIDER=ffc_openai_compat\n", config)
            self.assertIn("CODEX_BASE_URL=https://example.invalid/v1\n", config)
            self.assertIn("CODEX_EXEC_EPHEMERAL=0\n", config)
            self.assertIn("AI_PERMISSION_SCOPE=full\n", config)
            self.assertIn("AI_REQUIRE_SHELL_CONFIRMATION=0\n", config)
            self.assertIn("AI_PROCESS_CONTROL_ENABLED=1\n", config)
            self.assertIn("AI_TASK_TIMEOUT_SECONDS=7200\n", config)
            self.assertIn(f"HOME={root_home}\n", config)
            self.assertIn(f"CODEX_HOME={root_home / '.codex'}\n", config)
            self.assertIn("TERM=xterm-256color\n", config)
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
            self.assertIn("Restart=always", runner_unit)
            self.assertIn(str(install / ".venv" / "bin" / "python"), runner_unit)
            self.assertIn("restart ai-remote-runner.service", systemctl_calls.read_text(encoding="utf-8"))
            runner_calls = runner_python_calls.read_text(encoding="utf-8")
            self.assertIn("-m ai_remote_runner.cli providers", runner_calls)
            self.assertIn("-m ai_remote_runner.cli parse /ai 状态", runner_calls)
            codex_config = (root_home / ".codex" / "config.toml").read_text(encoding="utf-8")
            self.assertIn('model_provider = "ffc_openai_compat"', codex_config)
            self.assertIn('model = "gpt-5.5"', codex_config)
            self.assertNotIn('openai_base_url = "https://example.invalid/v1"', codex_config)
            self.assertIn("[model_providers.ffc_openai_compat]", codex_config)
            self.assertIn('base_url = "https://example.invalid/v1"', codex_config)
            self.assertIn('wire_api = "responses"', codex_config)
            self.assertIn('env_key = "OPENAI_API_KEY"', codex_config)
            self.assertIn("supports_websockets = false", codex_config)
            self.assertIn('approval_policy = "never"', codex_config)
            self.assertIn('sandbox_mode = "danger-full-access"', codex_config)
            self.assertIn("[sandbox_workspace_write]", codex_config)
            self.assertIn("network_access = true", codex_config)
            self.assertNotIn('network_access = "enabled"', codex_config)
            self.assertNotIn("workspace_write_network_access", codex_config)
            self.assertNotIn("dangerously_bypass_approvals_and_sandbox", codex_config)
            self.assertIn('inherit = "all"', codex_config)
            self.assertNotIn("disable_response_storage", codex_config)
            self.assertNotIn("windows_wsl_setup_acknowledged", codex_config)
            self.assertFalse(vscode_wrapper.exists())
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["vscode_ready"])
            self.assertEqual(manifest["configured_providers"], "codex")
            self.assertEqual(manifest["process_control_enabled"], "1")
            self.assertTrue(manifest["codex_exec_json_available"])
            self.assertTrue(manifest["codex_exec_ephemeral_available"])
            self.assertTrue(manifest["codex_exec_resume_available"])
            self.assertTrue(manifest["codex_exec_resume_json_available"])
            self.assertTrue(manifest["codex_exec_resume_output_last_message_available"])
            self.assertTrue(manifest["codex_exec_add_dir_available"])
            self.assertTrue(manifest["codex_exec_skip_git_repo_check_available"])
            self.assertEqual(manifest["codex_exec_full_access_mode"], "sandbox")
            self.assertTrue(manifest["codex_telegram_realtime_status"])

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
            write_executable(fakebin / "bash", "#!/bin/sh\nexec /bin/bash \"$@\"\n")
            write_executable(fakebin / "sh", "#!/bin/sh\nexec /bin/sh \"$@\"\n")
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.138.0\\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "resume" ] && [ "${3:-}" = "--help" ]; then printf 'usage: codex exec resume [--json] [--output-last-message] [SESSION_ID] [PROMPT]\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--json] [--ephemeral] [--cd] [--output-last-message] [--output-schema] [--sandbox] [--add-dir] [--skip-git-repo-check]\\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && printf ' %s ' "$*" | grep -q ' --strict-config '; then printf 'Failed to read output schema file /missing: No such file or directory (os error 2)\\n' >&2; exit 1; fi
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
            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "codex,telegram",
                    "AI_SERVICE_PATH": str(fakebin),
                    "AI_VSCODE_ROOT_WRAPPER": str(vscode_wrapper),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
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
            self.assertIn("Restart=always", telegram_unit.read_text(encoding="utf-8"))
            self.assertIn("Telegram service installed but not started", result.stdout)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["telegram_enabled"])
            self.assertEqual(manifest["permission_scope"], "full")
            self.assertEqual(manifest["codex_exec_ephemeral_enabled"], "0")

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
if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.138.0\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "resume" ] && [ "${3:-}" = "--help" ]; then printf 'usage: codex exec resume [--json] [--output-last-message] [SESSION_ID] [PROMPT]\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--json] [--ephemeral] [--cd] [--output-last-message] [--output-schema] [--sandbox] [--add-dir] [--skip-git-repo-check]\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && printf ' %s ' "$*" | grep -q ' --strict-config '; then printf 'Failed to read output schema file /missing: No such file or directory (os error 2)\n' >&2; exit 1; fi
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
            write_executable(fakebin / "bash", "#!/bin/sh\nexec /bin/bash \"$@\"\n")
            write_executable(fakebin / "sh", "#!/bin/sh\nexec /bin/sh \"$@\"\n")
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "node", "#!/usr/bin/env bash\nprintf 'v24.2.0\\n'\n")
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

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "codex",
                    "AI_SERVICE_PATH": str(fakebin),
                    "AI_VSCODE_ROOT_WRAPPER": str(root / "code-root"),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
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

    def test_codex_preflight_rejects_strict_config_errors(self) -> None:
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
                if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.138.0\\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "resume" ] && [ "${3:-}" = "--help" ]; then printf 'usage: codex exec resume [--json] [--output-last-message] [SESSION_ID] [PROMPT]\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--json] [--ephemeral] [--cd] [--output-last-message] [--output-schema] [--sandbox] [--add-dir] [--skip-git-repo-check]\\n'; exit 0; fi
                if [ "${1:-}" = "exec" ] && printf ' %s ' "$*" | grep -q ' --strict-config '; then
                  printf 'Error loading config.toml: unknown configuration field `bad_field`\\n' >&2
                  exit 1
                fi
                exit 0
                """,
            )

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "codex",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_DEFAULT_PROVIDER": "codex",
                    "CODEX_API_KEY": "test-openai-key",
                    "FAKE_SYSTEMD_DIR": str(root),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-runner.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("unknown configuration field", result.stdout + result.stderr)
            self.assertIn("codex config.toml is not accepted", result.stdout + result.stderr)

    def test_missing_claude_installs_stable_npm_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            state = root / "state"
            workspaces = root / "workspaces"
            install = root / "install"
            root_home = root / "root-home"
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
                  cat > "$(dirname "$0")/claude" <<'CLAUDE'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then printf 'claude-code 2.1.153\n'; exit 0; fi
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then printf '{"loggedIn":true}\n'; exit 0; fi
exit 0
CLAUDE
                  chmod +x "$(dirname "$0")/claude"
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
            write_executable(fakebin / "bash", "#!/bin/sh\nexec /bin/bash \"$@\"\n")
            write_executable(fakebin / "sh", "#!/bin/sh\nexec /bin/sh \"$@\"\n")
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "node", "#!/usr/bin/env bash\nprintf 'v24.2.0\\n'\n")
            write_executable(fakebin / "npm", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "claude-code",
                    "AI_SERVICE_PATH": str(fakebin),
                    "AI_TOOL_HOME": str(root_home),
                    "AI_DEFAULT_PROVIDER": "claude-code",
                    "FAKE_SYSTEMD_DIR": str(root),
                    "NPM_CALLS": str(npm_calls),
                    "ANTHROPIC_AUTH_TOKEN": "fixture-token",
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
            calls = npm_calls.read_text(encoding="utf-8")
            self.assertIn("npm install -g @anthropic-ai/claude-code@2.1.153", calls)
            self.assertIn("claude missing; installing @anthropic-ai/claude-code@2.1.153 through npm", result.stdout)

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
if [ "${1:-}" = "--version" ]; then printf 'codex-cli 0.138.0\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "resume" ] && [ "${3:-}" = "--help" ]; then printf 'usage: codex exec resume [--json] [--output-last-message] [SESSION_ID] [PROMPT]\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then printf 'usage: codex exec [--json] [--ephemeral] [--cd] [--output-last-message] [--output-schema] [--sandbox] [--add-dir] [--skip-git-repo-check]\n'; exit 0; fi
if [ "${1:-}" = "exec" ] && printf ' %s ' "$*" | grep -q ' --strict-config '; then printf 'Failed to read output schema file /missing: No such file or directory (os error 2)\n' >&2; exit 1; fi
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
                  printf 'v24.2.0\n'
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
            write_executable(fakebin / "bash", "#!/bin/sh\nexec /bin/bash \"$@\"\n")
            write_executable(fakebin / "sh", "#!/bin/sh\nexec /bin/sh \"$@\"\n")
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

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "AI_REMOTE_INSTALL_ROOT": str(install),
                    "AI_RUNNER_COMPONENTS": "codex",
                    "AI_SERVICE_PATH": str(fakebin),
                    "AI_VSCODE_ROOT_WRAPPER": str(root / "code-root"),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
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
            self.assertIn("Node.js 22+ even-major stable/LTS is required", result.stdout)
            self.assertIn("installing Node.js 24.x LTS from NodeSource", result.stdout)
            self.assertIn("npm install -g", npm_calls.read_text(encoding="utf-8"))

    def test_components_are_required_by_default(self) -> None:
        env = clean_env()
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AI_RUNNER_COMPONENTS is required", result.stdout + result.stderr)

    def test_installer_static_contract_enables_process_control_by_default(self) -> None:
        text = (ROOT / "scripts" / "install-runner.sh").read_text(encoding="utf-8")
        self.assertIn('AI_PROCESS_CONTROL_ENABLED="${AI_PROCESS_CONTROL_ENABLED:-1}"', text)
        self.assertIn("AI_PROCESS_CONTROL_ENABLED=$EFFECTIVE_AI_PROCESS_CONTROL_ENABLED", text)
        self.assertIn('"process_control_enabled": "$EFFECTIVE_AI_PROCESS_CONTROL_ENABLED"', text)

    def test_installer_rejects_codex_anthropic_key_family(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "codex,telegram", "OPENAI_API_KEY": "sk-ant-" + "x" * 24})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Codex/OpenAI config cannot use an Anthropic sk-ant-* key", result.stdout + result.stderr)

    def test_installer_rejects_invalid_provider_base_url(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "codex,telegram", "CODEX_BASE_URL": "ftp://proxy.example/v1"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("CODEX_BASE_URL must be an http(s) URL", result.stdout + result.stderr)

    def test_default_provider_must_match_requested_provider(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "claude-code", "AI_DEFAULT_PROVIDER": "codex"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("AI_DEFAULT_PROVIDER=codex must be included in AI_RUNNER_PROVIDERS=claude-code", result.stdout + result.stderr)

    def test_all_full_core_aliases_install_all_primary_tools(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "all,telegram"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 03: install or verify requested Claude Code provider/backend", result.stdout)
        self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
        self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
        self.assertIn("AI_RUNNER_PROVIDERS=codex,claude-code,vscode", result.stdout)
        self.assertIn("would install /etc/systemd/system/ai-telegram-bot.service", result.stdout)

    def test_cc_switch_optional_install_can_be_requested_without_runner(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "cc-switch"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 02c: install optional CC Switch desktop profile manager", result.stdout)
        self.assertIn("would download latest CC Switch Linux .deb", result.stdout)
        self.assertIn("runner bridge/provider service install skipped", result.stdout)

    def test_cc_switch_optional_install_can_be_enabled_by_env(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "codex,telegram", "AI_INSTALL_CC_SWITCH": "true"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 02c: install optional CC Switch desktop profile manager", result.stdout)
        self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)

    def test_mixed_primary_tools_are_allowed_as_multi_provider_runner(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "codex,vscode,telegram"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 03: install or verify requested Claude Code provider/backend", result.stdout)
        self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
        self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
        self.assertIn("AI_RUNNER_PROVIDERS=codex,vscode", result.stdout)

    def test_explicit_codex_telegram_components_skip_claude_and_vscode(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "codex,telegram"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skip Claude Code provider because AI_RUNNER_COMPONENTS does not request it", result.stdout)
        self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
        self.assertIn("skip VSCode because AI_RUNNER_COMPONENTS does not request it", result.stdout)
        self.assertIn("AI_RUNNER_PROVIDERS=codex", result.stdout)
        self.assertIn("would install /etc/systemd/system/ai-telegram-bot.service", result.stdout)

    def test_explicit_vscode_component_skips_runner_services(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "vscode"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 03: skip Claude Code provider because AI_RUNNER_COMPONENTS does not request it", result.stdout)
        self.assertIn("stage 04: skip Codex CLI provider because AI_RUNNER_COMPONENTS does not request it", result.stdout)
        self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
        self.assertIn("runner bridge/provider service install skipped", result.stdout)
        self.assertNotIn("stage 06: create runner directories", result.stdout)

    def test_explicit_vscode_telegram_installs_vscode_adapter_with_claude_backend(self) -> None:
        env = clean_env()
        env.update({"AI_RUNNER_COMPONENTS": "vscode,telegram"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-runner.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("stage 03: install or verify requested Claude Code provider/backend", result.stdout)
        self.assertIn("root env PATH=", result.stdout)
        self.assertIn("command -v claude", result.stdout)
        self.assertIn("stage 04: skip Codex CLI provider because AI_RUNNER_COMPONENTS does not request it", result.stdout)
        self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
        self.assertIn("AI_RUNNER_PROVIDERS=vscode", result.stdout)
        self.assertIn("VSCode Claude model=gpt-5.5", result.stdout)
        self.assertIn("would write /root/.vscode-root/User/settings.json for root VSCode operation", result.stdout)
        self.assertIn("would install /etc/systemd/system/ai-telegram-bot.service", result.stdout)
        self.assertNotIn("enabling both claude-code and codex", result.stdout)

    def test_vscode_only_does_not_write_unrequested_ai_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            root_home = root / "root-home"
            (root_home / ".claude").mkdir(parents=True)
            (root_home / ".codex").mkdir(parents=True)
            (root_home / ".claude" / "settings.json").write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"old"}}\n', encoding="utf-8")
            (root_home / ".anthropic-api-key").write_text("old\n", encoding="utf-8")
            (root_home / ".codex" / "config.toml").write_text('model = "old"\n', encoding="utf-8")
            (root_home / ".codex" / "auth.json").write_text('{"OPENAI_API_KEY":"old"}\n', encoding="utf-8")
            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "env" ]; then
                  shift
                  exec env "$@"
                fi
                if [ "${1:-}" = "-E" ]; then
                  shift
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
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "ai_remote_runner.cli" ]; then
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
            write_executable(fakebin / "code", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"--version\" ]; then printf '1.100.0\\n'; fi\nexit 0\n")
            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(root / "state"),
                    "AI_RUNNER_COMPONENTS": "vscode",
                    "AI_TOOL_HOME": str(root_home),
                    "AI_VSCODE_ROOT_WRAPPER": str(root / "code-root"),
                    "AI_VSCODE_ROOT_DIR": str(root / "vscode-root"),
                    "VSCODE_MODEL": "vscode-alias-model",
                    "ANTHROPIC_BASE_URL": "https://example.invalid",
                    "ANTHROPIC_AUTH_TOKEN": "fixture-token",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
                    "AI_WRITE_CLAUDE_SETTINGS": "true",
                    "OPENAI_API_KEY": "openai-fixture",
                    "CODEX_BASE_URL": "https://codex.example.invalid/v1",
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
            settings = json.loads((root_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["CLAUDE_MODEL"], "vscode-alias-model")
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://example.invalid")
            self.assertEqual(settings["env"]["ANTHROPIC_AUTH_TOKEN"], "fixture-token")
            vscode_settings = json.loads((root / "vscode-root" / "User" / "settings.json").read_text(encoding="utf-8"))
            self.assertFalse(vscode_settings["security.workspace.trust.enabled"])
            self.assertEqual(vscode_settings["telemetry.telemetryLevel"], "off")
            self.assertFalse((root_home / ".anthropic-api-key").exists())
            self.assertFalse((root_home / ".codex" / "config.toml").exists())
            self.assertFalse((root_home / ".codex" / "auth.json").exists())
            self.assertIn("write VSCode root user settings", result.stdout)
            self.assertIn("write VSCode Claude model/API settings", result.stdout)
            self.assertIn("skip Codex config/auth because Codex is not requested", result.stdout)
            self.assertIn("preserve root Claude settings because VSCode is configured to use Claude model/API settings", result.stdout)

    def test_claude_settings_are_written_for_requested_claude_root_tool_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            root_home = root / "root-home"
            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "env" ]; then
                  shift
                  exec env "$@"
                fi
                if [ "${1:-}" = "-E" ]; then
                  shift
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
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "pip" ]; then
                  exit 0
                fi
                if [[ "${1:-}" == */.venv/bin/python ]] && [ "${2:-}" = "-m" ] && [ "${3:-}" = "ai_remote_runner.cli" ]; then
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
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "--version" ]; then printf 'claude-code 1.0.0\\n'; exit 0; fi
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then printf '{"loggedIn":true}\\n'; exit 0; fi
                exit 0
                """,
            )
            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(root / "state"),
                    "AI_WORKSPACE_ROOT": str(root / "workspaces"),
                    "AI_REMOTE_INSTALL_ROOT": str(root / "install"),
                    "AI_RUNNER_COMPONENTS": "claude-code",
                    "AI_TOOL_HOME": str(root_home),
                    "FAKE_SYSTEMD_DIR": str(root),
                    "ANTHROPIC_BASE_URL": "https://example.invalid",
                    "ANTHROPIC_AUTH_TOKEN": "fixture-token",
                    "CLAUDE_MODEL": "claude",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
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
            settings = json.loads((root_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "https://example.invalid")
            self.assertEqual(settings["env"]["ANTHROPIC_AUTH_TOKEN"], "fixture-token")
            self.assertEqual(settings["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
            self.assertEqual(settings["env"]["CLAUDE_CODE_ATTRIBUTION_HEADER"], "0")
            config = (root / "state" / "config.env").read_text(encoding="utf-8")
            self.assertIn("ANTHROPIC_AUTH_TOKEN=fixture-token", config)
            self.assertIn("CLAUDE_MODEL=opus\n", config)
            self.assertIn("CLAUDE_MAX_TURNS=0\n", config)
            self.assertIn("CLAUDE_API_RETRY_ATTEMPTS=3\n", config)
            self.assertIn("CLAUDE_API_RETRY_SLEEP_SECONDS=12\n", config)
            self.assertNotIn("OPENAI_API_KEY=", config)

    def test_dry_run_does_not_execute_real_provider_or_runner_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            write_executable(fakebin / "python3", "#!/usr/bin/env bash\nprintf 'python3 should not run in dry-run\\n' >&2\nexit 99\n")
            write_executable(fakebin / "claude", "#!/usr/bin/env bash\nprintf 'claude should not run in dry-run\\n' >&2\nexit 99\n")
            write_executable(fakebin / "codex", "#!/usr/bin/env bash\nprintf 'codex should not run in dry-run\\n' >&2\nexit 99\n")
            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_SERVICE_PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_INSTALL_ROOT": str(root / "install"),
                    "AI_RUNNER_COMPONENTS": "codex,telegram",
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

    def test_bootstrap_debian12_defaults_to_full_install_with_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            write_executable(fakebin / "id", "#!/usr/bin/env bash\nif [ \"${1:-}\" = \"-u\" ]; then printf '0\\n'; exit 0; fi\nexec /usr/bin/id \"$@\"\n")
            write_executable(fakebin / "apt-get", "#!/usr/bin/env bash\nexit 0\n")

            env = clean_env()
            env.update(
                {
                    "PATH": f"{fakebin}:{env.get('PATH', '')}",
                    "FFC_AI_REPO_DIR": str(ROOT),
                    "FFC_AI_NONINTERACTIVE": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "bootstrap-debian12.sh"), "--dry-run"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("selected AI_RUNNER_COMPONENTS=all,telegram", result.stdout)
            self.assertIn("stage 03: install or verify requested Claude Code provider/backend", result.stdout)
            self.assertIn("stage 04: install or verify requested Codex CLI provider", result.stdout)
            self.assertIn("stage 05: install or verify VSCode for root/full-access operation", result.stdout)
            self.assertIn("would install /etc/systemd/system/ai-telegram-bot.service", result.stdout)
            self.assertIn("Telegram token was not supplied", result.stdout)


if __name__ == "__main__":
    unittest.main()
