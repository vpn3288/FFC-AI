#!/usr/bin/env bash
# Compatibility wrapper for older docs that referenced the "100s timeout" fix.
# It no longer hard-codes any third-party endpoint or API key.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[fix-claude-code-100s-timeout] applying safe Claude Code timeout/proxy repair\n'
printf '[fix-claude-code-100s-timeout] this script reads existing config.env/settings.json and never writes embedded API keys\n'

exec bash "$SCRIPT_DIR/fix-claude-code-third-party-api.sh"
