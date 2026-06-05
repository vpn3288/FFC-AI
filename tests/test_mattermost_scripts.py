from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import threading
import textwrap
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class MattermostScriptTests(unittest.TestCase):
    def test_install_communication_uses_latest_mattermost_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                if [ "${@: -1}" = "https://api.github.com/repos/mattermost/mattermost/releases/latest" ]; then
                  printf '{"tag_name":"v11.7.2"}\\n'
                  exit 0
                fi
                exit 0
                """,
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
                exec "$@"
                """,
            )
            write_executable(fakebin / "docker", "#!/usr/bin/env bash\nexit 0\n")
            env = os.environ.copy()
            env.update({"PATH": f"{fakebin}:{env['PATH']}"})

            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mattermost_version=11.7.2 source=github_latest minimum=10.11.0", result.stdout)
            self.assertIn("image=mattermost/mattermost-team-edition:11.7.2", result.stdout)

    def test_install_communication_rejects_old_mattermost_version(self) -> None:
        env = os.environ.copy()
        env.update({"MATTERMOST_VERSION": "10.5.3"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("below required minimum 10.11.0", result.stdout + result.stderr)

    def test_install_communication_minimum_version_cannot_be_lowered(self) -> None:
        env = os.environ.copy()
        env.update({"MATTERMOST_VERSION": "10.5.3", "MATTERMOST_MIN_VERSION": "10.0.0"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("below required minimum 10.11.0", result.stdout + result.stderr)

    def test_install_communication_rejects_image_override(self) -> None:
        env = os.environ.copy()
        env.update({"MATTERMOST_IMAGE": "mattermost/mattermost-team-edition:10.5.3"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("MATTERMOST_IMAGE override is not supported", result.stdout + result.stderr)

    def test_install_communication_rejects_image_repository_override(self) -> None:
        env = os.environ.copy()
        env.update({"MATTERMOST_IMAGE_REPOSITORY": "example/custom-mattermost"})
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("MATTERMOST_IMAGE_REPOSITORY override is not supported", result.stdout + result.stderr)

    def test_install_communication_ignores_lock_file_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            malicious_lock = root / "versions.lock"
            malicious_lock.write_text(
                "\n".join(
                    [
                        "mattermost_image_repository=example/evil",
                        "mattermost_version=10.11.0",
                        "mattermost_min_version=10.0.0",
                        "mattermost_db_image=postgres:16",
                        "mattermost_caddy_image=caddy:2",
                        "mattermost_docker_ref=example/docker",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                if [ "${@: -1}" = "https://api.github.com/repos/mattermost/mattermost/releases/latest" ]; then
                  printf '{"tag_name":"v11.7.2"}\\n'
                  exit 0
                fi
                exit 0
                """,
            )
            write_executable(fakebin / "docker", "#!/usr/bin/env bash\nexit 0\n")
            env = os.environ.copy()
            env.update({"PATH": f"{fakebin}:{env['PATH']}", "LOCK_FILE": str(malicious_lock)})
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("image=mattermost/mattermost-team-edition:11.7.2", result.stdout)
            self.assertNotIn("example/evil", result.stdout + result.stderr)

    def test_install_communication_ignores_latest_release_url_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            seen = root / "seen.log"
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "${@: -1}" >> "${SEEN_URLS:?}"
                if [ "${@: -1}" = "https://api.github.com/repos/mattermost/mattermost/releases/latest" ]; then
                  printf '{"tag_name":"v11.7.2"}\\n'
                  exit 0
                fi
                printf '{"tag_name":"v10.11.0"}\\n'
                exit 0
                """,
            )
            write_executable(fakebin / "docker", "#!/usr/bin/env bash\nexit 0\n")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "SEEN_URLS": str(seen),
                    "MATTERMOST_LATEST_RELEASE_URL": "https://evil.example/latest",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--dry-run", "--domain", "mattermost.example.test"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("mattermost_version=11.7.2 source=github_latest", result.stdout)
            self.assertIn("https://api.github.com/repos/mattermost/mattermost/releases/latest", seen.read_text(encoding="utf-8"))
            self.assertNotIn("evil.example", seen.read_text(encoding="utf-8"))

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

    def test_install_communication_ignores_native_download_url_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            install_dir = root / "mattermost"
            fakebin.mkdir()
            install_dir.mkdir()
            seen = root / "seen.log"
            log_path = root / "calls.log"
            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "docker" ]; then shift; exec docker "$@"; fi
                if [ "${1:-}" = "awk" ]; then shift; exec awk "$@"; fi
                if [ "${1:-}" = "chmod" ]; then shift; exec chmod "$@"; fi
                if [ "${1:-}" = "cp" ]; then shift; exec cp "$@"; fi
                if [ "${1:-}" = "find" ]; then shift; exec find "$@"; fi
                if [ "${1:-}" = "mkdir" ]; then shift; exec mkdir "$@"; fi
                if [ "${1:-}" = "rm" ]; then shift; exec rm "$@"; fi
                if [ "${1:-}" = "tar" ]; then shift; mkdir -p "${MATTERMOST_INSTALL_DIR:?}/mattermost/bin"; : > "${MATTERMOST_INSTALL_DIR:?}/mattermost/bin/mmctl"; chmod +x "${MATTERMOST_INSTALL_DIR:?}/mattermost/bin/mmctl"; exit 0; fi
                if [ "${1:-}" = "tee" ]; then
                  shift
                  if [ "${1:-}" = "-a" ]; then shift; exec tee -a "$@"; fi
                  if [[ "${1:-}" == /etc/systemd/system/* ]]; then cat >/dev/null; exit 0; fi
                  exec tee "$@"
                fi
                exec "$@"
                """,
            )
            write_executable(
                fakebin / "docker",
                """
                #!/usr/bin/env bash
                printf 'docker %s\\n' "$*" >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            write_executable(
                fakebin / "docker-compose",
                """
                #!/usr/bin/env bash
                printf 'docker-compose %s\\n' "$*" >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                url=""
                output=""
                args=("$@")
                index=0
                while [ "$index" -lt "${#args[@]}" ]; do
                  arg="${args[$index]}"
                  if [ "$arg" = "-o" ]; then
                    index=$((index + 1))
                    output="${args[$index]}"
                  elif [[ "$arg" == http://* || "$arg" == https://* ]]; then
                    url="$arg"
                  fi
                  index=$((index + 1))
                done
                printf '%s\\n' "$url" >> "${SEEN_URLS:?}"
                if [ "$url" = "https://api.github.com/repos/mattermost/mattermost/releases/latest" ]; then
                  printf '{"tag_name":"v11.7.2"}\\n'
                  exit 0
                fi
                if [[ "$*" == *" -fsI "* ]] || [[ "$*" == *"-fsI "* ]]; then
                  case "$url" in
                    https://releases.mattermost.com/11.7.2/mattermost-team-11.7.2-linux-arm64.tar.gz) exit 0 ;;
                    *) exit 22 ;;
                  esac
                fi
                if [ "$url" = "https://releases.mattermost.com/11.7.2/mattermost-team-11.7.2-linux-arm64.tar.gz" ] && [ -n "$output" ]; then
                  printf 'fake tarball' > "$output"
                  exit 0
                fi
                exit 22
                """,
            )
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "seq", "#!/usr/bin/env bash\n/usr/bin/seq 1 1\n")
            bootstrap = root / "bootstrap-mattermost.sh"
            write_executable(
                bootstrap,
                """
                #!/usr/bin/env bash
                printf 'bootstrap\\n' >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_TEST_ARCH": "aarch64",
                    "CALL_LOG": str(log_path),
                    "SEEN_URLS": str(seen),
                    "MATTERMOST_DOWNLOAD_URL": "https://evil.example/mattermost.tar.gz",
                    "MATTERMOST_INSTALL_DIR": str(install_dir),
                    "BOOTSTRAP_MATTERMOST_SCRIPT": str(bootstrap),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "install-communication-vps.sh"), "--domain", "mattermost.example.test"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            seen_urls = seen.read_text(encoding="utf-8")
            self.assertIn("https://releases.mattermost.com/11.7.2/mattermost-team-11.7.2-linux-arm64.tar.gz", seen_urls)
            self.assertNotIn("evil.example", seen_urls)

    def test_bootstrap_without_bridge_url_preserves_existing_slash_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            install_dir = root / "mattermost"
            fakebin.mkdir()
            install_dir.mkdir()
            (install_dir / ".env").write_text(
                "\n".join(
                    [
                        "AI_BRIDGE_SHARED_SECRET=secret",
                        "MATTERMOST_ADMIN_USERNAME=ai-admin",
                        "MATTERMOST_ADMIN_EMAIL=admin@example.test",
                        "MATTERMOST_ADMIN_PASSWORD=admin-password",
                        "MATTERMOST_SLASH_TOKEN=existing-slash-token",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (install_dir / "mattermost-objects.json").write_text(
                json.dumps(
                    {
                        "slash_command_status": "ready",
                        "slash_command_url": "http://127.0.0.1:18765/bridge/command",
                        "slash_command_token_configured": True,
                        "incoming_webhook_id": "existing-hook-id",
                    }
                ),
                encoding="utf-8",
            )
            log_path = root / "calls.log"
            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "awk" ]; then shift; exec awk "$@"; fi
                if [ "${1:-}" = "chmod" ]; then shift; exec chmod "$@"; fi
                if [ "${1:-}" = "tee" ]; then
                  shift
                  if [ "${1:-}" = "-a" ]; then shift; exec tee -a "$@"; fi
                  exec tee "$@"
                fi
                exec "$@"
                """,
            )
            write_executable(fakebin / "docker", "#!/usr/bin/env bash\nexit 1\n")
            write_executable(fakebin / "docker-compose", "#!/usr/bin/env bash\nexit 1\n")
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                method=GET
                url=""
                while [ "$#" -gt 0 ]; do
                  case "$1" in
                    -X) method="$2"; shift 2 ;;
                    -d|-H) shift 2 ;;
                    -fsS|-sS|-f|-s|-S|-i) shift ;;
                    http://*|https://*) url="$1"; shift ;;
                    *) shift ;;
                  esac
                done
                printf 'curl %s %s\\n' "$method" "$url" >> "${CALL_LOG:?}"
                case "$url" in
                  */api/v4/users/login) printf 'HTTP/1.1 200 OK\\nToken: admin-token\\n\\n{}\\n' ;;
                  */api/v4/bots*) printf '[]\\n' ;;
                  */api/v4/users/me) printf '{"id":"admin"}\\n' ;;
                  */api/v4/teams/name/ai-lab) printf '{"id":"team-id"}\\n' ;;
                  *) printf '{}\\n' ;;
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
                    "MATTERMOST_INSTALL_DIR": str(install_dir),
                    "MATTERMOST_URL": "http://127.0.0.1:8065",
                    "MATTERMOST_ADMIN_USERNAME": "ai-admin",
                    "MATTERMOST_ADMIN_EMAIL": "admin@example.test",
                    "MATTERMOST_ADMIN_PASSWORD": "admin-password",
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
            manifest = json.loads((install_dir / "mattermost-objects.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["slash_command_status"], "ready")
            self.assertEqual(manifest["slash_command_url"], "http://127.0.0.1:18765/bridge/command")
            self.assertTrue(manifest["slash_command_token_configured"])
            self.assertEqual(manifest["incoming_webhook_id"], "existing-hook-id")

    def test_install_communication_rerun_preserves_existing_env_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            install_dir = root / "mattermost"
            fakebin.mkdir()
            install_dir.mkdir()
            (install_dir / ".env").write_text(
                "\n".join(
                    [
                        "MM_DB_PASSWORD=existing-db-password",
                        "AI_BRIDGE_SHARED_SECRET=existing-bridge-secret",
                        "MATTERMOST_ADMIN_USERNAME=ai-admin",
                        "MATTERMOST_ADMIN_EMAIL=old-admin@example.test",
                        "MATTERMOST_ADMIN_PASSWORD=existing-admin-password",
                        "MATTERMOST_SLASH_TOKEN=existing-slash-token",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            log_path = root / "calls.log"
            write_executable(
                fakebin / "sudo",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${1:-}" = "docker" ]; then shift; exec docker "$@"; fi
                if [ "${1:-}" = "awk" ]; then shift; exec awk "$@"; fi
                if [ "${1:-}" = "chown" ]; then printf 'chown %s\\n' "$*" >> "${CALL_LOG:?}"; exit 0; fi
                if [ "${1:-}" = "chmod" ]; then shift; exec chmod "$@"; fi
                if [ "${1:-}" = "cp" ]; then shift; exec cp "$@"; fi
                if [ "${1:-}" = "mkdir" ]; then shift; exec mkdir "$@"; fi
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
                set -euo pipefail
                printf 'docker %s\\n' "$*" >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            write_executable(
                fakebin / "docker-compose",
                """
                #!/usr/bin/env bash
                printf 'docker-compose %s\\n' "$*" >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            write_executable(
                fakebin / "curl",
                """
                #!/usr/bin/env bash
                set -euo pipefail
                if [ "${@: -1}" = "https://api.github.com/repos/mattermost/mattermost/releases/latest" ]; then
                  printf '{"tag_name":"v11.7.2"}\\n'
                fi
                exit 0
                """,
            )
            write_executable(fakebin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "sleep", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(fakebin / "seq", "#!/usr/bin/env bash\n/usr/bin/seq 1 1\n")
            bootstrap = root / "bootstrap-mattermost.sh"
            write_executable(
                bootstrap,
                """
                #!/usr/bin/env bash
                printf 'bootstrap\\n' >> "${CALL_LOG:?}"
                exit 0
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "CALL_LOG": str(log_path),
                    "MATTERMOST_INSTALL_DIR": str(install_dir),
                    "BOOTSTRAP_MATTERMOST_SCRIPT": str(bootstrap),
                }
            )
            script = ROOT / "scripts" / "install-communication-vps.sh"
            result = subprocess.run(
                ["bash", str(script), "--domain", "mattermost.example.test"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            env_text = (install_dir / ".env").read_text(encoding="utf-8")
            self.assertIn("MM_DB_PASSWORD=existing-db-password\n", env_text)
            self.assertIn("AI_BRIDGE_SHARED_SECRET=existing-bridge-secret\n", env_text)
            self.assertIn("MATTERMOST_ADMIN_PASSWORD=existing-admin-password\n", env_text)
            self.assertIn("MATTERMOST_SLASH_TOKEN=existing-slash-token\n", env_text)
            self.assertIn("MATTERMOST_DOMAIN=mattermost.example.test\n", env_text)
            self.assertIn("2000:2000", log_path.read_text(encoding="utf-8"))

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
                if [ "${1:-}" = "-" ] && [ "$#" -eq 5 ] && [[ "${4:-}" == */install-manifest.json ]]; then
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
            manifest = json.loads((mm / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["platform_ready"])
            self.assertEqual(manifest["platform_ready_status"], "bridge_only_not_platform_validated")
            runner_manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(runner_manifest["bridge_loopback_validated"])
            self.assertFalse(runner_manifest["mattermost_command_validated"])
            self.assertNotIn("core_ready", runner_manifest)

    def test_validate_integration_requires_multiple_mattermost_commands_for_platform_ready(self) -> None:
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
                if [ "${1:-}" = "-" ] && [ "$#" -eq 5 ] && [[ "${4:-}" == */install-manifest.json ]]; then
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
            seen_args = seen.read_text(encoding="utf-8")
            self.assertIn("/ai 状态", seen_args)
            self.assertIn("/ai 帮助", seen_args)
            self.assertIn("/ai 新对话", seen_args)
            self.assertIn("/ai 压缩", seen_args)
            self.assertIn("/ai 凭据 列表", seen_args)
            manifest = json.loads((mm / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["platform_ready"])
            self.assertEqual(manifest["platform_ready_status"], "validated")
            runner_manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(runner_manifest["bridge_loopback_validated"])
            self.assertTrue(runner_manifest["mattermost_command_validated"])
            self.assertEqual(runner_manifest["integration_ready_status"], "validated")

    def test_validate_integration_failure_clears_previous_platform_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            mm = root / "mattermost"
            state.mkdir()
            mm.mkdir()
            secret = "A" * 43
            (mm / ".env").write_text(
                "\n".join(
                    [
                        f"AI_BRIDGE_SHARED_SECRET={secret}",
                        "MATTERMOST_ADMIN_USERNAME=ai-admin",
                        "MATTERMOST_ADMIN_PASSWORD=admin-password",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (mm / "mattermost-objects.json").write_text(
                json.dumps({"slash_command_url": "http://127.0.0.1:1/bridge/command"}),
                encoding="utf-8",
            )
            (mm / "install-manifest.json").write_text(
                json.dumps({"component": "mattermost-communication-platform", "platform_ready": True, "platform_ready_status": "validated"}),
                encoding="utf-8",
            )

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    return

                def _json(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None) -> None:
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    for key, value in (headers or {}).items():
                        self.send_header(key, value)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self) -> None:
                    if self.path == "/bridge/command":
                        self.rfile.read(int(self.headers.get("Content-Length", "0")))
                        self._json({"status": "accepted"})
                        return
                    if self.path == "/api/v4/users/login":
                        self.rfile.read(int(self.headers.get("Content-Length", "0")))
                        self._json({"error": "login_failed"}, status=500)
                        return
                    self._json({"error": "not_found"}, status=404)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            (mm / "mattermost-objects.json").write_text(
                json.dumps({"slash_command_url": f"{base_url}/bridge/command"}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "AI_REMOTE_STATE": str(state),
                    "MATTERMOST_INSTALL_DIR": str(mm),
                    "MATTERMOST_URL": base_url,
                    "PYTHONPATH": str(ROOT / "src"),
                }
            )
            try:
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "validate-integration.sh")],
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((mm / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["platform_ready"])
            self.assertEqual(manifest["platform_ready_status"], "validation_failed")
            runner_manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(runner_manifest["mattermost_command_validated"])
            self.assertEqual(runner_manifest["integration_ready_status"], "validation_failed")

    def test_validate_integration_executes_credential_confirmation_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            mm = root / "mattermost"
            state.mkdir()
            mm.mkdir()
            secret = "A" * 43
            (mm / ".env").write_text(
                "\n".join(
                    [
                        f"AI_BRIDGE_SHARED_SECRET={secret}",
                        "MATTERMOST_ADMIN_USERNAME=ai-admin",
                        "MATTERMOST_ADMIN_PASSWORD=admin-password",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            executed_commands: list[str] = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    return

                def _json(self, payload: dict, status: int = 200, headers: dict[str, str] | None = None) -> None:
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    for key, value in (headers or {}).items():
                        self.send_header(key, value)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self) -> None:
                    body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.path == "/bridge/command":
                        self._json({"status": "accepted"})
                        return
                    if self.path == "/api/v4/users/login":
                        self._json({}, headers={"Token": "admin-token"})
                        return
                    if self.path == "/api/v4/commands/execute":
                        command = json.loads(body.decode("utf-8"))["command"]
                        executed_commands.append(command)
                        if command.startswith("/ai 凭据 添加"):
                            inner = {
                                "status": "needs_confirmation",
                                "data": {"confirmation_token": "confirm-token"},
                            }
                        elif command == "/ai 确认 confirm-token":
                            inner = {
                                "status": "accepted",
                                "data": {"upload_path": "/bridge/credential-upload/test-token"},
                            }
                        else:
                            inner = {"status": "accepted", "data": {}}
                        self._json({"props": {"ai_remote_response": inner}})
                        return
                    self._json({"error": "not_found"}, status=404)

                def do_GET(self) -> None:
                    if self.path == "/api/v4/teams/name/ai-lab":
                        self._json({"id": "team-id"})
                        return
                    if self.path == "/api/v4/teams/team-id/channels/name/ai-ops":
                        self._json({"id": "channel-id"})
                        return
                    self._json({"error": "not_found"}, status=404)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            (mm / "mattermost-objects.json").write_text(
                json.dumps({"slash_command_url": f"{base_url}/bridge/command"}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "AI_REMOTE_STATE": str(state),
                    "MATTERMOST_INSTALL_DIR": str(mm),
                    "MATTERMOST_URL": base_url,
                    "PYTHONPATH": str(ROOT / "src"),
                }
            )
            try:
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "validate-integration.sh")],
                    text=True,
                    capture_output=True,
                    env=env,
                    check=False,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Mattermost /ai commands and credential confirmation passed", result.stdout)
            self.assertIn("/ai 凭据 添加 credential://smoke/mattermost-validation", executed_commands)
            self.assertIn("/ai 确认 confirm-token", executed_commands)
            runner_manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(runner_manifest["mattermost_command_validated"])
            platform_manifest = json.loads((mm / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(platform_manifest["platform_ready"])


if __name__ == "__main__":
    unittest.main()
