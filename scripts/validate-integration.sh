#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
MATTERMOST_INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
MATTERMOST_OBJECTS_MANIFEST="${MATTERMOST_OBJECTS_MANIFEST:-$MATTERMOST_INSTALL_DIR/mattermost-objects.json}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-}"
MATTERMOST_URL="${MATTERMOST_URL:-}"
VALIDATE_MATTERMOST_COMMAND="${VALIDATE_MATTERMOST_COMMAND:-true}"
VALIDATE_MATTERMOST_BACKGROUND_TASK="${VALIDATE_MATTERMOST_BACKGROUND_TASK:-false}"
MATTERMOST_COMMAND_VALIDATED=false
MATTERMOST_BACKGROUND_TASK_VALIDATED=false

# 从commands.py动态生成验证命令列表（关键命令子集）
MATTERMOST_COMMANDS=(
  "/ai"
  "/ai 状态"
  "/ai 帮助"
  "/ai 功能"
  "/ai 新对话"
  "/ai 对话"
  "/ai 继续"
  "/ai 每次新对话"
  "/ai 持续对话"
  "/ai 压缩"
  "/ai 上下文"
  "/ai 预算"
  "/ai 自动压缩 开启"
  "/ai 自动压缩 关闭"
  "/ai 聊天模式 开启"
  "/ai 编辑模式 开启"
  "/ai 终端模式 开启"
  "/ai 完全访问 开启"
  "/ai 全局 查看"
  "/ai 项目 查看"
  "/ai 凭据 列表"
  "/ai 工作区 列表"
  "/ai 提供商 列表"
  "/ai 扩展 列表"
  "/ai 工具 列表"
  "/ai 说明"
  "/ai 说明 生成 smoke"
)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"
if [ -f "$STATE_ROOT/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$STATE_ROOT/config.env"
  set +a
fi
if [ -z "${AI_BRIDGE_SHARED_SECRET:-}" ] && [ -f "$MATTERMOST_INSTALL_DIR/.env" ]; then
  AI_BRIDGE_SHARED_SECRET="$(
    awk -F= '$1 == "AI_BRIDGE_SHARED_SECRET" {print substr($0, index($0, "=") + 1); exit}' "$MATTERMOST_INSTALL_DIR/.env"
  )"
fi

manifest_value() {
  local key="$1"
  [ -f "$MATTERMOST_OBJECTS_MANIFEST" ] || return 0
  python3 - "$MATTERMOST_OBJECTS_MANIFEST" "$key" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
try:
    value = json.loads(path.read_text(encoding="utf-8")).get(key, "")
except json.JSONDecodeError:
    value = ""
print(value if isinstance(value, str) else "")
PY
}

write_validation_status() {
  local mattermost_validated="$1"
  local status="$2"
  python3 - "$mattermost_validated" "$status" "$STATE_ROOT/install-manifest.json" "$MATTERMOST_INSTALL_DIR/install-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

mattermost_validated = sys.argv[1].lower() == "true"
status = sys.argv[2]

for index, raw_path in enumerate(sys.argv[3:]):
    path = Path(raw_path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    is_mattermost_manifest = index == 1 or "mattermost" in data.get("component", "")
    if is_mattermost_manifest:
        data["platform_ready"] = mattermost_validated
        data["platform_ready_status"] = "validated" if mattermost_validated else status
    else:
        data["bridge_loopback_validated"] = status in {"validated", "bridge_only_not_platform_validated"}
        data["mattermost_command_validated"] = mattermost_validated
        data["integration_ready_status"] = "validated" if mattermost_validated else status
    data["integration_validated_at"] = "manual-smoke"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY
}

on_validation_error() {
  local exit_code="$?"
  trap - ERR
  write_validation_status false validation_failed || true
  exit "$exit_code"
}

BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-$(manifest_value slash_command_url)}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-http://127.0.0.1:8765/bridge/command}"
MATTERMOST_URL="${MATTERMOST_URL:-${MATTERMOST_PLATFORM_URL:-}}"
MATTERMOST_URL="${MATTERMOST_URL:-http://127.0.0.1:8065}"

trap on_validation_error ERR
write_validation_status false validation_in_progress
: "${AI_BRIDGE_SHARED_SECRET:?AI_BRIDGE_SHARED_SECRET is required for integration validation}"

python3 - "$BRIDGE_COMMAND_URL" "$AI_BRIDGE_SHARED_SECRET" <<'PY' >/dev/null
import json
import sys
import time
import uuid
from urllib import request

from ai_remote_runner.security import sign_body

url, secret = sys.argv[1], sys.argv[2]
body = json.dumps({"request_id": str(uuid.uuid4()), "raw_text": "/ai 状态"}, ensure_ascii=False).encode("utf-8")
timestamp = str(time.time())
nonce = str(uuid.uuid4())
headers = {
    "Content-Type": "application/json",
    "X-AI-Bridge-Timestamp": timestamp,
    "X-AI-Bridge-Nonce": nonce,
    "X-AI-Bridge-Signature": sign_body(secret, timestamp, nonce, body),
}
response = request.urlopen(request.Request(url, data=body, headers=headers, method="POST"), timeout=10)
payload = json.loads(response.read().decode("utf-8"))
if payload.get("status") != "accepted":
    raise SystemExit(f"bridge loopback failed: {payload}")
PY

if [ "$VALIDATE_MATTERMOST_COMMAND" = true ] && [ -f "$MATTERMOST_INSTALL_DIR/.env" ]; then
  MATTERMOST_ADMIN_USERNAME="$(
    awk -F= '$1 == "MATTERMOST_ADMIN_USERNAME" {print substr($0, index($0, "=") + 1); exit}' "$MATTERMOST_INSTALL_DIR/.env"
  )"
  MATTERMOST_ADMIN_PASSWORD="$(
    awk -F= '$1 == "MATTERMOST_ADMIN_PASSWORD" {print substr($0, index($0, "=") + 1); exit}' "$MATTERMOST_INSTALL_DIR/.env"
  )"
  if [ -n "$MATTERMOST_ADMIN_USERNAME" ] && [ -n "$MATTERMOST_ADMIN_PASSWORD" ]; then
    python3 - "$MATTERMOST_URL" "$MATTERMOST_ADMIN_USERNAME" "$MATTERMOST_ADMIN_PASSWORD" "$VALIDATE_MATTERMOST_BACKGROUND_TASK" "${MATTERMOST_COMMANDS[@]}" <<'PY' >/dev/null
import json
import sys
from urllib import request

base, username, password, validate_background_task, *commands = sys.argv[1:]
login_body = json.dumps({"login_id": username, "password": password}).encode("utf-8")
login_response = request.urlopen(
    request.Request(
        base.rstrip("/") + "/api/v4/users/login",
        data=login_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    ),
    timeout=10,
)
token = login_response.headers["Token"]
headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}


def get(path: str) -> dict:
    response = request.urlopen(request.Request(base.rstrip("/") + path, headers=headers), timeout=10)
    return json.loads(response.read().decode("utf-8"))


team = get("/api/v4/teams/name/ai-lab")
channel = get(f"/api/v4/teams/{team['id']}/channels/name/ai-ops")


def execute_command(command: str) -> dict:
    command_body = json.dumps({"channel_id": channel["id"], "command": command}, ensure_ascii=False).encode("utf-8")
    command_response = request.urlopen(
        request.Request(
            base.rstrip("/") + "/api/v4/commands/execute",
            data=command_body,
            headers=headers,
            method="POST",
        ),
        timeout=20,
    )
    return json.loads(command_response.read().decode("utf-8"))


def inner_response(payload: dict) -> dict:
    inner = payload.get("props", {}).get("ai_remote_response", {})
    if not isinstance(inner, dict):
        raise SystemExit(f"Mattermost command response missing ai_remote_response props: {payload}")
    return inner


for command in commands:
    payload = execute_command(command)
    inner = inner_response(payload)
    if inner.get("status") != "accepted":
        raise SystemExit(f"Mattermost command execution failed for {command}: {payload}")

credential_command = "/ai 凭据 添加 credential://smoke/mattermost-validation"
credential_payload = execute_command(credential_command)
credential_inner = inner_response(credential_payload)
if credential_inner.get("status") != "needs_confirmation":
    raise SystemExit(f"Mattermost credential confirmation preflight failed: {credential_payload}")
confirmation_token = credential_inner.get("data", {}).get("confirmation_token")
if not confirmation_token:
    raise SystemExit(f"Mattermost credential confirmation token missing: {credential_payload}")
confirmed_payload = execute_command(f"/ai 确认 {confirmation_token}")
confirmed_inner = inner_response(confirmed_payload)
if confirmed_inner.get("status") != "accepted" or not confirmed_inner.get("data", {}).get("upload_path"):
    raise SystemExit(f"Mattermost credential confirmation failed: {confirmed_payload}")

if validate_background_task == "true":
    task_payload = execute_command("/ai integration smoke task: reply with mattermost-background-ok")
    task_inner = inner_response(task_payload)
    if task_inner.get("status") != "accepted" or not task_inner.get("data", {}).get("background"):
        raise SystemExit(f"Mattermost background task validation failed: {task_payload}")
PY
    MATTERMOST_COMMAND_VALIDATED=true
    if [ "$VALIDATE_MATTERMOST_BACKGROUND_TASK" = true ]; then
      MATTERMOST_BACKGROUND_TASK_VALIDATED=true
    fi
  else
    printf '[validate-integration] Mattermost admin credentials are required for slash command validation\n' >&2
    exit 1
  fi
elif [ "$VALIDATE_MATTERMOST_COMMAND" = true ]; then
  printf '[validate-integration] Mattermost .env is required for slash command validation\n' >&2
  exit 1
fi

if [ "$MATTERMOST_COMMAND_VALIDATED" = true ]; then
  write_validation_status true validated
else
  write_validation_status false bridge_only_not_platform_validated
fi
trap - ERR

printf '[validate-integration] bridge loopback passed\n'
if [ "$MATTERMOST_COMMAND_VALIDATED" = true ]; then
  printf '[validate-integration] Mattermost /ai commands and credential confirmation passed\n'
  if [ "$MATTERMOST_BACKGROUND_TASK_VALIDATED" = true ]; then
    printf '[validate-integration] Mattermost background task dispatch passed\n'
  fi
else
  printf '[validate-integration] Mattermost /ai commands skipped; platform_ready was not marked validated\n'
fi
