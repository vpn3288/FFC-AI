from __future__ import annotations

import secrets
import tempfile
import time
import unittest
from pathlib import Path

from ai_remote_runner.security import b64url_encode, sign_body, verify_headers, NonceStore


class SecurityTests(unittest.TestCase):
    def test_hmac_validates_and_rejects_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = b64url_encode(secrets.token_bytes(32))
            body = '{"raw_text":"/ai 状态"}'.encode("utf-8")
            timestamp = str(time.time())
            nonce = "nonce-1"
            signature = sign_body(secret, timestamp, nonce, body)
            headers = {
                "X-AI-Bridge-Timestamp": timestamp,
                "X-AI-Bridge-Nonce": nonce,
                "X-AI-Bridge-Signature": signature,
            }
            store = NonceStore(Path(tmp) / "nonces.json")
            self.assertEqual(verify_headers(secret, headers, body, store), (True, "ok"))
            self.assertEqual(verify_headers(secret, headers, body, store), (False, "replay_nonce"))

    def test_bad_signature_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = b64url_encode(secrets.token_bytes(32))
            headers = {
                "X-AI-Bridge-Timestamp": str(time.time()),
                "X-AI-Bridge-Nonce": "nonce-2",
                "X-AI-Bridge-Signature": "bad",
            }
            ok, reason = verify_headers(secret, headers, b"{}", NonceStore(Path(tmp) / "nonces.json"))
            self.assertFalse(ok)
            self.assertEqual(reason, "bad_signature")


if __name__ == "__main__":
    unittest.main()
