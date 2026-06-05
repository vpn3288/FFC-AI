from __future__ import annotations

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


class MattermostScriptTests(unittest.TestCase):
    def test_bootstrap_allows_internal_bridge_and_syncs_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            install_dir = root / "mattermost"
            fakebin.mkdir()
            install_dir.mkdir()
            (install_dir / ".env").write_text("AI_BRIDGE_SHARED_SECRET=secret\n", encoding="utf-8")
            log_path = root / "calls.log"
            command_state = root / "command-created"

            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "awk" ]; then shift; exec awk "$@"; fi
                if [ "${1:-}" = "cp" ]; then shift; exec cp "$@"; fi
                if [ "${1:-}" = "chmod" ]; then shift; exec chmod "$@"; fi
                if [ "${1:-}" = "tee" ]; then
                  shift
                  if [ "${1:-}" = "-a" ]; then shift; exec tee -a "$@"; fi
                  exec tee "$@"
                fi
                exec "$@"
                """,
            )
            write_executable(
                fakebin / "docker",
                """
                #!/usr/bin/env bash
                exit 1
                """,
            )
            write_executable(
                fakebin / "docker-compose",
                """
                #!/usr/bin/env bash
                exit 1
                """,
            )
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                method=GET
                data=""
                url=""
                while [ "$#" -gt 0 ]; do
                  case "$1" in
                    -X) method="$2"; shift 2 ;;
                    -d) data="$2"; shift 2 ;;
                    -H) shift 2 ;;
                    -fsS|-sS|-f|-s|-S|-i) shift ;;
                    http://*|https://*) url="$1"; shift ;;
                    *) shift ;;
                  esac
                done
                printf 'curl %s %s %s\\n' "$method" "$url" "$data" >> "${CALL_LOG:?}"
                case "$url" in
                  */api/v4/users/login)
                    printf 'HTTP/1.1 200 OK\\nToken: admin-token\\n\\n{}\\n'
                    ;;
                  */api/v4/bots*)
                    printf '[]\\n'
                    ;;
                  */api/v4/users/me)
                    printf '{"id":"admin"}\\n'
                    ;;
                  */api/v4/teams/name/ai-lab)
                    printf '{"id":"team-id"}\\n'
                    ;;
                  */api/v4/commands?team_id=team-id)
                    if [ -f "${COMMAND_STATE:?}" ]; then
                      printf '[{"id":"command-id","trigger":"ai","url":"http://127.0.0.1:18765/bridge/command"}]\\n'
                    else
                      printf '[]\\n'
                    fi
                    ;;
                  */api/v4/commands)
                    : > "${COMMAND_STATE:?}"
                    printf '{"id":"command-id"}\\n'
                    ;;
                  */api/v4/commands/command-id)
                    printf '{"id":"command-id","token":"slash-token"}\\n'
                    ;;
                  */api/v4/teams/team-id/channels/name/ai-status)
                    printf '{"id":"status-channel"}\\n'
                    ;;
                  */api/v4/hooks/incoming)
                    printf '{"id":"hook-id"}\\n'
                    ;;
                  *)
                    printf '{}\\n'
                    ;;
                esac
                """,
            )
            write_executable(
                fakebin / "mmctl",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "--local" ]; then shift; fi
                printf 'mmctl %s\\n' "$*" >> "${CALL_LOG:?}"
                case "$*" in
                  version*) exit 0 ;;
                  "user search ai-admin") exit 0 ;;
                  "team list") printf 'ai-lab\\n'; exit 0 ;;
                  "channel list ai-lab") printf 'town-square\\nai-ops\\nai-status\\nai-reviews\\nai-errors\\nai-archive\\n'; exit 0 ;;
                  "config get ServiceSettings.AllowedUntrustedInternalConnections") printf '""\\n'; exit 0 ;;
                  *) exit 0 ;;
                esac
                """,
            )
            mattermost_dir = install_dir / "mattermost" / "bin"
            mattermost_dir.mkdir(parents=True)
            (mattermost_dir / "mmctl").symlink_to(fakebin / "mmctl")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "CALL_LOG": str(log_path),
                    "COMMAND_STATE": str(command_state),
                    "MATTERMOST_INSTALL_DIR": str(install_dir),
                    "MATTERMOST_URL": "http://127.0.0.1:8065",
                    "MATTERMOST_ADMIN_USERNAME": "ai-admin",
                    "MATTERMOST_ADMIN_EMAIL": "admin@example.test",
                    "MATTERMOST_ADMIN_PASSWORD": "admin-password",
                    "BRIDGE_COMMAND_URL": "http://127.0.0.1:18765/bridge/command",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "bootstrap-mattermost.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log_path.read_text(encoding="utf-8")
            self.assertIn("mmctl user change-password ai-admin --password admin-password", calls)
            self.assertIn("mmctl config set ServiceSettings.AllowedUntrustedInternalConnections 127.0.0.1", calls)
            self.assertIn("mmctl team users add ai-lab ai-admin ai-bridge", calls)
            env_text = (install_dir / ".env").read_text(encoding="utf-8")
            self.assertIn("MATTERMOST_SLASH_TOKEN=slash-token\n", env_text)

    def test_validate_integration_reads_secret_from_mattermost_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            mm = root / "mattermost"
            fakebin = root / "bin"
            state.mkdir()
            mm.mkdir()
            fakebin.mkdir()
            (mm / ".env").write_text(
                "\n".join(
                    [
                        "AI_BRIDGE_SHARED_SECRET=" + "A" * 43,
                        "MATTERMOST_ADMIN_USERNAME=ai-admin",
                        "MATTERMOST_ADMIN_PASSWORD=admin-password",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (mm / "mattermost-objects.json").write_text(
                '{"slash_command_url":"http://127.0.0.1:18765/bridge/command"}',
                encoding="utf-8",
            )
            seen = root / "seen-args"
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> "${SEEN_ARGS:?}"
                if [ "${1:-}" = "-" ] && [ "$#" -eq 3 ] && [[ "${2:-}" == */mattermost-objects.json ]]; then
                  exec /usr/bin/python3 "$@"
                fi
                if [ "${1:-}" = "-" ] && [ "$#" -eq 3 ] && [[ "${2:-}" == */install-manifest.json ]]; then
                  exec /usr/bin/python3 "$@"
                fi
                if [ "${1:-}" = "-" ]; then
                  cat >/dev/null
                  exit 0
                fi
                exec /usr/bin/python3 "$@"
                """,
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "SEEN_ARGS": str(seen),
                    "AI_REMOTE_STATE": str(state),
                    "MATTERMOST_INSTALL_DIR": str(mm),
                    "VALIDATE_MATTERMOST_COMMAND": "false",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "validate-integration.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("[validate-integration] bridge loopback passed", result.stdout)
            seen_args = seen.read_text(encoding="utf-8")
            self.assertIn("- http://127.0.0.1:18765/bridge/command " + "A" * 43, seen_args)
            self.assertTrue((mm / "install-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
