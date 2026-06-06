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
AI_VALIDATE_PROVIDER_RESERVED_USD="${AI_VALIDATE_PROVIDER_RESERVED_USD:-0.50}"
AI_VALIDATE_PROVIDER_TIMEOUT_SECONDS="${AI_VALIDATE_PROVIDER_TIMEOUT_SECONDS:-300}"
SMOKE_TOKEN="FFC_FULL_ACCESS_SMOKE_$(date +%s)_$$"
SMOKE_WORKSPACE="$WORKSPACE_ROOT/provider-smoke"
SMOKE_TMP="/tmp/ffc-ai-full-access-smoke-$SMOKE_TOKEN"
CLAUDE_FILE_PROMPT="$SMOKE_TMP/claude-file-prompt.txt"
CLAUDE_NET_PROMPT="$SMOKE_TMP/claude-net-prompt.txt"
CLAUDE_VENV_PROMPT="$SMOKE_TMP/claude-venv-prompt.txt"
CODEX_FILE_PROMPT="$SMOKE_TMP/codex-file-prompt.txt"
CODEX_NET_PROMPT="$SMOKE_TMP/codex-net-prompt.txt"
CODEX_VENV_PROMPT="$SMOKE_TMP/codex-venv-prompt.txt"
if [ -f "$STATE_ROOT/config.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$STATE_ROOT/config.env"
  set +a
fi
if [ -z "${AI_RUNNER_PROVIDERS+x}" ] && [ -f "$STATE_ROOT/install-manifest.json" ]; then
  if manifest_providers="$(python3 - "$STATE_ROOT/install-manifest.json" <<'PY'
import json
import sys
from pathlib import Path

try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    data = {}
if "configured_providers" in data:
    print(data.get("configured_providers") or "")
else:
    raise SystemExit(3)
PY
  )"; then
    AI_RUNNER_PROVIDERS="$manifest_providers"
  fi
fi
if [ -z "${AI_RUNNER_PROVIDERS+x}" ]; then
  printf '[validate-core-ready] AI_RUNNER_PROVIDERS is not configured; run install-runner.sh with explicit AI_RUNNER_COMPONENTS before validation\n' >&2
  exit 2
fi
export AI_RUNNER_PROVIDERS

python3 -m ai_remote_runner.cli providers >/dev/null
python3 -m ai_remote_runner.cli index >/dev/null
: "${AI_BRIDGE_SHARED_SECRET:?AI_BRIDGE_SHARED_SECRET is required for bridge loopback validation}"
rm -rf "$SMOKE_TMP"
mkdir -p "$SMOKE_TMP/localpkg/full_access_smoke_pkg"
printf 'VALUE = "%s"\n' "$SMOKE_TOKEN" > "$SMOKE_TMP/localpkg/full_access_smoke_pkg/__init__.py"
cat > "$SMOKE_TMP/localpkg/setup.py" <<'EOF'
from setuptools import setup

setup(name="full-access-smoke-pkg", version="0.0.0", packages=["full_access_smoke_pkg"])
EOF
cat > "$SMOKE_TMP/localpkg/pyproject.toml" <<'EOF'
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "full-access-smoke-pkg"
version = "0.0.0"
EOF
for provider in claude codex; do
  cat > "$SMOKE_TMP/$provider-file-tmp.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '$SMOKE_TOKEN' > '$SMOKE_WORKSPACE/full-access-smoke-$provider.txt'
printf '%s\n' '$SMOKE_TOKEN' > '$SMOKE_TMP/$provider-tmp.txt'
grep -q '$SMOKE_TOKEN' '$SMOKE_TMP/$provider-tmp.txt'
EOF
  cat > "$SMOKE_TMP/$provider-net.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
python3 -c 'from urllib import request; from pathlib import Path; status=request.urlopen("https://example.com", timeout=15).status; Path("$SMOKE_WORKSPACE/full-access-smoke-$provider-net.txt").write_text(str(status), encoding="utf-8")'
test -s '$SMOKE_WORKSPACE/full-access-smoke-$provider-net.txt'
EOF
  cat > "$SMOKE_TMP/$provider-venv.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
python3 -m venv '$SMOKE_TMP/$provider-venv'
'$SMOKE_TMP/$provider-venv/bin/python' -m pip install --no-index --no-build-isolation '$SMOKE_TMP/localpkg'
'$SMOKE_TMP/$provider-venv/bin/python' -c 'import full_access_smoke_pkg; assert full_access_smoke_pkg.VALUE == "$SMOKE_TOKEN"'
EOF
  chmod +x "$SMOKE_TMP/$provider-file-tmp.sh" "$SMOKE_TMP/$provider-net.sh" "$SMOKE_TMP/$provider-venv.sh"
done
cat > "$CLAUDE_FILE_PROMPT" <<EOF
请使用 Bash 工具执行这一条命令，必须真的执行，不要只解释：
bash $SMOKE_TMP/claude-file-tmp.sh
成功后只回复：$SMOKE_TOKEN FILE_TMP_OK
EOF
cat > "$CLAUDE_NET_PROMPT" <<EOF
请使用 Bash 工具执行这一条命令，必须真的执行，不要只解释：
bash $SMOKE_TMP/claude-net.sh
成功后只回复：$SMOKE_TOKEN NETWORK_OK
EOF
cat > "$CLAUDE_VENV_PROMPT" <<EOF
请使用 Bash 工具执行这一条命令，必须真的执行，不要只解释：
bash $SMOKE_TMP/claude-venv.sh
成功后只回复：$SMOKE_TOKEN VENV_INSTALL_OK
EOF
cat > "$CODEX_FILE_PROMPT" <<EOF
Use the shell to run this exact command. Do not merely explain it:
bash $SMOKE_TMP/codex-file-tmp.sh
After success, reply only: $SMOKE_TOKEN FILE_TMP_OK
EOF
cat > "$CODEX_NET_PROMPT" <<EOF
Use the shell to run this exact command. Do not merely explain it:
bash $SMOKE_TMP/codex-net.sh
After success, reply only: $SMOKE_TOKEN NETWORK_OK
EOF
cat > "$CODEX_VENV_PROMPT" <<EOF
Use the shell to run this exact command. Do not merely explain it:
bash $SMOKE_TMP/codex-venv.sh
After success, reply only: $SMOKE_TOKEN VENV_INSTALL_OK
EOF

run_provider_smoke_step() {
  local provider="$1"
  local prompt_file="$2"
  local label="$3"
  python3 -m ai_remote_runner.cli provider-smoke --provider "$provider" --workspace "$SMOKE_WORKSPACE" \
    --prompt-file "$prompt_file" \
    --reserved-usd "$AI_VALIDATE_PROVIDER_RESERVED_USD" \
    --timeout-seconds "$AI_VALIDATE_PROVIDER_TIMEOUT_SECONDS" >/dev/null || {
    printf '[validate-core-ready] %s full-access smoke step failed: %s\n' "$provider" "$label" >&2
    exit 1
  }
}
if [[ ",$AI_RUNNER_PROVIDERS," == *",claude-code,"* ]]; then
  command -v claude >/dev/null 2>&1 || { printf '[validate-core-ready] claude is required for requested provider claude-code\n' >&2; exit 1; }
  claude auth status --json >/dev/null
  run_provider_smoke_step claude-code "$CLAUDE_FILE_PROMPT" file-tmp
  grep -q "$SMOKE_TOKEN" "$SMOKE_WORKSPACE/full-access-smoke-claude.txt" || {
    printf '[validate-core-ready] Claude Code did not prove file/tool full access in smoke workspace\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_TMP/claude-tmp.txt" || {
    printf '[validate-core-ready] Claude Code did not prove broad /tmp file access\n' >&2
    exit 1
  }
  run_provider_smoke_step claude-code "$CLAUDE_NET_PROMPT" network
  [ -s "$SMOKE_WORKSPACE/full-access-smoke-claude-net.txt" ] || {
    printf '[validate-core-ready] Claude Code did not prove network access\n' >&2
    exit 1
  }
  run_provider_smoke_step claude-code "$CLAUDE_VENV_PROMPT" venv-install
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
  run_provider_smoke_step codex "$CODEX_FILE_PROMPT" file-tmp
  grep -q "$SMOKE_TOKEN" "$SMOKE_WORKSPACE/full-access-smoke-codex.txt" || {
    printf '[validate-core-ready] Codex did not prove file/tool full access in smoke workspace\n' >&2
    exit 1
  }
  grep -q "$SMOKE_TOKEN" "$SMOKE_TMP/codex-tmp.txt" || {
    printf '[validate-core-ready] Codex did not prove broad /tmp file access\n' >&2
    exit 1
  }
  run_provider_smoke_step codex "$CODEX_NET_PROMPT" network
  [ -s "$SMOKE_WORKSPACE/full-access-smoke-codex-net.txt" ] || {
    printf '[validate-core-ready] Codex did not prove network access\n' >&2
    exit 1
  }
  run_provider_smoke_step codex "$CODEX_VENV_PROMPT" venv-install
  [ -x "$SMOKE_TMP/codex-venv/bin/python" ] || {
    printf '[validate-core-ready] Codex did not prove venv/install capability\n' >&2
    exit 1
  }
  "$SMOKE_TMP/codex-venv/bin/python" -c 'import full_access_smoke_pkg' >/dev/null || {
    printf '[validate-core-ready] Codex local package install proof failed\n' >&2
    exit 1
  }
fi
if [ -z "$AI_RUNNER_PROVIDERS" ]; then
  printf '[validate-core-ready] no AI provider configured; validating management-only runner commands and bridge loopback\n'
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

python3 - "$STATE_ROOT/install-manifest.json" "$AI_RUNNER_PROVIDERS" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
providers = [item.strip() for item in sys.argv[2].split(",") if item.strip()]
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
data["core_ready"] = True
data["core_ready_status"] = "validated"
data["core_ready_validated_at"] = "manual-smoke"
data["provider_full_access_smoke_validated"] = bool(providers)
if not providers:
    data["core_ready_note"] = "management-only Telegram runner; no AI provider configured on this machine"
path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

printf '[validate-core-ready] core_ready=true\n'
