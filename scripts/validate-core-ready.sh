#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_COMMAND_URL="${BRIDGE_COMMAND_URL:-http://127.0.0.1:8765/bridge/command}"

if [ "$(id -u)" != 0 ] && [ "${AI_VALIDATE_CORE_READY_ALLOW_NON_ROOT:-false}" != true ]; then
  exec sudo -E env AI_REMOTE_STATE="$STATE_ROOT" AI_WORKSPACE_ROOT="$WORKSPACE_ROOT" BRIDGE_COMMAND_URL="$BRIDGE_COMMAND_URL" bash "$ROOT/scripts/validate-core-ready.sh" "$@"
fi

export AI_REMOTE_STATE="$STATE_ROOT"
export AI_WORKSPACE_ROOT="$WORKSPACE_ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"
AI_RUNNER_PROVIDERS="${AI_RUNNER_PROVIDERS:-claude-code,codex}"
SMOKE_TOKEN="FFC_FULL_ACCESS_SMOKE_$(date +%s)_$$"
SMOKE_WORKSPACE="$WORKSPACE_ROOT/provider-smoke"
SMOKE_TMP="/tmp/ffc-ai-full-access-smoke-$SMOKE_TOKEN"
if [ -f "$STATE_ROOT/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$STATE_ROOT/config.env"
  set +a
fi

python3 -m ai_remote_runner.cli providers >/dev/null
python3 -m ai_remote_runner.cli index >/dev/null
: "${AI_BRIDGE_SHARED_SECRET:?AI_BRIDGE_SHARED_SECRET is required for bridge loopback validation}"
rm -rf "$SMOKE_TMP"
mkdir -p "$SMOKE_TMP/localpkg/full_access_smoke_pkg"
printf 'VALUE = "%s"\n' "$SMOKE_TOKEN" > "$SMOKE_TMP/localpkg/full_access_smoke_pkg/__init__.py"
cat > "$SMOKE_TMP/localpkg/pyproject.toml" <<'EOF'
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "full-access-smoke-pkg"
version = "0.0.0"
EOF
if [[ ",$AI_RUNNER_PROVIDERS," == *",claude-code,"* ]]; then
  command -v claude >/dev/null 2>&1 || { printf '[validate-core-ready] claude is required for requested provider claude-code\n' >&2; exit 1; }
  claude auth status --json >/dev/null
  python3 -m ai_remote_runner.cli provider-smoke --provider claude-code --workspace "$SMOKE_WORKSPACE" \
    --prompt "执行真实 full-access smoke。必须使用 shell 命令完成：1) 在当前工作目录创建 full-access-smoke-claude.txt，内容为 $SMOKE_TOKEN；2) 在 $SMOKE_TMP/claude-tmp.txt 写入 $SMOKE_TOKEN 并读取确认；3) 用 Python/urllib 或 curl 请求 https://example.com 并把 HTTP 状态或页面标题写入 full-access-smoke-claude-net.txt；4) 创建 venv $SMOKE_TMP/claude-venv，并从 $SMOKE_TMP/localpkg 安装本地包 full-access-smoke-pkg，然后 import full_access_smoke_pkg 验证 VALUE；5) 最终回复必须包含 $SMOKE_TOKEN、NETWORK_OK、VENV_INSTALL_OK。" \
    --expect-contains "$SMOKE_TOKEN" >/dev/null || {
    printf '[validate-core-ready] Claude Code full-access smoke failed\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_WORKSPACE/full-access-smoke-claude.txt" || {
    printf '[validate-core-ready] Claude Code did not prove file/tool full access in smoke workspace\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_TMP/claude-tmp.txt" || {
    printf '[validate-core-ready] Claude Code did not prove broad /tmp file access\n' >&2
    exit 1
  }
  [ -s "$SMOKE_WORKSPACE/full-access-smoke-claude-net.txt" ] || {
    printf '[validate-core-ready] Claude Code did not prove network access\n' >&2
    exit 1
  }
  [ -x "$SMOKE_TMP/claude-venv/bin/python" ] || {
    printf '[validate-core-ready] Claude Code did not prove venv/install capability\n' >&2
    exit 1
  }
  "$SMOKE_TMP/claude-venv/bin/python" -c 'import full_access_smoke_pkg' >/dev/null || {
    printf '[validate-core-ready] Claude Code local package install proof failed\n' >&2
    exit 1
  }
fi
if [[ ",$AI_RUNNER_PROVIDERS," == *",codex,"* ]]; then
  command -v codex >/dev/null 2>&1 || { printf '[validate-core-ready] codex is required for requested provider codex\n' >&2; exit 1; }
  python3 -m ai_remote_runner.cli provider-smoke --provider codex --workspace "$SMOKE_WORKSPACE" \
    --prompt "Run a real full-access smoke using shell commands. You must: 1) create full-access-smoke-codex.txt in the current working directory containing $SMOKE_TOKEN; 2) write $SMOKE_TOKEN to $SMOKE_TMP/codex-tmp.txt and read it back; 3) fetch https://example.com with Python urllib or curl and write the HTTP status or title to full-access-smoke-codex-net.txt; 4) create venv $SMOKE_TMP/codex-venv, install the local package at $SMOKE_TMP/localpkg, and import full_access_smoke_pkg to verify VALUE; 5) final reply must contain $SMOKE_TOKEN, NETWORK_OK, and VENV_INSTALL_OK." \
    --expect-contains "$SMOKE_TOKEN" >/dev/null || {
    printf '[validate-core-ready] Codex full-access smoke failed\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_WORKSPACE/full-access-smoke-codex.txt" || {
    printf '[validate-core-ready] Codex did not prove file/tool full access in smoke workspace\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_TMP/codex-tmp.txt" || {
    printf '[validate-core-ready] Codex did not prove broad /tmp file access\n' >&2
    exit 1
  }
  [ -s "$SMOKE_WORKSPACE/full-access-smoke-codex-net.txt" ] || {
    printf '[validate-core-ready] Codex did not prove network access\n' >&2
    exit 1
  }
  [ -x "$SMOKE_TMP/codex-venv/bin/python" ] || {
    printf '[validate-core-ready] Codex did not prove venv/install capability\n' >&2
    exit 1
  }
  "$SMOKE_TMP/codex-venv/bin/python" -c 'import full_access_smoke_pkg' >/dev/null || {
    printf '[validate-core-ready] Codex local package install proof failed\n' >&2
    exit 1
  }
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
data["core_ready_status"] = "validated"
data["core_ready_validated_at"] = "manual-smoke"
data["provider_full_access_smoke_validated"] = True
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

printf '[validate-core-ready] core_ready=true\n'
