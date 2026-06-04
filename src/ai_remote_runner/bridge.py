from __future__ import annotations

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .commands import parse_command
from .security import NonceStore, verify_headers


class BridgeState:
    def __init__(self, root: Path, shared_secret: str) -> None:
        self.root = root
        self.shared_secret = shared_secret
        self.commands_path = root / "bridge-commands.jsonl"
        self.events_path = root / "bridge-events.jsonl"
        self.nonce_store = NonceStore(root / "bridge-nonces.json")
        root.mkdir(parents=True, exist_ok=True)

    def append_jsonl(self, path: Path, item: dict) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "AIRemoteBridge/0.1"

    @property
    def state(self) -> BridgeState:
        return self.server.state  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _auth(self, body: bytes) -> tuple[bool, str]:
        headers = {key: self.headers[key] for key in self.headers}
        return verify_headers(self.state.shared_secret, headers, body, self.state.nonce_store)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/bridge/health":
            self._json(200, {"status": "ok", "time": int(time.time())})
            return
        if path == "/bridge/poll":
            self._json(200, {"commands": []})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body()
        ok, reason = self._auth(body)
        if not ok:
            self._json(401, {"status": "rejected", "error": {"code": reason, "detail": reason}})
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"status": "error", "error": {"code": "invalid_json", "detail": "invalid_json"}})
            return

        if path == "/bridge/command":
            parsed = parse_command(payload.get("raw_text", ""))
            request_id = payload.get("request_id") or str(uuid.uuid4())
            item = dict(payload)
            item.update(parsed)
            item["request_id"] = request_id
            self.state.append_jsonl(self.state.commands_path, item)
            self._json(
                200,
                {
                    "request_id": request_id,
                    "status": parsed["status"],
                    "run_id": str(uuid.uuid4()) if parsed["status"] == "accepted" else None,
                    "message_zh": "已接受" if parsed["status"] == "accepted" else "命令被拒绝",
                    "error": None if parsed["status"] == "accepted" else {"code": parsed["error"], "detail": parsed["error"]},
                },
            )
            return

        if path == "/bridge/event":
            self.state.append_jsonl(self.state.events_path, payload)
            self._json(200, {"status": "accepted"})
            return

        self._json(404, {"error": "not_found"})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    secret = os.environ["AI_BRIDGE_SHARED_SECRET"]
    root = Path(os.environ.get("AI_REMOTE_STATE", "/var/lib/ai-remote-runner"))
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.state = BridgeState(root, secret)  # type: ignore[attr-defined]
    server.serve_forever()
