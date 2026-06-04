from __future__ import annotations

import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path

from ai_remote_runner.security import MAX_STORED_NONCES, b64url_encode, sign_body, verify_headers, NonceStore


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
            store = NonceStore(Path(tmp) / "nonces.json", ttl_seconds=MAX_STORED_NONCES + 10)
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

    def test_body_tampering_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret = b64url_encode(secrets.token_bytes(32))
            original = b'{"raw_text":"/ai status"}'
            tampered = b'{"raw_text":"/ai budget"}'
            timestamp = str(time.time())
            headers = {
                "X-AI-Bridge-Timestamp": timestamp,
                "X-AI-Bridge-Nonce": "nonce-3",
                "X-AI-Bridge-Signature": sign_body(secret, timestamp, "nonce-3", original),
            }
            ok, reason = verify_headers(secret, headers, tampered, NonceStore(Path(tmp) / "nonces.json"))
            self.assertFalse(ok)
            self.assertEqual(reason, "bad_signature")

    def test_nonce_store_rotates_when_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = NonceStore(Path(tmp) / "nonces.json", ttl_seconds=MAX_STORED_NONCES + 10)
            store.path.write_text(json.dumps({f"nonce-{index}": float(index) for index in range(MAX_STORED_NONCES)}), encoding="utf-8")
            self.assertTrue(store.check_and_store(f"nonce-{MAX_STORED_NONCES}", now=float(MAX_STORED_NONCES)))
            data = store._load()
            self.assertLessEqual(len(data), 5000)
            self.assertIn(f"nonce-{MAX_STORED_NONCES}", data)


if __name__ == "__main__":
    unittest.main()
