#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-http://127.0.0.1:8765/bridge/command}"

export AI_REMOTE_STATE="$STATE_ROOT"
export AI_WORKSPACE_ROOT="$WORKSPACE_ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"
AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:-claude-code,codex}"
if [ -f "$STATE_ROOT/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$STATE_ROOT/config.env"
  set +a
fi

python3 -m ai_remote_runner.cli providers >/dev/null
python3 -m ai_remote_runner.cli index >/dev/null
: "${AI_BRIDGE_SHARED_SECRET:?AI_BRIDGE_SHARED_SECRET is required for bridge loopback validation}"
if [[ ",$AI_RUNNER_PROVIDERS," == *",claude-code,"* ]]; then
  command -v claude >/dev/null 2>&1 || { printf '[validate-core-ready] claude is required for requested provider claude-code\n' >&2; exit 1; }
  claude auth status --json >/dev/null
fi
if [[ ",$AI_RUNNER_PROVIDERS," == *",codex,"* ]]; then
  command -v codex >/dev/null 2>&1 || { printf '[validate-core-ready] codex is required for requested provider codex\n' >&2; exit 1; }
fi

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

python3 - "$STATE_ROOT/install-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["core_ready"] = True
data["core_ready_validated_at"] = "manual-smoke"
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

printf '[validate-core-ready] core_ready=true\n'
