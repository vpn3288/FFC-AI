from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

from .storage import atomic_write_json

MAX_STORED_NONCES = 10000
ROTATED_NONCES = 5000


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
        loaded = self._load()
        data = {k: v for k, v in loaded.items() if current - float(v) <= self.ttl_seconds}
        if nonce in data:
            return False
        data[nonce] = current
        if len(data) > MAX_STORED_NONCES:
            newest = sorted(data.items(), key=lambda item: float(item[1]), reverse=True)[:ROTATED_NONCES]
            data = dict(newest)
        atomic_write_json(self.path, data, ensure_ascii=True)
        return True


def verify_header_preamble(headers: dict[str, str], max_skew_seconds: int = 300) -> tuple[bool, str]:
    normalized = {key.lower(): value for key, value in headers.items()}
    timestamp = normalized.get("x-ai-bridge-timestamp", "")
    nonce = normalized.get("x-ai-bridge-nonce", "")
    signature = normalized.get("x-ai-bridge-signature", "")
    if not timestamp or not nonce or not signature:
        return False, "missing_bridge_auth_header"
    try:
        ts_value = float(timestamp)
    except ValueError:
        return False, "invalid_timestamp"
    if abs(time.time() - ts_value) > max_skew_seconds:
        return False, "timestamp_skew"
    return True, "ok"


def verify_headers(
    shared_secret: str,
    headers: dict[str, str],
    raw_body: bytes,
    nonce_store: NonceStore,
    max_skew_seconds: int = 300,
) -> tuple[bool, str]:
    ok, reason = verify_header_preamble(headers, max_skew_seconds=max_skew_seconds)
    if not ok:
        return ok, reason
    normalized = {key.lower(): value for key, value in headers.items()}
    timestamp = normalized.get("x-ai-bridge-timestamp", "")
    nonce = normalized.get("x-ai-bridge-nonce", "")
    signature = normalized.get("x-ai-bridge-signature", "")
    expected = sign_body(shared_secret, timestamp, nonce, raw_body)
    if not hmac.compare_digest(expected, signature):
        return False, "bad_signature"
    if not nonce_store.check_and_store(nonce):
        return False, "replay_nonce"
    return True, "ok"
