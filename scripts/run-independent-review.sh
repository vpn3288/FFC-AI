#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REVIEW_ROOT="${AI_REVIEW_ROOT:-${TMPDIR:-/tmp}/ffc-ai-independent-reviews}"
RUN_ID="${AI_REVIEW_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
RUN_DIR="$REVIEW_ROOT/$RUN_ID"
PRIVATE_ROOT="${AI_REVIEW_PRIVATE_ROOT:-${TMPDIR:-/tmp}/ffc-ai-independent-review-private/$RUN_ID-$(date -u +%s)-$$}"
START_GATE="$PRIVATE_ROOT/start-gate"
SNAPSHOT_ROOT="$RUN_DIR/snapshots"
CLAUDE_REPO="$SNAPSHOT_ROOT/claude-repo"
CODEX_REPO="$SNAPSHOT_ROOT/codex-repo"
CLAUDE_PRIVATE_DIR="$PRIVATE_ROOT/claude"
CODEX_PRIVATE_DIR="$PRIVATE_ROOT/codex"
CLAUDE_HOME="$PRIVATE_ROOT/claude-home"
CODEX_HOME_FRESH="$PRIVATE_ROOT/codex-home"
USER_REQUIREMENTS_FILE="${USER_REQUIREMENTS_FILE:-}"
USER_REQUIREMENTS_TEXT="${USER_REQUIREMENTS_TEXT:-}"
CLAUDE_REVIEWER_MODEL="${CLAUDE_REVIEWER_MODEL:-${CLAUDE_MODEL:-}}"
CODEX_REVIEWER_MODEL="${CODEX_REVIEWER_MODEL:-${CODEX_MODEL:-}}"
CODEX_REVIEW_BASE_URL="${CODEX_REVIEW_BASE_URL:-}"
CODEX_REVIEW_API_KEY="${CODEX_REVIEW_API_KEY:-${OPENAI_API_KEY:-}}"
CLAUDE_REVIEW_API_KEY="${CLAUDE_REVIEW_API_KEY:-${ANTHROPIC_API_KEY:-}}"
CLAUDE_REVIEW_AUTH_TOKEN="${CLAUDE_REVIEW_AUTH_TOKEN:-${ANTHROPIC_AUTH_TOKEN:-}}"
CLAUDE_REVIEW_BASE_URL="${CLAUDE_REVIEW_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"
RUN_CLAUDE_REVIEW="${RUN_CLAUDE_REVIEW:-true}"
RUN_CODEX_REVIEW="${RUN_CODEX_REVIEW:-true}"
CLAUDE_REVIEW_TIMEOUT_SECONDS="${CLAUDE_REVIEW_TIMEOUT_SECONDS:-1800}"
CODEX_REVIEW_TIMEOUT_SECONDS="${CODEX_REVIEW_TIMEOUT_SECONDS:-900}"
CLAUDE_REVIEW_MAX_BUDGET_USD="${CLAUDE_REVIEW_MAX_BUDGET_USD:-0.50}"
CODEX_REVIEW_MAX_BUDGET_USD="${CODEX_REVIEW_MAX_BUDGET_USD:-0.50}"

usage() {
  printf 'usage: %s [--user-requirements-file PATH] [--user-requirements TEXT]\n' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --user-requirements-file) USER_REQUIREMENTS_FILE="$2"; shift ;;
    --user-requirements) USER_REQUIREMENTS_TEXT="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

if [ "$RUN_CLAUDE_REVIEW" != true ] || [ "$RUN_CODEX_REVIEW" != true ]; then
  printf '[independent-review] both Claude and Codex reviews are required for the quality gate\n' >&2
  exit 2
fi

mkdir -p "$RUN_DIR" "$CLAUDE_PRIVATE_DIR" "$CODEX_PRIVATE_DIR" "$CLAUDE_HOME" "$CODEX_HOME_FRESH"
chmod 700 "$PRIVATE_ROOT" "$CLAUDE_PRIVATE_DIR" "$CODEX_PRIVATE_DIR" "$CLAUDE_HOME" "$CODEX_HOME_FRESH" 2>/dev/null || true
if [ -n "${CODEX_HOME:-}" ] && [ -d "$CODEX_HOME" ]; then
  [ -f "$CODEX_HOME/config.toml" ] && cp "$CODEX_HOME/config.toml" "$CODEX_HOME_FRESH/config.toml"
  [ -f "$CODEX_HOME/auth.json" ] && cp "$CODEX_HOME/auth.json" "$CODEX_HOME_FRESH/auth.json"
  chmod 600 "$CODEX_HOME_FRESH"/* 2>/dev/null || true
fi
if [ -n "$CODEX_REVIEW_API_KEY" ]; then
  python3 - "$CODEX_HOME_FRESH/auth.json" "$CODEX_REVIEW_API_KEY" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({"OPENAI_API_KEY": sys.argv[2]}, indent=2), encoding="utf-8")
path.chmod(0o600)
PY
fi
if [ -n "$CODEX_REVIEW_BASE_URL" ]; then
  python3 - "$CODEX_HOME_FRESH/config.toml" "$CODEX_REVIEW_BASE_URL" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
base_url = sys.argv[2]
compat_provider = "ffc_openai_compat"
text = path.read_text(encoding="utf-8") if path.exists() else ""

def remove_top_level_key(config: str, key: str) -> str:
    top, sep, rest = config.partition("\n[")
    top = re.sub(rf'(?m)^{re.escape(key)}\s*=\s*".*"\n?', "", top)
    return top + (sep + rest if sep else "")

def replace_or_prepend_top_level_string(config: str, key: str, value: str) -> str:
    line = f'{key} = "{value}"'
    pattern = re.compile(rf'(?m)^{re.escape(key)}\s*=\s*".*"$')
    top, sep, rest = config.partition("\n[")
    if pattern.search(top):
        top = pattern.sub(line, top, count=1)
    else:
        top = f"{line}\n{top}"
    return top + (sep + rest if sep else "")

updated = text
top_level = re.split(r"(?m)^\s*\[", updated, maxsplit=1)[0]
match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"', top_level)
provider = match.group(1) if match else "openai"
if provider.lower() == "openai":
    provider = compat_provider
    updated = replace_or_prepend_top_level_string(updated, "model_provider", provider)
    updated = remove_top_level_key(updated, "openai_base_url")
else:
    if re.search(r'(?m)^openai_base_url\s*=', top_level):
        updated = replace_or_prepend_top_level_string(updated, "openai_base_url", base_url)

def replace_provider_base_url(config: str, provider_id: str) -> str:
    block_pattern = re.compile(rf"(?ms)(^\[model_providers\.{re.escape(provider_id)}\]\n.*?)(?=^\[|\Z)")
    block_match = block_pattern.search(config)
    if not block_match:
        return config.rstrip() + f'\n\n[model_providers.{provider_id}]\nbase_url = "{base_url}"\n'
    block = block_match.group(1)
    replaced_block = re.sub(r'(?m)^(base_url\s*=\s*").*(")\s*$', rf'\1{base_url}\2', block, count=1)
    if replaced_block == block:
        replaced_block = block.replace(f"[model_providers.{provider_id}]\n", f'[model_providers.{provider_id}]\nbase_url = "{base_url}"\n', 1)
    return config[: block_match.start(1)] + replaced_block + config[block_match.end(1) :]

def replace_provider_string(config: str, provider_id: str, key: str, value: str) -> str:
    block_pattern = re.compile(rf"(?ms)(^\[model_providers\.{re.escape(provider_id)}\]\n.*?)(?=^\[|\Z)")
    block_match = block_pattern.search(config)
    if not block_match:
        return config.rstrip() + f'\n\n[model_providers.{provider_id}]\n{key} = "{value}"\n'
    block = block_match.group(1)
    replaced_block = re.sub(rf'(?m)^{re.escape(key)}\s*=\s*".*"$', f'{key} = "{value}"', block, count=1)
    if replaced_block == block:
        replaced_block = block.replace(f"[model_providers.{provider_id}]\n", f'[model_providers.{provider_id}]\n{key} = "{value}"\n', 1)
    return config[: block_match.start(1)] + replaced_block + config[block_match.end(1) :]

def replace_provider_bool(config: str, provider_id: str, key: str, value: bool) -> str:
    block_pattern = re.compile(rf"(?ms)(^\[model_providers\.{re.escape(provider_id)}\]\n.*?)(?=^\[|\Z)")
    line = f"{key} = {'true' if value else 'false'}"
    block_match = block_pattern.search(config)
    if not block_match:
        return config.rstrip() + f"\n\n[model_providers.{provider_id}]\n{line}\n"
    block = block_match.group(1)
    replaced_block = re.sub(rf"(?m)^{re.escape(key)}\s*=\s*(?:true|false)$", line, block, count=1)
    if replaced_block == block:
        replaced_block = block.replace(f"[model_providers.{provider_id}]\n", f"[model_providers.{provider_id}]\n{line}\n", 1)
    return config[: block_match.start(1)] + replaced_block + config[block_match.end(1) :]

def replace_provider_int(config: str, provider_id: str, key: str, value: int) -> str:
    block_pattern = re.compile(rf"(?ms)(^\[model_providers\.{re.escape(provider_id)}\]\n.*?)(?=^\[|\Z)")
    line = f"{key} = {value}"
    block_match = block_pattern.search(config)
    if not block_match:
        return config.rstrip() + f"\n\n[model_providers.{provider_id}]\n{line}\n"
    block = block_match.group(1)
    replaced_block = re.sub(rf"(?m)^{re.escape(key)}\s*=\s*[0-9]+$", line, block, count=1)
    if replaced_block == block:
        replaced_block = block.replace(f"[model_providers.{provider_id}]\n", f"[model_providers.{provider_id}]\n{line}\n", 1)
    return config[: block_match.start(1)] + replaced_block + config[block_match.end(1) :]

updated = replace_provider_base_url(updated, provider)
updated = replace_provider_string(updated, provider, "wire_api", "responses")
updated = replace_provider_string(updated, provider, "env_key", "OPENAI_API_KEY")
updated = replace_provider_bool(updated, provider, "supports_websockets", False)
updated = replace_provider_int(updated, provider, "request_max_retries", 6)
updated = replace_provider_int(updated, provider, "stream_max_retries", 10)
updated = replace_provider_int(updated, provider, "stream_idle_timeout_ms", 600000)
if provider == compat_provider:
    updated = replace_provider_string(updated, provider, "name", "OpenAI-compatible proxy")
if "[model_providers.OpenAI]" in updated:
    updated = replace_provider_base_url(updated, "OpenAI")
path.write_text(updated, encoding="utf-8")
PY
fi

if [ -n "$CLAUDE_REVIEW_API_KEY" ]; then
  printf '%s\n' "$CLAUDE_REVIEW_API_KEY" > "$CLAUDE_HOME/.anthropic-api-key"
  chmod 600 "$CLAUDE_HOME/.anthropic-api-key" 2>/dev/null || true
fi

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

REVIEW_INPUT_FILES="$RUN_DIR/review-input-files.txt"
REVIEW_CANDIDATE_FILES="$RUN_DIR/review-candidate-files.txt"
{
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT" ls-files
    git -C "$ROOT" ls-files --others --exclude-standard
  else
    find "$ROOT" -type f -printf '%P\n'
  fi
} > "$REVIEW_CANDIDATE_FILES"
python3 - "$ROOT" "$REVIEW_CANDIDATE_FILES" > "$REVIEW_INPUT_FILES" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
candidates = Path(sys.argv[2])
excluded_prefixes = (
    ".git/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".nox/",
    ".venv/",
    ".venv-test/",
    "__pycache__/",
    "build/",
    "dist/",
    "htmlcov/",
    "node_modules/",
    "work/reviews/",
    "work/independent-reviews/",
    "work/private/",
)
excluded_suffixes = (".pyc", ".pyo", ".swp", ".tmp")
excluded_dir_names = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
}
seen: set[str] = set()
for raw in candidates.read_text(encoding="utf-8").splitlines():
    rel = raw.strip()
    if not rel or rel in seen:
        continue
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        continue
    normalized = rel_path.as_posix()
    if normalized.startswith(excluded_prefixes) or normalized.endswith(excluded_suffixes):
        continue
    if any(part in excluded_dir_names or part.startswith(".venv") for part in rel_path.parts):
        continue
    full = root / rel_path
    if not full.is_file():
        continue
    seen.add(normalized)
for rel in sorted(seen):
    print(rel)
PY

make_snapshot() {
  local target="$1"
  mkdir -p "$target"
  while IFS= read -r rel; do
    [ -f "$ROOT/$rel" ] || continue
    mkdir -p "$target/$(dirname "$rel")"
    cp "$ROOT/$rel" "$target/$rel"
  done < "$REVIEW_INPUT_FILES"
}

make_snapshot "$CLAUDE_REPO"
make_snapshot "$CODEX_REPO"

{
  printf '{\n'
  printf '  "run_id": "%s",\n' "$RUN_ID"
  printf '  "repository": "isolated reviewer snapshots only",\n'
  printf '  "input_sha256": {\n'
  first=true
  while IFS= read -r rel; do
    [ -f "$ROOT/$rel" ] || continue
    if [ "$first" = true ]; then
      first=false
    else
      printf ',\n'
    fi
    printf '    "%s": "%s"' "$rel" "$(sha256_file "$ROOT/$rel")"
  done < "$REVIEW_INPUT_FILES"
  printf '\n  }\n'
  printf '}\n'
} > "$RUN_DIR/manifest.json"

REVIEW_MANIFEST_SUMMARY="$RUN_DIR/manifest-summary.json"
python3 - "$RUN_DIR/manifest.json" "$REVIEW_INPUT_FILES" "$REVIEW_MANIFEST_SUMMARY" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
input_files_path = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
input_files = input_files_path.read_text(encoding="utf-8").splitlines()
summary = {
    "run_id": manifest.get("run_id"),
    "repository": manifest.get("repository"),
    "input_file_count": len(input_files),
    "input_file_sample": input_files[:120],
    "full_manifest_file": str(manifest_path),
    "review_input_files_file": str(input_files_path),
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

if [ -n "$USER_REQUIREMENTS_FILE" ]; then
  cp "$USER_REQUIREMENTS_FILE" "$RUN_DIR/user-requirements.txt"
else
  printf '%s\n' "$USER_REQUIREMENTS_TEXT" > "$RUN_DIR/user-requirements.txt"
fi

COMMON_REQUIREMENTS="$RUN_DIR/common-review-requirements.md"
cat > "$COMMON_REQUIREMENTS" <<'EOF'
You are an independent reviewer, not the master-writer.
Run this review as a fresh conversation. Do not use resume, continue, prior reviewer memory, or another reviewer's output.
Review only the isolated repository snapshot and the user requirements supplied in your prompt.
Do not inspect the parent repository work/reviews directory, sibling snapshots, or any other reviewer output path.
Each reviewer is launched with a fresh HOME. Codex is launched with a fresh CODEX_HOME containing only copied auth/config files when available. Claude Code receives only explicit reviewer API env/auth inputs and must not use resume/continue state.

Current user requirements to verify:
	- Installer must support AI_RUNNER_COMPONENTS=all,telegram for one Debian 12 VM with Claude Code, Codex, VSCode, runner, and Telegram installed globally for root usage.
	- Focused deployments must still work with codex,telegram OR claude-code,telegram OR vscode,telegram; mixed primary tools are allowed, but the runner must keep one active default provider at a time and switch explicitly.
	- Installer must install/verify every explicitly requested provider/tool globally for root usage and must clean stale unrequested provider config files when a VM is re-role-installed.
	- Runner and Telegram services must run as root unless explicitly overridden.
	- Claude Code and Codex must default to full access inside the VM: network access, shell access, file access, install-anything capability.
	- VSCode must be installed or verified during runner install and must be usable as a root/full-access tool through the root wrapper.
- Telegram and Mattermost must be equal first-class phone communication channels. Telegram may be the user's primary daily phone entrypoint because it is simpler and faster.
- Telegram commands and status messages must be aligned with the runner command table, including visible queued/calling/running heartbeat states while AI tasks are active.
- Mattermost must preserve parity with Telegram for the same command table, status vocabulary, provider selection, long-conversation policy, and full-access controls.
- Do not add extra sandbox/privacy/safety boundaries beyond the VM boundary, credential-not-in-Git, credential-not-in-prompts, and budget controls that already exist.
	- Master-writer and reviewer AIs must be separate. Reviewers must run in fresh new conversations with no context pollution and must not simply be the master-writer reviewing itself.
	- Reviewer runs must have explicit spend controls. Claude uses --max-budget-usd. Codex currently has no native dollar cap in this CLI, so the script must enforce a hard timeout and record CODEX_REVIEW_MAX_BUDGET_USD as the operator-approved budget ceiling.

Report sections exactly:
P0
P1
High-value P2
Missing user requirements
Creative proposals
Over-compression findings
Over-engineering findings
Must fix
Score
EOF

CLAUDE_PROMPT="$RUN_DIR/claude-review-prompt.md"
cat > "$CLAUDE_PROMPT" <<EOF
$(cat "$COMMON_REQUIREMENTS")

Claude Code reviewer focus:
- Verify Claude Code CLI invocation feasibility.
- Verify install-runner.sh does not leave Claude in plan-only or no-tool mode when full access is requested.
- Verify no --continue, --resume, or persisted previous review context is used for this review.
- Verify implementation scripts, not only docs.

Isolated repository snapshot: $CLAUDE_REPO
User requirements:
$(cat "$RUN_DIR/user-requirements.txt")
Manifest summary:
$(cat "$REVIEW_MANIFEST_SUMMARY")
EOF

CODEX_PROMPT="$RUN_DIR/codex-review-prompt.md"
cat > "$CODEX_PROMPT" <<EOF
$(cat "$COMMON_REQUIREMENTS")

Codex/GPT reviewer focus:
	- Verify Codex exec invocation uses full access or dangerous bypass when supported.
	- Verify root/global installation and service environment alignment.
	- Verify VSCode root/full-access installation and wrapper behavior.
	- Verify Telegram status/heartbeat behavior makes queued, thinking/running, and stuck-vs-running states visible to phone users.
	- Verify command UX can switch to full access and default policy is full.
	- Verify independent reviewer workflow is script-enforced rather than only documented.
	- Treat CODEX_REVIEW_MAX_BUDGET_USD=$CODEX_REVIEW_MAX_BUDGET_USD and timeout=$CODEX_REVIEW_TIMEOUT_SECONDS seconds as this run's Codex spend guard unless this Codex CLI exposes a native budget flag.

Isolated repository snapshot: $CODEX_REPO
User requirements:
$(cat "$RUN_DIR/user-requirements.txt")
Manifest summary:
$(cat "$REVIEW_MANIFEST_SUMMARY")
EOF

run_claude_review() {
  local output="$CLAUDE_PRIVATE_DIR/review.md"
  if ! command -v claude >/dev/null 2>&1; then
    printf 'claude command not found\n' > "$output"
    return 127
  fi
  local env_args=(HOME="$CLAUDE_HOME")
  if [ -n "$CLAUDE_REVIEW_BASE_URL" ]; then
    env_args+=(ANTHROPIC_BASE_URL="$CLAUDE_REVIEW_BASE_URL")
  fi
  if [ -n "$CLAUDE_REVIEW_AUTH_TOKEN" ]; then
    env_args+=(ANTHROPIC_AUTH_TOKEN="$CLAUDE_REVIEW_AUTH_TOKEN")
  fi
  if [ -n "$CLAUDE_REVIEW_API_KEY" ]; then
    env_args+=(ANTHROPIC_API_KEY="$CLAUDE_REVIEW_API_KEY")
  fi
  if [ -z "$CLAUDE_REVIEW_AUTH_TOKEN" ] && [ -z "$CLAUDE_REVIEW_API_KEY" ]; then
    printf 'claude reviewer requires CLAUDE_REVIEW_API_KEY, ANTHROPIC_API_KEY, CLAUDE_REVIEW_AUTH_TOKEN, or ANTHROPIC_AUTH_TOKEN because fresh HOME + --bare cannot rely on original-home OAuth/keychain auth\n' > "$output"
    return 2
  fi
  local cmd=(env "${env_args[@]}" claude -p --bare --no-session-persistence --output-format json --permission-mode acceptEdits --tools Bash,Read,Write,Edit,Grep,Glob --add-dir "$CLAUDE_REPO" --max-budget-usd "$CLAUDE_REVIEW_MAX_BUDGET_USD" --append-system-prompt "$(cat "$CLAUDE_PROMPT")")
  if [ -n "$CLAUDE_REVIEWER_MODEL" ]; then
    cmd+=(--model "$CLAUDE_REVIEWER_MODEL")
  fi
  timeout "$CLAUDE_REVIEW_TIMEOUT_SECONDS" "${cmd[@]}" "Run the independent Claude Code reviewer now. Return only the review report." > "$CLAUDE_PRIVATE_DIR/review.raw.json" 2> "$CLAUDE_PRIVATE_DIR/review.stderr" || return $?
  python3 - "$CLAUDE_PRIVATE_DIR/review.raw.json" "$output" <<'PY'
import json
import sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text(encoding="utf-8")
try:
    data = json.loads(raw)
    text = data.get("result") or data.get("message") or raw
except json.JSONDecodeError:
    text = raw
Path(sys.argv[2]).write_text(str(text).strip() + "\n", encoding="utf-8")
PY
}

run_codex_review() {
  local output="$CODEX_PRIVATE_DIR/review.md"
  local last_message="$CODEX_PRIVATE_DIR/review-last-message.md"
  if ! command -v codex >/dev/null 2>&1; then
    printf 'codex command not found\n' > "$output"
    return 127
  fi
  if [ -f "$CODEX_HOME_FRESH/config.toml" ] && grep -q 'example\.invalid' "$CODEX_HOME_FRESH/config.toml"; then
    printf 'codex reviewer config contains placeholder base_url example.invalid; set CODEX_REVIEW_BASE_URL to a real Responses-compatible endpoint\n' > "$output"
    return 2
  fi
  if [ -f "$CODEX_HOME_FRESH/auth.json" ] && python3 - "$CODEX_HOME_FRESH/auth.json" <<'PY'
import json
import sys
from pathlib import Path

try:
    key = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("OPENAI_API_KEY", "")
except Exception:
    key = ""
raise SystemExit(0 if key.startswith("test-") else 1)
PY
  then
    printf 'codex reviewer auth contains a test placeholder API key; set CODEX_REVIEW_API_KEY or OPENAI_API_KEY for the reviewer\n' > "$output"
    return 2
  fi
  local help_text
  help_text="$(codex exec --help 2>&1 || true)"
  printf '%s' "$help_text" | grep -q -- '--json' || {
    printf 'codex exec --json is required for review event capture\n' > "$output"
    return 2
  }
  printf '%s' "$help_text" | grep -q -- '--output-last-message' || {
    printf 'codex exec --output-last-message is required for review capture\n' > "$output"
    return 2
  }
  local cmd=(env HOME="$CODEX_HOME_FRESH" CODEX_HOME="$CODEX_HOME_FRESH" TERM="${TERM:-xterm-256color}" codex exec -c 'approval_policy="never"' -c 'shell_environment_policy.inherit=all' --json)
  if printf '%s' "$help_text" | grep -q -- '--ephemeral'; then
    cmd+=(--ephemeral)
  fi
  if printf '%s' "$help_text" | grep -q -- '--color'; then
    cmd+=(--color never)
  fi
  if printf '%s' "$help_text" | grep -q -- '--dangerously-bypass-approvals-and-sandbox'; then
    cmd+=(--dangerously-bypass-approvals-and-sandbox)
  elif printf '%s' "$help_text" | grep -q -- '--sandbox'; then
    cmd+=(--sandbox danger-full-access)
  else
    printf 'codex full access is unavailable: dangerous bypass or danger-full-access sandbox is required\n' > "$output"
    return 2
  fi
  if printf '%s' "$help_text" | grep -q -- '--dangerously-bypass-hook-trust'; then
    cmd+=(--dangerously-bypass-hook-trust)
  fi
  if printf '%s' "$help_text" | grep -q -- '--ignore-rules'; then
    cmd+=(--ignore-rules)
  fi
  if printf '%s' "$help_text" | grep -q -- '--add-dir'; then
    cmd+=(--add-dir "$CODEX_REPO")
  else
    printf 'codex full access is unavailable: --add-dir is required for isolated review snapshots\n' > "$output"
    return 2
  fi
  if printf '%s' "$help_text" | grep -q -- '--skip-git-repo-check'; then
    cmd+=(--skip-git-repo-check)
  fi
  if [ -n "$CODEX_REVIEWER_MODEL" ]; then
    cmd+=(--model "$CODEX_REVIEWER_MODEL")
  fi
  if printf '%s' "$help_text" | grep -q -- '--max-budget-usd'; then
    cmd+=(--max-budget-usd "$CODEX_REVIEW_MAX_BUDGET_USD")
  fi
  cmd+=(--cd "$CODEX_REPO" --output-last-message "$last_message" -- -)
  timeout "$CODEX_REVIEW_TIMEOUT_SECONDS" "${cmd[@]}" < "$CODEX_PROMPT" > "$CODEX_PRIVATE_DIR/review.raw.jsonl" 2> "$CODEX_PRIVATE_DIR/review.stderr" || return $?
  if [ -f "$last_message" ]; then
    cp "$last_message" "$output"
  else
    cp "$CODEX_PRIVATE_DIR/review.raw.jsonl" "$output"
  fi
}

claude_status="skipped"
codex_status="skipped"
claude_pid=""
codex_pid=""
claude_status_file="$CLAUDE_PRIVATE_DIR/status"
codex_status_file="$CODEX_PRIVATE_DIR/status"

wait_for_start_gate() {
  while [ ! -e "$START_GATE" ]; do
    sleep 0.05
  done
}

if [ "$RUN_CLAUDE_REVIEW" = true ]; then
  (
    wait_for_start_gate
    if run_claude_review; then
      printf 'completed\n' > "$claude_status_file"
    else
      printf 'failed\n' > "$claude_status_file"
    fi
  ) &
  claude_pid=$!
fi

if [ "$RUN_CODEX_REVIEW" = true ]; then
  (
    wait_for_start_gate
    if run_codex_review; then
      printf 'completed\n' > "$codex_status_file"
    else
      printf 'failed\n' > "$codex_status_file"
    fi
  ) &
  codex_pid=$!
fi

: > "$START_GATE"

if [ -n "$claude_pid" ]; then
  wait "$claude_pid" || true
  claude_status="$(cat "$claude_status_file" 2>/dev/null || printf 'failed')"
fi
if [ -n "$codex_pid" ]; then
  wait "$codex_pid" || true
  codex_status="$(cat "$codex_status_file" 2>/dev/null || printf 'failed')"
fi

if [ "$claude_status" != completed ] || [ "$codex_status" != completed ]; then
  review_exit=1
else
  review_exit=0
fi

gate_exit=0
python3 - "$RUN_DIR" "$claude_status" "$codex_status" "$PRIVATE_ROOT" "$review_exit" "$CLAUDE_REVIEW_MAX_BUDGET_USD" "$CLAUDE_REVIEW_TIMEOUT_SECONDS" "$CODEX_REVIEW_MAX_BUDGET_USD" "$CODEX_REVIEW_TIMEOUT_SECONDS" <<'PY' || gate_exit=$?
import json
import re
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
private_root = Path(sys.argv[4])
process_failed = int(sys.argv[5]) != 0
claude_budget, claude_timeout, codex_budget, codex_timeout = sys.argv[6:10]
manifest["reviewers"] = {
    "claude-code": {"status": sys.argv[2], "output": str(private_root / "claude" / "review.md")},
    "codex": {"status": sys.argv[3], "output": str(private_root / "codex" / "review.md")},
}
manifest["budget_controls"] = {
    "claude_max_budget_usd": claude_budget,
    "claude_timeout_seconds": claude_timeout,
    "codex_max_budget_usd": codex_budget,
    "codex_timeout_seconds": codex_timeout,
    "codex_native_budget_flag_used_when_available": True,
}
private_root_str = str(private_root.resolve())
run_dir_str = str(run_dir.resolve())
claude_repo = run_dir / "snapshots" / "claude-repo"
codex_repo = run_dir / "snapshots" / "codex-repo"


def snapshot_contains_review_output(path: Path) -> bool:
    for candidate in path.rglob("*"):
        if not candidate.is_file():
            continue
        normalized = candidate.as_posix()
        if "/review.md" in normalized or "/review.raw" in normalized or "/review.stderr" in normalized:
            return True
    return False


manifest["isolation"] = {
    "fresh_prompts": True,
    "fresh_home_per_reviewer": True,
    "fresh_codex_home": True,
    "concurrent_reviewer_launch": True,
    "start_gate_used": True,
    "reviewers_started_before_waiting_for_outputs": True,
    "separate_repository_snapshots": True,
    "private_outputs_outside_run_dir": not private_root_str.startswith(run_dir_str),
    "review_outputs_outside_visible_snapshots": not snapshot_contains_review_output(claude_repo) and not snapshot_contains_review_output(codex_repo),
    "claude_fresh_home_path": str(private_root / "claude-home"),
    "codex_fresh_home_path": str(private_root / "codex-home"),
    "residual_same_os_user_full_access_risk": True,
    "residual_risk_accepted_reason": "Reviewers intentionally run with full-access tools inside the VM; script-enforced isolation uses fresh homes, separate snapshots, concurrent launch, and private outputs, while OS-user/container isolation is a future hardening option.",
    "reviewers_read_each_other_output_before_completion": False,
    "master_writer_self_review_only": False,
}

HEADINGS = [
    "P0",
    "P1",
    "High-value P2",
    "Missing user requirements",
    "Creative proposals",
    "Over-compression findings",
    "Over-engineering findings",
    "Must fix",
    "Score",
]


def normalize_heading(line: str) -> str:
    return line.strip().strip("#").strip().strip("*").strip()


def sections(text: str) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    positions: dict[str, int] = {}
    current: str | None = None
    for index, line in enumerate(text.splitlines()):
        heading = normalize_heading(line)
        if heading in HEADINGS:
            current = heading
            found.setdefault(current, [])
            positions.setdefault(current, index)
            continue
        if current:
            found[current].append(line)
    result = {key: "\n".join(value).strip() for key, value in found.items()}
    result["__positions__"] = json.dumps(positions)
    return result


def is_clear(value: str) -> bool:
    compact = re.sub(r"[\s。.,，:：*`_-]+", "", value).lower()
    return compact in {"", "none", "无", "没有", "n/a", "na", "0"}


def score_value(value: str) -> int | None:
    match = re.search(r"\b(\d{1,3})\b", value)
    if not match:
        return None
    return int(match.group(1))


quality_gate = {"passed": not process_failed, "minimum_score": 90, "reviewers": {}}
if process_failed:
    quality_gate["process_failed"] = True

for name, output_path in (
    ("claude-code", private_root / "claude" / "review.md"),
    ("codex", private_root / "codex" / "review.md"),
):
    text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    parsed = sections(text)
    positions = json.loads(parsed.pop("__positions__", "{}"))
    required_sections_present = all(heading in parsed for heading in HEADINGS)
    exact_section_order = required_sections_present and [heading for heading in HEADINGS if heading in positions] == HEADINGS and all(
        positions[HEADINGS[index]] < positions[HEADINGS[index + 1]] for index in range(len(HEADINGS) - 1)
    )
    p0_clear = is_clear(parsed.get("P0", ""))
    p1_clear = is_clear(parsed.get("P1", ""))
    p2_clear = is_clear(parsed.get("High-value P2", ""))
    missing_clear = is_clear(parsed.get("Missing user requirements", ""))
    must_fix_clear = is_clear(parsed.get("Must fix", ""))
    score = score_value(parsed.get("Score", ""))
    reviewer_gate = {
        "p0_clear": p0_clear,
        "p1_clear": p1_clear,
        "high_value_p2_clear": p2_clear,
        "missing_user_requirements_clear": missing_clear,
        "must_fix_clear": must_fix_clear,
        "score": score,
        "required_sections_present": required_sections_present,
        "exact_section_order": exact_section_order,
        "passed": required_sections_present and exact_section_order and p0_clear and p1_clear and p2_clear and missing_clear and must_fix_clear and score is not None and score >= 90,
    }
    if not reviewer_gate["passed"]:
        quality_gate["passed"] = False
    quality_gate["reviewers"][name] = reviewer_gate

manifest["quality_gate"] = quality_gate
(run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
if not quality_gate["passed"]:
    raise SystemExit(1)
PY
if [ "$gate_exit" -ne 0 ]; then
  review_exit=1
fi

printf '[independent-review] run_dir=%s claude=%s codex=%s gate=%s\n' "$RUN_DIR" "$claude_status" "$codex_status" "$([ "$review_exit" -eq 0 ] && printf passed || printf failed)"
exit "$review_exit"
