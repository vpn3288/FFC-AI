#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT="${AI_REMOTE_STATE:-/var/lib/ai-remote-runner}"
WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-/srv/ai-workspaces}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AI_REMOTE_STATE="$STATE_ROOT"
export AI_WORKSPACE_ROOT="$WORKSPACE_ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"

python3 -m ai_remote_runner.cli providers >/dev/null
python3 -m ai_remote_runner.cli execute '/ai 状态' >/dev/null
python3 -m ai_remote_runner.cli index >/dev/null

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
