from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ValidateCoreReadyScriptTests(unittest.TestCase):
    def test_missing_provider_configuration_fails_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            workspaces = root / "workspaces"
            fakebin = root / "bin"
            calls = root / "calls.txt"
            state.mkdir()
            workspaces.mkdir()
            fakebin.mkdir()
            (state / "config.env").write_text("AI_BRIDGE_SHARED_SECRET=" + "A" * 43 + "\n", encoding="utf-8")
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> "${CALLS:?}"
                exec /usr/bin/python3 "$@"
                """,
            )

            env = os.environ.copy()
            env.pop("AI_RUNNER_PROVIDERS", None)
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "BRIDGE_COMMAND_URL": "http://127.0.0.1:1/bridge/command",
                    "CALLS": str(calls),
                    "PYTHONPATH": str(ROOT / "src"),
                    "AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "validate-core-ready.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("AI_RUNNER_PROVIDERS is not configured", result.stderr)
            seen = calls.read_text(encoding="utf-8") if calls.exists() else ""
            self.assertNotIn("provider-smoke", seen)

    def test_core_ready_requires_full_access_provider_smokes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            workspaces = root / "workspaces"
            fakebin = root / "bin"
            calls = root / "calls.txt"
            state.mkdir()
            workspaces.mkdir()
            fakebin.mkdir()
            secret = "A" * 43
            (state / "config.env").write_text(
                "\n".join(
                    [
                        "AI_RUNNER_PROVIDERS=claude-code,codex",
                        f"AI_BRIDGE_SHARED_SECRET={secret}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (state / "install-manifest.json").write_text("{}", encoding="utf-8")
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "auth" ] && [ "${2:-}" = "status" ]; then
                  printf '{"loggedIn":true}\\n'
                  exit 0
                fi
                exit 0
                """,
            )
            write_executable(fakebin / "codex", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> "${CALLS:?}"
                if [ "${1:-}" = "-m" ] && [ "${2:-}" = "ai_remote_runner.cli" ] && [ "${3:-}" = "provider-smoke" ]; then
                  provider=''
                  token=''
                  workspace=''
                  while [ "$#" -gt 0 ]; do
                    if [ "$1" = "--provider" ]; then provider="$2"; shift 2; continue; fi
                    if [ "$1" = "--prompt-file" ]; then
                      token="$(grep -o 'FFC_FULL_ACCESS_SMOKE_[0-9_]*' "$2" | head -n 1)"
                      shift 2
                      continue
                    fi
                    if [ "$1" = "--prompt" ]; then
                      token="$(printf '%s' "$2" | grep -o 'FFC_FULL_ACCESS_SMOKE_[0-9_]*' | head -n 1)"
                      shift 2
                      continue
                    fi
                    if [ "$1" = "--workspace" ]; then workspace="$2"; shift 2; continue; fi
                    shift
                  done
                  mkdir -p "$workspace"
                  if [ "$provider" = "claude-code" ]; then
                    tmp_root="/tmp/ffc-ai-full-access-smoke-$token"
                    printf '%s\\n' "$token" > "$workspace/full-access-smoke-claude.txt"
                    mkdir -p "$tmp_root/claude-venv/bin"
                    printf '%s\\n' "$token" > "$tmp_root/claude-tmp.txt"
                    printf 'NETWORK_OK\\n' > "$workspace/full-access-smoke-claude-net.txt"
                    printf '#!/usr/bin/env bash\\nexit 0\\n' > "$tmp_root/claude-venv/bin/python"
                    chmod +x "$tmp_root/claude-venv/bin/python"
                  elif [ "$provider" = "codex" ]; then
                    tmp_root="/tmp/ffc-ai-full-access-smoke-$token"
                    printf '%s\\n' "$token" > "$workspace/full-access-smoke-codex.txt"
                    mkdir -p "$tmp_root/codex-venv/bin"
                    printf '%s\\n' "$token" > "$tmp_root/codex-tmp.txt"
                    printf 'NETWORK_OK\\n' > "$workspace/full-access-smoke-codex-net.txt"
                    printf '#!/usr/bin/env bash\\nexit 0\\n' > "$tmp_root/codex-venv/bin/python"
                    chmod +x "$tmp_root/codex-venv/bin/python"
                  fi
                  exit 0
                fi
                exec /usr/bin/python3 "$@"
                """,
            )

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    pass

                def do_POST(self) -> None:
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "accepted"}).encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.update(
                    {
                        "PATH": f"{fakebin}:/usr/bin:/bin",
                        "AI_REMOTE_STATE": str(state),
                        "AI_WORKSPACE_ROOT": str(workspaces),
                        "BRIDGE_COMMAND_URL": f"http://127.0.0.1:{server.server_port}/bridge/command",
                        "CALLS": str(calls),
                        "PYTHONPATH": str(ROOT / "src"),
                        "AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT": "true",
                    }
                )
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "validate-core-ready.sh")],
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
            seen = calls.read_text(encoding="utf-8")
            self.assertIn("provider-smoke --provider claude-code", seen)
            self.assertIn("provider-smoke --provider codex", seen)
            self.assertIn("--prompt-file", seen)
            self.assertIn("--reserved-usd 0.50", seen)
            self.assertNotIn("--expect-contains", seen)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["core_ready"])
            self.assertTrue(manifest["provider_full_access_smoke_validated"])

    def test_core_ready_fails_when_codex_smoke_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            workspaces = root / "workspaces"
            fakebin = root / "bin"
            state.mkdir()
            workspaces.mkdir()
            fakebin.mkdir()
            secret = "A" * 43
            (state / "config.env").write_text(
                "\n".join(
                    [
                        "AI_RUNNER_PROVIDERS=codex",
                        f"AI_BRIDGE_SHARED_SECRET={secret}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (state / "install-manifest.json").write_text("{}", encoding="utf-8")
            write_executable(fakebin / "codex", "#!/usr/bin/env bash\nexit 0\n")
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "-m" ] && [ "${2:-}" = "ai_remote_runner.cli" ] && [ "${3:-}" = "provider-smoke" ]; then
                  exit 1
                fi
                exec /usr/bin/python3 "$@"
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REMOTE_STATE": str(state),
                    "AI_WORKSPACE_ROOT": str(workspaces),
                    "BRIDGE_COMMAND_URL": "http://127.0.0.1:1/bridge/command",
                    "PYTHONPATH": str(ROOT / "src"),
                    "AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT": "true",
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "validate-core-ready.sh")],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("codex full-access smoke step failed: file-tmp", result.stderr)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("core_ready", manifest)

    def test_management_only_runner_skips_provider_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            workspaces = root / "workspaces"
            fakebin = root / "bin"
            calls = root / "calls.txt"
            state.mkdir()
            workspaces.mkdir()
            fakebin.mkdir()
            secret = "A" * 43
            (state / "config.env").write_text(
                "\n".join(
                    [
                        "AI_RUNNER_PROVIDERS=",
                        f"AI_BRIDGE_SHARED_SECRET={secret}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (state / "install-manifest.json").write_text(json.dumps({"requested_components": "vscode,telegram"}), encoding="utf-8")
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> "${CALLS:?}"
                exec /usr/bin/python3 "$@"
                """,
            )

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    pass

                def do_POST(self) -> None:
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "accepted"}).encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.update(
                    {
                        "PATH": f"{fakebin}:/usr/bin:/bin",
                        "AI_REMOTE_STATE": str(state),
                        "AI_WORKSPACE_ROOT": str(workspaces),
                        "BRIDGE_COMMAND_URL": f"http://127.0.0.1:{server.server_port}/bridge/command",
                        "CALLS": str(calls),
                        "PYTHONPATH": str(ROOT / "src"),
                        "AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT": "true",
                    }
                )
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "validate-core-ready.sh")],
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
            self.assertIn("management-only runner", result.stdout)
            self.assertNotIn("provider-smoke", calls.read_text(encoding="utf-8"))
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["core_ready"])
            self.assertFalse(manifest["provider_full_access_smoke_validated"])
            self.assertIn("management-only", manifest["core_ready_note"])

    def test_manifest_configured_providers_fallback_drives_management_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            workspaces = root / "workspaces"
            fakebin = root / "bin"
            calls = root / "calls.txt"
            state.mkdir()
            workspaces.mkdir()
            fakebin.mkdir()
            secret = "A" * 43
            (state / "config.env").write_text(f"AI_BRIDGE_SHARED_SECRET={secret}\n", encoding="utf-8")
            (state / "install-manifest.json").write_text(
                json.dumps({"requested_components": "vscode,telegram", "configured_providers": ""}),
                encoding="utf-8",
            )
            write_executable(
                fakebin / "python3",
                """
                #!/usr/bin/env bash
                printf '%s\\n' "$*" >> "${CALLS:?}"
                exec /usr/bin/python3 "$@"
                """,
            )
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf 'unexpected claude probe %s\\n' "$*" >> "${CALLS:?}"
                exit 99
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                printf 'unexpected codex probe %s\\n' "$*" >> "${CALLS:?}"
                exit 99
                """,
            )

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, format: str, *args: object) -> None:
                    pass

                def do_POST(self) -> None:
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "accepted"}).encode())

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env.pop("AI_RUNNER_PROVIDERS", None)
                env.update(
                    {
                        "PATH": f"{fakebin}:/usr/bin:/bin",
                        "AI_REMOTE_STATE": str(state),
                        "AI_WORKSPACE_ROOT": str(workspaces),
                        "BRIDGE_COMMAND_URL": f"http://127.0.0.1:{server.server_port}/bridge/command",
                        "CALLS": str(calls),
                        "PYTHONPATH": str(ROOT / "src"),
                        "AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT": "true",
                    }
                )
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "validate-core-ready.sh")],
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
            self.assertIn("management-only runner", result.stdout)
            seen = calls.read_text(encoding="utf-8")
            self.assertNotIn("provider-smoke", seen)
            self.assertNotIn("unexpected claude probe", seen)
            self.assertNotIn("unexpected codex probe", seen)


if __name__ == "__main__":
    unittest.main()
