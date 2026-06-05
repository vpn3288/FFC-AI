#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
MATTERMOST_INSTALL_DIR="${MATTERMOST_INSTALL_DIR:-/opt/ffc-ai-mattermost}"
MATTERMOST_OBJECTS_MANIFEST="${MATTERMOST_OBJECTS_MANIFEST:-$MATTERMOST_INSTALL_DIR/mattermost-objects.json}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-}"
MATTERMOST_URL="${MATTERMOST_URL:-}"
VALIDATE_MATTERMOST_COMMAND="${VALIDATE_MATTERMOST_COMMAND:-true}"
MATTERMOST_COMMAND_VALIDATED=false
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

BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-$(manifest_value slash_command_url)}"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-http://127.0.0.1:8765/bridge/command}"
MATTERMOST_URL="${MATTERMOST_URL:-${MATTERMOST_PLATFORM_URL:-}}"
MATTERMOST_URL="${MATTERMOST_URL:-http://127.0.0.1:8065}"

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
    python3 - "$MATTERMOST_URL" "$MATTERMOST_ADMIN_USERNAME" "$MATTERMOST_ADMIN_PASSWORD" <<'PY' >/dev/null
import json
import sys
from urllib import request

base, username, password = sys.argv[1:4]
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
command_body = json.dumps({"channel_id": channel["id"], "command": "/ai 状态"}).encode("utf-8")
command_response = request.urlopen(
    request.Request(
        base.rstrip("/") + "/api/v4/commands/execute",
        data=command_body,
        headers=headers,
        method="POST",
    ),
    timeout=20,
)
payload = json.loads(command_response.read().decode("utf-8"))
inner = payload.get("props", {}).get("ai_remote_response", {})
if inner.get("status") != "accepted":
    raise SystemExit(f"Mattermost command execution failed: {payload}")
PY
    MATTERMOST_COMMAND_VALIDATED=true
  fi
fi

python3 - "$STATE_ROOT/install-manifest.json" "$MATTERMOST_INSTALL_DIR/install-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if "mattermost" in data.get("component", ""):
        data["platform_ready"] = True
        data["platform_ready_status"] = "validated"
    else:
        data["core_ready"] = True
        data["core_ready_status"] = "validated"
    data["integration_validated_at"] = "manual-smoke"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

printf '[validate-integration] bridge loopback passed\n'
if [ "$MATTERMOST_COMMAND_VALIDATED" = true ]; then
  printf '[validate-integration] Mattermost /ai command passed\n'
fi
