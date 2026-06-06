from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .commands import parse_command
from .executor import RunnerRuntime, execute
from .events import status_event
from .phone_render import render_response_text
from .runtime_config import redact_secret
from .security import NonceStore, verify_header_preamble, verify_headers
from .storage import atomic_write_json


MAX_BODY_BYTES = int(os.environ.get("AI_BRIDGE_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
SECRET_VALUE_RE = re.compile(r"\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{12,}|[0-9]{8,}:[A-Za-z0-9_-]{20,})\b")


class BridgeState:
    def __init__(self, root: Path, shared_secret: str) -> None:
        self.root = root
        self.shared_secret = shared_secret
        self.commands_path = root / "bridge-commands.jsonl"
        self.events_path = root / "bridge-events.jsonl"
        self.responses_path = root / "bridge-responses.json"
        self.confirmations_path = root / "bridge-confirmations.json"
        self.credential_uploads_path = root / "credential-upload-tokens.json"
        self.nonce_store = NonceStore(root / "bridge-nonces.json")
        self.mattermost_slash_token = os.environ.get("MATTERMOST_SLASH_TOKEN", "")
        self.runtime = RunnerRuntime(root, Path(os.environ.get("AI_WORKSPACE_ROOT", "/srv/ai-workspaces")), os.environ.get("MATTERMOST_WEBHOOK_URL"))
        root.mkdir(parents=True, exist_ok=True)

    def append_jsonl(self, path: Path, item: dict) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    def responses(self) -> dict[str, dict]:
        if not self.responses_path.exists():
            return {}
        try:
            raw = json.loads(self.responses_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        responses = {}
        for request_id, value in raw.items():
            responses[request_id] = value.get("response", value) if isinstance(value, dict) else value
        return responses

    def save_response(self, request_id: str, response: dict) -> None:
        now = int(time.time())
        raw = {}
        if self.responses_path.exists():
            try:
                raw = json.loads(self.responses_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raw = {}
        data = {
            key: value
            for key, value in raw.items()
            if isinstance(value, dict) and now - int(value.get("cached_at", now)) <= 86400
        }
        data[request_id] = {"cached_at": now, "response": response}
        atomic_write_json(self.responses_path, data)

    def confirmations(self) -> dict[str, dict]:
        if not self.confirmations_path.exists():
            return {}
        try:
            raw = json.loads(self.confirmations_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        now = int(time.time())
        return {token: item for token, item in raw.items() if now - int(item.get("created_at", 0)) <= 600}

    def save_confirmations(self, data: dict[str, dict]) -> None:
        atomic_write_json(self.confirmations_path, data)

    def credential_uploads(self) -> dict[str, dict]:
        if not self.credential_uploads_path.exists():
            return {}
        try:
            return json.loads(self.credential_uploads_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save_credential_uploads(self, data: dict[str, dict]) -> None:
        atomic_write_json(self.credential_uploads_path, data)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "AIRemoteBridge/0.1"

    def log_message(self, format: str, *args: object) -> None:
        if os.environ.get("AI_BRIDGE_ACCESS_LOG") == "1":
            super().log_message(format, *args)

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
        if length > MAX_BODY_BYTES:
            raise ValueError("request_too_large")
        return self.rfile.read(length)

    def _auth(self, body: bytes) -> tuple[bool, str]:
        headers = {key: self.headers[key] for key in self.headers}
        return verify_headers(self.state.shared_secret, headers, body, self.state.nonce_store)

    def _preauth(self) -> tuple[bool, str]:
        headers = {key: self.headers[key] for key in self.headers}
        return verify_header_preamble(headers)

    def _mattermost_payload(self, body: bytes) -> tuple[bool, dict | str]:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        token = form.get("token", [""])[0]
        if not self.state.mattermost_slash_token:
            return False, "mattermost_slash_token_not_configured"
        if token != self.state.mattermost_slash_token:
            return False, "bad_mattermost_slash_token"
        command = form.get("command", ["/ai"])[0] or "/ai"
        text = form.get("text", [""])[0]
        raw_text = f"{command} {text}".strip()
        request_id = form.get("trigger_id", [""])[0] or str(uuid.uuid4())
        return True, {
            "request_id": request_id,
            "platform": "mattermost",
            "team_id": form.get("team_id", [""])[0],
            "channel_id": form.get("channel_id", [""])[0],
            "sender_id": form.get("user_id", [""])[0],
            "sender_name": form.get("user_name", [""])[0],
            "slash_text": text,
            "raw_text": raw_text,
        }

    def _mattermost_response_text(self, response: dict) -> str:
        return render_response_text(response, platform="mattermost", max_chars=3500)

    def _redact_command_log_item(self, item: dict) -> dict:
        redacted = json.loads(json.dumps(item, ensure_ascii=False))
        raw_text = str(redacted.get("raw_text") or "")
        redacted["raw_text"] = SECRET_VALUE_RE.sub(lambda match: redact_secret(match.group(0)), raw_text)
        args = redacted.get("args")
        if isinstance(args, dict):
            tail = args.get("tail")
            if isinstance(tail, list):
                args["tail"] = [redact_secret(str(value)) if SECRET_VALUE_RE.fullmatch(str(value)) else value for value in tail]
        return redacted

    def _configured_provider_names(self) -> list[str] | None:
        raw = os.environ.get("AI_RUNNER_PROVIDERS")
        if raw is None:
            return None
        providers: list[str] = []
        for item in raw.split(","):
            provider = item.strip()
            if provider == "claude":
                provider = "claude-code"
            if provider:
                providers.append(provider)
        return providers

    def _mattermost_task_provider(self, item: dict) -> str | None:
        configured = self._configured_provider_names()
        if item.get("provider"):
            provider = str(item["provider"])
            if configured is not None and provider not in configured:
                return None
            return provider
        path = self.state.root / "provider-selection.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                provider = data.get("provider")
                if provider in {"claude-code", "codex"} and (configured is None or provider in configured):
                    return str(provider)
            except json.JSONDecodeError:
                pass
        if configured is not None:
            if len(configured) == 1:
                return configured[0]
            return None
        return "claude-code"

    def _run_mattermost_task_background(self, parsed: dict, item: dict) -> None:
        request_id = item.get("request_id") or str(uuid.uuid4())
        run_id = item.get("run_id") or str(uuid.uuid4())
        item["run_id"] = run_id
        provider = self._mattermost_task_provider(item)
        item["provider"] = provider
        done = threading.Event()
        self.state.runtime.events.emit(status_event(run_id, "queued", "Mattermost 任务已进入后台队列", provider))

        def heartbeat() -> None:
            heartbeat_seconds = int(os.environ.get("AI_MATTERMOST_HEARTBEAT_SECONDS", "45"))
            if heartbeat_seconds <= 0:
                return
            while not done.wait(heartbeat_seconds):
                self.state.runtime.events.emit(
                    status_event(
                        run_id,
                        "running",
                        "仍在运行：模型思考、工具执行、联网等待或生成中；不是卡死。",
                        provider,
                    )
                )

        heartbeat_thread = threading.Thread(target=heartbeat, name=f"mattermost-heartbeat-{request_id}", daemon=True)
        heartbeat_thread.start()
        try:
            response = execute(parsed, item, self.state.runtime)
        except Exception as exc:  # pragma: no cover - daemon safety net.
            response = {
                "request_id": request_id,
                "status": "error",
                "run_id": run_id,
                "message_zh": "执行失败",
                "data": {},
                "error": {"code": "mattermost_background_task_failed", "detail": str(exc)},
            }
        finally:
            done.set()
        if response.get("status") == "needs_confirmation":
            token = response.get("data", {}).get("confirmation_token")
            if token:
                pending = self.state.confirmations()
                pending[token] = {"created_at": int(time.time()), "parsed": parsed, "envelope": item}
                self.state.save_confirmations(pending)
        self.state.save_response(request_id, response)
        text = self._mattermost_response_text(response)
        phase = "done" if response.get("status") == "accepted" else "error"
        self.state.runtime.events.emit(status_event(response.get("run_id") or run_id, phase, text, response.get("data", {}).get("provider") or provider))

    def _start_mattermost_task_background(self, parsed: dict, item: dict) -> dict:
        request_id = item.get("request_id") or str(uuid.uuid4())
        item["run_id"] = item.get("run_id") or str(uuid.uuid4())
        provider = self._mattermost_task_provider(item)
        if provider is None:
            response = execute(parsed, item, self.state.runtime)
            self.state.save_response(request_id, response)
            return response
        item["provider"] = provider
        thread = threading.Thread(target=self._run_mattermost_task_background, args=(parsed, item), name=f"mattermost-task-{request_id}", daemon=True)
        thread.start()
        response = {
            "request_id": request_id,
            "status": "accepted",
            "run_id": item["run_id"],
            "message_zh": "已收到任务，正在后台运行。状态和最终结果会发送到配置的 Mattermost 状态频道。",
            "data": {"background": True, "provider": item["provider"]},
            "error": None,
        }
        self.state.save_response(request_id, response)
        return response

    def _handle_command_payload(self, payload: dict) -> dict:
        request_id = payload.get("request_id") or str(uuid.uuid4())
        cached = self.state.responses().get(request_id)
        if cached:
            return cached
        # Mattermost slash-command payloads can arrive without the /ai prefix after platform routing.
        parsed = parse_command(payload.get("raw_text", ""), allow_bare=True)
        raw_text = payload.get("raw_text", "")
        if parsed.get("canonical_action") == "confirm":
            token = " ".join(parsed.get("args", {}).get("tail", []))
            pending = self.state.confirmations()
            item = pending.pop(token, None)
            self.state.save_confirmations(pending)
            if not item:
                return {"status": "rejected", "error": {"code": "confirmation_not_found", "detail": "confirmation_not_found"}}
            item["envelope"]["confirmed"] = True
            response = execute(item["parsed"], item["envelope"], self.state.runtime)
            if item["parsed"].get("canonical_action") == "credential.add" and response.get("status") == "accepted":
                token = uuid.uuid4().hex
                metadata = {"handle": response.get("data", {}).get("handle"), "type": "custom"}
                uploads = self.state.credential_uploads()
                uploads[token] = {"metadata": metadata, "expires_at": int(time.time()) + 600}
                self.state.save_credential_uploads(uploads)
                response.setdefault("data", {})["upload_path"] = f"/bridge/credential-upload/{token}"
                response["data"]["upload_method"] = "PUT"
            self.state.save_response(item["envelope"]["request_id"], response)
            return response
        if parsed.get("status") == "rejected" and (
            parsed.get("error") == "command_must_start_with_/ai" and not raw_text.strip().startswith("/")
        ):
            parsed = {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": raw_text}, "requires_confirmation": False}
        elif parsed.get("status") == "rejected" and payload.get("platform") == "mattermost" and payload.get("slash_text"):
            parsed = {
                "status": "accepted",
                "canonical_action": "task.run",
                "args": {"prompt": payload["slash_text"]},
                "requires_confirmation": False,
            }
        item = dict(payload)
        item.pop("confirmed", None)
        item.update(parsed)
        item["request_id"] = request_id
        self.state.append_jsonl(self.state.commands_path, self._redact_command_log_item(item))
        if payload.get("platform") == "mattermost" and parsed.get("canonical_action") == "task.run" and not item.get("confirmed"):
            return self._start_mattermost_task_background(parsed, item)
        response = execute(parsed, item, self.state.runtime)
        if response.get("status") == "needs_confirmation":
            token = response.get("data", {}).get("confirmation_token")
            if token:
                pending = self.state.confirmations()
                pending[token] = {"created_at": int(time.time()), "parsed": parsed, "envelope": item}
                self.state.save_confirmations(pending)
        if parsed.get("canonical_action") == "credential.add" and response.get("status") == "accepted":
            token = uuid.uuid4().hex
            metadata = {"handle": response.get("data", {}).get("handle"), "type": "custom"}
            uploads = self.state.credential_uploads()
            uploads[token] = {"metadata": metadata, "expires_at": int(time.time()) + 600}
            self.state.save_credential_uploads(uploads)
            response.setdefault("data", {})["upload_path"] = f"/bridge/credential-upload/{token}"
            response["data"]["upload_method"] = "PUT"
        self.state.save_response(request_id, response)
        return response

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        ok, reason = self._auth(b"")
        if not ok:
            self._json(401, {"status": "rejected", "error": {"code": reason, "detail": reason}})
            return
        if path == "/bridge/health":
            self._json(200, {"status": "ok", "time": int(time.time())})
            return
        if path == "/bridge/poll":
            commands = []
            if self.state.commands_path.exists():
                commands = [json.loads(line) for line in self.state.commands_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self._json(200, {"commands": commands})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        content_type = self.headers.get("Content-Type", "")
        if path == "/bridge/command" and content_type.startswith("application/x-www-form-urlencoded"):
            try:
                body = self._read_body()
            except ValueError:
                self._json(413, {"status": "rejected", "error": {"code": "request_too_large", "detail": "request_too_large"}})
                return
            ok, payload_or_reason = self._mattermost_payload(body)
            if not ok:
                self._json(401, {"status": "rejected", "error": {"code": payload_or_reason, "detail": payload_or_reason}})
                return
            response = self._handle_command_payload(payload_or_reason)  # type: ignore[arg-type]
            self._json(
                200,
                {
                    "response_type": "ephemeral",
                    "text": self._mattermost_response_text(response),
                    "props": {"ai_remote_response": response},
                },
            )
            return

        ok, reason = self._preauth()
        if not ok:
            self._json(401, {"status": "rejected", "error": {"code": reason, "detail": reason}})
            return
        try:
            body = self._read_body()
        except ValueError:
            self._json(413, {"status": "rejected", "error": {"code": "request_too_large", "detail": "request_too_large"}})
            return
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
            self._json(200, self._handle_command_payload(payload))
            return

        if path == "/bridge/credential-upload-url":
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            handle = metadata.get("handle") or f"credential://pending/{uuid.uuid4()}"
            metadata["handle"] = handle
            token = uuid.uuid4().hex
            uploads = self.state.credential_uploads()
            uploads[token] = {"metadata": metadata, "expires_at": int(time.time()) + 600}
            self.state.save_credential_uploads(uploads)
            self._json(200, {"status": "accepted", "handle": handle, "expires_at": uploads[token]["expires_at"], "upload_path": f"/bridge/credential-upload/{token}", "method": "PUT"})
            return

        if path == "/bridge/event":
            self.state.append_jsonl(self.state.events_path, payload)
            self._json(200, {"status": "accepted"})
            return

        self._json(404, {"error": "not_found"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        prefix = "/bridge/credential-upload/"
        if not path.startswith(prefix):
            self._json(404, {"error": "not_found"})
            return
        token = path[len(prefix) :]
        uploads = self.state.credential_uploads()
        record = uploads.get(token)
        if not record or int(record.get("expires_at", 0)) < int(time.time()):
            self._json(404, {"status": "rejected", "error": {"code": "upload_token_not_found", "detail": "upload_token_not_found"}})
            return
        try:
            body = self._read_body()
        except ValueError:
            self._json(413, {"status": "rejected", "error": {"code": "request_too_large", "detail": "request_too_large"}})
            return
        public = self.state.runtime.credentials.add_local_secret(record["metadata"], body.decode("utf-8"))
        uploads.pop(token, None)
        self.state.save_credential_uploads(uploads)
        self._json(200, {"status": "accepted", "credential": public})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    secret = os.environ["AI_BRIDGE_SHARED_SECRET"]
    root = Path(os.environ.get("AI_REMOTE_STATE", "/var/lib/ai-remote-runner"))
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    server.state = BridgeState(root, secret)  # type: ignore[attr-defined]
    server.serve_forever()
