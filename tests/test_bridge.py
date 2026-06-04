from __future__ import annotations

import json
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
        with tempfile.TemporaryDirectory() as tmp:
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
                self.assertEqual(payload["text"], "hi")
                invoke.assert_called_once()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
