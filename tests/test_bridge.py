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
from urllib import error, request

from ai_remote_runner.bridge import BridgeHandler, BridgeState
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


if __name__ == "__main__":
    unittest.main()
