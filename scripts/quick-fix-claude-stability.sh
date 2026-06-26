#!/usr/bin/env bash
# Compatibility entrypoint for Claude Code stability fixes.
# This script is intentionally non-restarting so it is safe to run from Telegram tasks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[quick-fix] applying stability settings without restarting services\n'
exec bash "$SCRIPT_DIR/fix-stability-restart.sh"
