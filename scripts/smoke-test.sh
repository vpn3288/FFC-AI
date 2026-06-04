#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
export AI_REMOTE_STATE="${AI_REMOTE_STATE:-$ROOT/work/smoke-state}"
export AI_WORKSPACE_ROOT="${AI_WORKSPACE_ROOT:-$ROOT/work/smoke-workspaces}"

python3 -m unittest discover -s "$ROOT/tests" -v
python3 -m ai_remote_runner.cli parse '/ai 状态' >/dev/null
python3 -m ai_remote_runner.cli index >/dev/null
python3 -m ai_remote_runner.cli providers >/dev/null
python3 -m ai_remote_runner.cli instruction global append --text 'smoke' >/dev/null
python3 -m ai_remote_runner.cli budget --reserve-run smoke-run --usd 0.01 >/dev/null

printf 'smoke tests passed\n'
