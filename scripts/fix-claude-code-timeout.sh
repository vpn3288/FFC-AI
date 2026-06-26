#!/usr/bin/env bash
# Safe Claude Code timeout/proxy repair entrypoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[fix-claude-code-timeout] applying Claude Code timeout and third-party API repair\n'
printf '[fix-claude-code-timeout] using existing ANTHROPIC_BASE_URL/ANTHROPIC_API_URL and key aliases from config.env/settings.json\n'

exec bash "$SCRIPT_DIR/fix-claude-code-third-party-api.sh"
