#!/usr/bin/env bash
# Compatibility wrapper for Claude Code API timeout repairs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[fix-claude-api-timeout] applying safe Claude Code timeout/proxy repair\n'
printf '[fix-claude-api-timeout] no endpoint or API key is hard-coded; existing config.env/settings.json values are reused\n'

exec bash "$SCRIPT_DIR/fix-claude-code-third-party-api.sh"
