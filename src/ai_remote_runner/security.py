from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def sign_body(shared_secret: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    key = b64url_decode(shared_secret)
    payload = timestamp.encode("utf-8") + b"\n" + nonce.encode("utf-8") + b"\n" + raw_body
    return b64url_encode(hmac.new(key, payload, hashlib.sha256).digest())


class NonceStore:
    def __init__(self, path: Path, ttl_seconds: int = 600) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def check_and_store(self, nonce: str, now: float | None = None) -> bool:
        current = now if now is not None else time.time()
        data = {k: v for k, v in self._load().items() if current - float(v) <= self.ttl_seconds}
        if nonce in data:
            return False
        data[nonce] = current
        self.path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        return True


def verify_headers(
    shared_secret: str,
    headers: dict[str, str],
    raw_body: bytes,
    nonce_store: NonceStore,
    max_skew_seconds: int = 300,
) -> tuple[bool, str]:
    timestamp = headers.get("X-AI-Bridge-Timestamp", "")
    nonce = headers.get("X-AI-Bridge-Nonce", "")
    signature = headers.get("X-AI-Bridge-Signature", "")
    if not timestamp or not nonce or not signature:
        return False, "missing_bridge_auth_header"
    try:
        ts_value = float(timestamp)
    except ValueError:
        return False, "invalid_timestamp"
    if abs(time.time() - ts_value) > max_skew_seconds:
        return False, "timestamp_skew"
    expected = sign_body(shared_secret, timestamp, nonce, raw_body)
    if not hmac.compare_digest(expected, signature):
        return False, "bad_signature"
    if not nonce_store.check_and_store(nonce):
        return False, "replay_nonce"
    return True, "ok"
