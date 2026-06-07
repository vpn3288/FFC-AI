from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib import error, request
from unittest.mock import patch

from ai_remote_runner.bridge import BridgeHandler, BridgeState
from ai_remote_runner.providers import ProviderResult
from ai_remote_runner.security import b64url_encode, sign_body


class BridgeHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict("os.environ", {}, clear=False)
        self._env_patcher.start()
        self.addCleanup(self._env_patcher.stop)
        for key in (
            "AI_RUNNER_PROVIDERS",
            "AI_PERMISSION_SCOPE",
            "AI_REQUIRE_SHELL_CONFIRMATION",
            "AI_TASK_RESERVED_USD",
            "OPENAI_API_KEY",
            "CODEX_BASE_URL",
            "CODEX_MODEL",
            "CODEX_HOME",
            "AI_CODEX_HOME",
            "AI_TOOL_HOME",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_MODEL",
            "VSCODE_CLAUDE_API_RETRY_ATTEMPTS",
            "VSCODE_CLAUDE_API_RETRY_SLEEP_SECONDS",
            "VSCODE_CLAUDE_MAX_TURNS",
            "VSCODE_CLAUDE_MODEL",
            "MATTERMOST_SLASH_TOKEN",
        ):
            os.environ.pop(key, None)

    def _server(self, tmp: str) -> tuple[ThreadingHTTPServer, str, str]:
        secret = b64url_encode(secrets.token_bytes(32))
        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        server.state = BridgeState(Path(tmp) / "state", secret)  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        return server, secret, f"http://{host}:{port}"

    def _post(self, url: str, secret: str, body: bytes, nonce: str | None = None, tamper_signature: bool = False) -> tuple[int, dict]:
        timestamp = str(time.time())
        actual_nonce = nonce or str(uuid.uuid4())
        signature = sign_body(secret, timestamp, actual_nonce, body)
        if tamper_signature:
            signature = "bad" + signature
        req = request.Request(
            f"{url}/bridge/command",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AI-Bridge-Timestamp": timestamp,
                "X-AI-Bridge-Nonce": actual_nonce,
                "X-AI-Bridge-Signature": signature,
            },
            method="POST",
        )
        try:
            response = request.urlopen(req, timeout=10)
            return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def _signed_request(self, url: str, secret: str, path: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timestamp = str(time.time())
        nonce = str(uuid.uuid4())
        req = request.Request(
            f"{url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AI-Bridge-Timestamp": timestamp,
                "X-AI-Bridge-Nonce": nonce,
                "X-AI-Bridge-Signature": sign_body(secret, timestamp, nonce, body),
            },
            method="POST",
        )
        response = request.urlopen(req, timeout=10)
        return json.loads(response.read().decode("utf-8"))

    def test_signed_command_and_auth_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "AI_TOOL_HOME": str(Path(tmp) / "root-home"),
                "CODEX_HOME": str(Path(tmp) / "root-home" / ".codex"),
            },
            clear=False,
        ):
            server, secret, url = self._server(tmp)
            try:
                body = json.dumps({"request_id": "r1", "raw_text": "/ai 状态"}, ensure_ascii=False).encode("utf-8")
                status, payload = self._post(url, secret, body, nonce="nonce-1")
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], "accepted")

                replay_status, replay_payload = self._post(url, secret, body, nonce="nonce-1")
                self.assertEqual(replay_status, 401)
                self.assertEqual(replay_payload["error"]["code"], "replay_nonce")

                bad_status, bad_payload = self._post(url, secret, body, nonce="nonce-2", tamper_signature=True)
                self.assertEqual(bad_status, 401)
                self.assertEqual(bad_payload["error"]["code"], "bad_signature")
            finally:
                server.shutdown()
                server.server_close()

    def test_credential_upload_token_stores_secret_publicly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, secret, url = self._server(tmp)
            try:
                issued = self._signed_request(url, secret, "/bridge/credential-upload-url", {"metadata": {"handle": "credential://test/api", "type": "api_token"}})
                self.assertEqual(issued["status"], "accepted")
                upload = request.Request(
                    f"{url}{issued['upload_path']}",
                    data=b"super-secret-value",
                    headers={"Content-Type": "text/plain"},
                    method="PUT",
                )
                response = request.urlopen(upload, timeout=10)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["status"], "accepted")
                self.assertEqual(payload["credential"]["handle"], "credential://test/api")
                self.assertEqual(payload["credential"]["secret_material"], "never returned")
                self.assertNotIn("super-secret-value", json.dumps(payload))
            finally:
                server.shutdown()
                server.server_close()

    def test_confirmation_command_executes_pending_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, secret, url = self._server(tmp)
            try:
                body = json.dumps({"request_id": "confirm-1", "raw_text": "/ai 全局 替换 hello"}, ensure_ascii=False).encode("utf-8")
                status, payload = self._post(url, secret, body, nonce="confirm-nonce-1")
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], "needs_confirmation")
                token = payload["data"]["confirmation_token"]
                confirm_body = json.dumps({"request_id": "confirm-2", "raw_text": f"/ai 确认 {token}"}, ensure_ascii=False).encode("utf-8")
                confirmed_status, confirmed = self._post(url, secret, confirm_body, nonce="confirm-nonce-2")
                self.assertEqual(confirmed_status, 200)
                self.assertEqual(confirmed["status"], "accepted")
            finally:
                server.shutdown()
                server.server_close()

    def test_confirmed_credential_add_returns_upload_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, secret, url = self._server(tmp)
            try:
                body = json.dumps({"request_id": "cred-1", "raw_text": "/ai 凭据 添加 credential://phone/test"}, ensure_ascii=False).encode("utf-8")
                _, pending = self._post(url, secret, body, nonce="cred-nonce-1")
                token = pending["data"]["confirmation_token"]
                confirm_body = json.dumps({"request_id": "cred-2", "raw_text": f"/ai 确认 {token}"}, ensure_ascii=False).encode("utf-8")
                _, confirmed = self._post(url, secret, confirm_body, nonce="cred-nonce-2")
                self.assertEqual(confirmed["status"], "accepted")
                self.assertEqual(confirmed["data"]["upload_method"], "PUT")
                self.assertTrue(confirmed["data"]["upload_path"].startswith("/bridge/credential-upload/"))
            finally:
                server.shutdown()
                server.server_close()

    def test_exact_bare_slash_returns_command_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server, secret, url = self._server(tmp)
            try:
                body = json.dumps({"request_id": "slash-1", "raw_text": "/"}, ensure_ascii=False).encode("utf-8")
                with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                    status, payload = self._post(url, secret, body, nonce="slash-nonce")
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], "accepted")
                self.assertIn("items", payload["data"])
                invoke.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_slash_command_uses_platform_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"MATTERMOST_SLASH_TOKEN": "slash-secret", "AI_WORKSPACE_ROOT": str(Path(tmp) / "workspaces")}
        ):
            server, _, url = self._server(tmp)
            try:
                body = urlencode(
                    {
                        "token": "slash-secret",
                        "team_id": "team",
                        "channel_id": "channel",
                        "user_id": "user",
                        "user_name": "alice",
                        "command": "/ai",
                        "text": "状态",
                        "trigger_id": "trigger-1",
                    }
                ).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                response = request.urlopen(req, timeout=10)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["response_type"], "ephemeral")
                self.assertEqual(payload["props"]["ai_remote_response"]["status"], "accepted")
                self.assertEqual(payload["props"]["ai_remote_response"]["data"]["default_workspace"], "default")
                self.assertIn("recent_runs", payload["props"]["ai_remote_response"]["data"])
                self.assertIn("状态已生成", payload["text"])
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_help_text_renders_visible_command_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"MATTERMOST_SLASH_TOKEN": "slash-secret"}):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "slash-secret", "command": "/ai", "text": "帮助", "trigger_id": "help-1"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                response = request.urlopen(req, timeout=10)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertIn("/ai 状态", payload["text"])
                self.assertIn("显示当前运行", payload["text"])
                self.assertIn("索引已生成", payload["text"])
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_slash_command_rejects_bad_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"MATTERMOST_SLASH_TOKEN": "slash-secret"}):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "wrong", "command": "/ai", "text": "状态"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with self.assertRaises(error.HTTPError) as raised:
                    request.urlopen(req, timeout=10)
                self.assertEqual(raised.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_unknown_slash_text_runs_as_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"MATTERMOST_SLASH_TOKEN": "slash-secret", "AI_WORKSPACE_ROOT": str(Path(tmp) / "workspaces")}
        ):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "slash-secret", "command": "/ai", "text": "reply with hi", "trigger_id": "task-1"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                fake = ProviderResult("run", "claude-code", "completed", "hi", None, 0)
                with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                    response = request.urlopen(req, timeout=10)
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(payload["props"]["ai_remote_response"]["status"], "accepted")
                    self.assertIn("后台运行", payload["text"])
                    deadline = time.time() + 5
                    while time.time() < deadline and invoke.call_count == 0:
                        time.sleep(0.05)
                    invoke.assert_called_once()
                    deadline = time.time() + 5
                    cached = {}
                    while time.time() < deadline:
                        cached = server.state.responses().get("task-1", {})  # type: ignore[attr-defined]
                        if cached.get("data", {}).get("output") == "hi":
                            break
                        time.sleep(0.05)
                self.assertEqual(cached.get("data", {}).get("output"), "hi")
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_management_only_text_does_not_start_background_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "MATTERMOST_SLASH_TOKEN": "slash-secret",
                "AI_WORKSPACE_ROOT": str(Path(tmp) / "workspaces"),
                "AI_RUNNER_PROVIDERS": "",
            },
        ):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "slash-secret", "command": "/ai", "text": "reply with hi", "trigger_id": "task-management-only"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                    response = request.urlopen(req, timeout=10)
                    payload = json.loads(response.read().decode("utf-8"))
                ai_response = payload["props"]["ai_remote_response"]
                self.assertEqual(ai_response["status"], "error")
                self.assertEqual(ai_response["error"]["code"], "ai_provider_not_configured")
                self.assertIn("没有配置 Claude Code、VSCode 或 Codex", payload["text"])
                self.assertNotIn("后台运行", payload["text"])
                invoke.assert_not_called()
                events_path = Path(tmp) / "state" / "events.jsonl"
                if events_path.exists():
                    events = events_path.read_text(encoding="utf-8")
                    self.assertNotIn('"provider": "claude-code"', events)
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_background_task_emits_running_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "MATTERMOST_SLASH_TOKEN": "slash-secret",
                "AI_WORKSPACE_ROOT": str(Path(tmp) / "workspaces"),
                "AI_MATTERMOST_HEARTBEAT_SECONDS": "1",
            },
        ):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "slash-secret", "command": "/ai", "text": "slow task", "trigger_id": "task-heartbeat"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )

                def slow_provider(*args: object, **kwargs: object) -> ProviderResult:
                    time.sleep(1.4)
                    return ProviderResult("run", "claude-code", "completed", "done", None, 0)

                with patch("ai_remote_runner.executor.invoke_claude", side_effect=slow_provider):
                    response = request.urlopen(req, timeout=10)
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(payload["props"]["ai_remote_response"]["status"], "accepted")
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        events_path = Path(tmp) / "state" / "events.jsonl"
                        if events_path.exists() and '"phase": "running"' in events_path.read_text(encoding="utf-8"):
                            break
                        time.sleep(0.05)
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        cached = server.state.responses().get("task-heartbeat", {})  # type: ignore[attr-defined]
                        if cached.get("data", {}).get("output") == "done":
                            break
                        time.sleep(0.05)
                events = (Path(tmp) / "state" / "events.jsonl").read_text(encoding="utf-8")
                self.assertIn('"phase": "running"', events)
                self.assertIn('"provider": "claude-code"', events)
                self.assertNotIn('"provider": "runner"', events)
                self.assertIn("不是卡死", events)
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_background_needs_confirmation_can_be_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "MATTERMOST_SLASH_TOKEN": "slash-secret",
                "AI_WORKSPACE_ROOT": str(Path(tmp) / "workspaces"),
                "AI_REQUIRE_SHELL_CONFIRMATION": "1",
            },
        ):
            server, _, url = self._server(tmp)
            try:
                for trigger, text in (("shell-mode", "shell模式 开启"), ("shell-task", "run shell task")):
                    body = urlencode({"token": "slash-secret", "command": "/ai", "text": text, "trigger_id": trigger}).encode("utf-8")
                    request.urlopen(
                        request.Request(
                            f"{url}/bridge/command",
                            data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            method="POST",
                        ),
                        timeout=10,
                    ).read()
                deadline = time.time() + 5
                cached = {}
                while time.time() < deadline:
                    cached = server.state.responses().get("shell-task", {})  # type: ignore[attr-defined]
                    if cached.get("status") == "needs_confirmation":
                        break
                    time.sleep(0.05)
                token = cached.get("data", {}).get("confirmation_token")
                self.assertTrue(token)
                fake = ProviderResult("run", "claude-code", "completed", "confirmed", None, 0)
                with patch("ai_remote_runner.executor.invoke_claude", return_value=fake) as invoke:
                    body = urlencode({"token": "slash-secret", "command": "/ai", "text": f"确认 {token}", "trigger_id": "shell-confirm"}).encode("utf-8")
                    response = request.urlopen(
                        request.Request(
                            f"{url}/bridge/command",
                            data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            method="POST",
                        ),
                        timeout=10,
                    )
                    payload = json.loads(response.read().decode("utf-8"))
                    self.assertIn("confirmed", payload["text"])
                invoke.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()

    def test_mattermost_cancel_response_includes_non_kill_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"MATTERMOST_SLASH_TOKEN": "slash-secret"}):
            server, _, url = self._server(tmp)
            try:
                body = urlencode({"token": "slash-secret", "command": "/ai", "text": "取消", "trigger_id": "cancel-1"}).encode("utf-8")
                req = request.Request(
                    f"{url}/bridge/command",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                response = request.urlopen(req, timeout=10)
                payload = json.loads(response.read().decode("utf-8"))
                self.assertIn("不会强制终止", payload["text"])
            finally:
                server.shutdown()
                server.server_close()

    def test_sensitive_provider_config_command_is_redacted_in_command_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "AI_TOOL_HOME": str(Path(tmp) / "root-home"),
                "CODEX_HOME": str(Path(tmp) / "root-home" / ".codex"),
            },
            clear=False,
        ):
            server, secret, url = self._server(tmp)
            try:
                secret_key = "sk-" + "a" * 24
                body = json.dumps({"request_id": "secret-cmd", "raw_text": f"/ai 密钥 设置 codex {secret_key}"}, ensure_ascii=False).encode("utf-8")
                status, payload = self._post(url, secret, body, nonce="secret-command")
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], "accepted")
                commands_log = (Path(tmp) / "state" / "bridge-commands.jsonl").read_text(encoding="utf-8")
                self.assertNotIn(secret_key, commands_log)
                self.assertNotIn(secret_key, json.dumps(payload, ensure_ascii=False))
                self.assertIn("sk-a", commands_log)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
