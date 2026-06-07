from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PASSING_REVIEW = (
    "P0\nnone\n"
    "P1\nnone\n"
    "High-value P2\nnone\n"
    "Missing user requirements\nnone\n"
    "Creative proposals\nnone\n"
    "Over-compression findings\nnone\n"
    "Over-engineering findings\nnone\n"
    "Must fix\nnone\n"
    "Score\n95\n"
)
P1_REVIEW = (
    "P0\nnone\n"
    "P1\ninstaller still broken\n"
    "High-value P2\nnone\n"
    "Missing user requirements\nnone\n"
    "Creative proposals\nnone\n"
    "Over-compression findings\nnone\n"
    "Over-engineering findings\nnone\n"
    "Must fix\nfix installer\n"
    "Score\n80\n"
)
TRUNCATED_REVIEW = "P0\nnone\nP1\nnone\nScore\n95\n"


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class IndependentReviewScriptTests(unittest.TestCase):
    def test_runs_two_fresh_independent_reviewers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            calls = root / "calls.jsonl"

            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"tool":"claude","args":%s}\\n' "$(python3 - "$@" <<'PY'
import json, sys
print(json.dumps(sys.argv[1:]))
PY
)" >> "${CALLS:?}"
                printf '{"result":"%s"}\\n' "${PASSING_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--ephemeral] [--color] [--dangerously-bypass-approvals-and-sandbox] [--dangerously-bypass-hook-trust] [--ignore-rules] [--add-dir] [--skip-git-repo-check]\\n'
                  exit 0
                fi
                printf '{"tool":"codex","args":%s}\\n' "$(python3 - "$@" <<'PY'
import json, sys
print(json.dumps(sys.argv[1:]))
PY
)" >> "${CALLS:?}"
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${PASSING_REVIEW:?}" > "$last"
                printf '{}\\n'
                """,
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "unit-review",
                    "CALLS": str(calls),
                    "CODEX_HOME": "",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "PASSING_REVIEW": PASSING_REVIEW,
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access and independent reviewers"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run_dir = review_root / "unit-review"
            self.assertTrue((run_dir / "claude-review-prompt.md").exists())
            self.assertTrue((run_dir / "codex-review-prompt.md").exists())
            self.assertFalse((run_dir / "snapshots" / "claude-repo" / "private" / "codex" / "review.md").exists())
            self.assertFalse((run_dir / "snapshots" / "codex-repo" / "private" / "claude" / "review.md").exists())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["reviewers"]["claude-code"]["status"], "completed")
            self.assertEqual(manifest["reviewers"]["codex"]["status"], "completed")
            self.assertTrue(manifest["quality_gate"]["passed"])
            claude_output = Path(manifest["reviewers"]["claude-code"]["output"])
            codex_output = Path(manifest["reviewers"]["codex"]["output"])
            self.assertTrue(claude_output.exists())
            self.assertTrue(codex_output.exists())
            self.assertFalse(str(claude_output).startswith(str(run_dir)))
            self.assertFalse(str(codex_output).startswith(str(run_dir)))
            self.assertFalse(manifest["isolation"]["reviewers_read_each_other_output_before_completion"])
            self.assertFalse(manifest["isolation"]["master_writer_self_review_only"])
            self.assertTrue(manifest["isolation"]["separate_repository_snapshots"])
            self.assertTrue(manifest["isolation"]["review_outputs_outside_visible_snapshots"])
            self.assertTrue(manifest["isolation"]["fresh_home_per_reviewer"])
            self.assertTrue(manifest["isolation"]["fresh_codex_home"])
            self.assertTrue(manifest["isolation"]["concurrent_reviewer_launch"])
            self.assertTrue(manifest["isolation"]["start_gate_used"])
            self.assertTrue(manifest["isolation"]["reviewers_started_before_waiting_for_outputs"])
            self.assertTrue(manifest["isolation"]["private_outputs_outside_run_dir"])
            self.assertTrue(manifest["isolation"]["residual_same_os_user_full_access_risk"])
            self.assertIn("full-access tools", manifest["isolation"]["residual_risk_accepted_reason"])
            self.assertEqual(manifest["budget_controls"]["claude_max_budget_usd"], "0.50")
            self.assertEqual(manifest["budget_controls"]["codex_max_budget_usd"], "0.50")
            self.assertEqual(manifest["budget_controls"]["codex_timeout_seconds"], "900")
            self.assertTrue((run_dir / "snapshots" / "claude-repo" / "src" / "ai_remote_runner" / "bridge.py").exists())
            self.assertTrue((run_dir / "snapshots" / "claude-repo" / "src" / "ai_remote_runner" / "cli.py").exists())
            self.assertTrue((run_dir / "snapshots" / "claude-repo" / "src" / "ai_remote_runner" / "events.py").exists())
            self.assertTrue((run_dir / "snapshots" / "claude-repo" / "scripts" / "pair-runner.sh").exists())
            self.assertTrue((run_dir / "snapshots" / "codex-repo" / "tests" / "test_bridge.py").exists())

            rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            claude_args = next(row["args"] for row in rows if row["tool"] == "claude")
            codex_args = next(row["args"] for row in rows if row["tool"] == "codex")
            self.assertNotIn("--continue", claude_args)
            self.assertNotIn("--resume", claude_args)
            self.assertIn("--bare", claude_args)
            self.assertIn("--no-session-persistence", claude_args)
            self.assertIn("--permission-mode", claude_args)
            self.assertIn("acceptEdits", claude_args)
            self.assertIn("Bash,Read,Write,Edit,Grep,Glob", claude_args)
            self.assertIn("--max-budget-usd", claude_args)
            self.assertEqual(claude_args[claude_args.index("--max-budget-usd") + 1], "0.50")
            self.assertNotIn("bypassPermissions", claude_args)
            self.assertNotIn("--dangerously-skip-permissions", claude_args)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex_args)
            self.assertIn("shell_environment_policy.inherit=all", codex_args)
            self.assertNotIn("sandbox_workspace_write.network_access=true", codex_args)
            self.assertNotIn('network_access="enabled"', codex_args)
            self.assertIn("--dangerously-bypass-hook-trust", codex_args)
            self.assertIn("--ignore-rules", codex_args)
            self.assertIn("--add-dir", codex_args)
            self.assertIn(str(run_dir / "snapshots" / "codex-repo"), codex_args)
            self.assertNotIn("--max-budget-usd", codex_args)
            self.assertEqual(codex_args[-1], "-")
            self.assertNotIn("Run this review as a fresh conversation", codex_args)

    def test_codex_review_base_url_override_replaces_placeholder_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            codex_home = root / "codex-home-source"
            fakebin.mkdir()
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                '[model_providers.OpenAI]\nbase_url = "https://example.invalid/v1"\n',
                encoding="utf-8",
            )
            (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"test"}\n', encoding="utf-8")
            observed_config = root / "observed-config.toml"
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"result":"%s"}\\n' "${PASSING_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                f"""
                #!/usr/bin/env bash
                if [ "${{1:-}}" = "exec" ] && [ "${{2:-}}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir]\\n'
                  exit 0
                fi
                cp "${{CODEX_HOME:?}}/config.toml" "{observed_config}"
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${{PASSING_REVIEW:?}}" > "$last"
                printf '{{}}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "override-review",
                    "CODEX_HOME": str(codex_home),
                    "CODEX_REVIEW_BASE_URL": "https://review.example/v1",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "PASSING_REVIEW": PASSING_REVIEW,
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            observed_text = observed_config.read_text(encoding="utf-8")
            self.assertIn('openai_base_url = "https://review.example/v1"', observed_text)
            self.assertIn('base_url = "https://review.example/v1"', observed_text)

    def test_codex_review_api_key_override_replaces_placeholder_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            codex_home = root / "codex-home-source"
            fakebin.mkdir()
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('[model_providers.OpenAI]\nbase_url = "https://review.example/v1"\n', encoding="utf-8")
            (codex_home / "auth.json").write_text('{"OPENAI_API_KEY":"test-placeholder"}\n', encoding="utf-8")
            observed_auth = root / "observed-auth.json"
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"result":"%s"}\\n' "${PASSING_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                f"""
                #!/usr/bin/env bash
                if [ "${{1:-}}" = "exec" ] && [ "${{2:-}}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir]\\n'
                  exit 0
                fi
                cp "${{CODEX_HOME:?}}/auth.json" "{observed_auth}"
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${{PASSING_REVIEW:?}}" > "$last"
                printf '{{}}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "auth-override-review",
                    "CODEX_HOME": str(codex_home),
                    "CODEX_REVIEW_API_KEY": "review-api-key-fixture",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "PASSING_REVIEW": PASSING_REVIEW,
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            observed = json.loads(observed_auth.read_text(encoding="utf-8"))
            self.assertEqual(observed["OPENAI_API_KEY"], "review-api-key-fixture")

    def test_codex_native_budget_flag_is_used_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            calls = root / "calls.jsonl"
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"result":"%s"}\\n' "${PASSING_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir] [--max-budget-usd]\\n'
                  exit 0
                fi
                printf '{"args":%s}\\n' "$(python3 - "$@" <<'PY'
import json, sys
print(json.dumps(sys.argv[1:]))
PY
)" >> "${CALLS:?}"
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${PASSING_REVIEW:?}" > "$last"
                printf '{}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "codex-budget-review",
                    "CODEX_HOME": "",
                    "CODEX_REVIEW_MAX_BUDGET_USD": "0.17",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "PASSING_REVIEW": PASSING_REVIEW,
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                    "CALLS": str(calls),
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            args = json.loads(calls.read_text(encoding="utf-8").splitlines()[0])["args"]
            self.assertIn("--max-budget-usd", args)
            self.assertEqual(args[args.index("--max-budget-usd") + 1], "0.17")
            manifest = json.loads((review_root / "codex-budget-review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["budget_controls"]["codex_max_budget_usd"], "0.17")

    def test_review_failure_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            write_executable(fakebin / "claude", "#!/usr/bin/env bash\nprintf '{\"result\":\"ok\"}\\n'\n")
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec\\n'
                  exit 0
                fi
                exit 1
                """,
            )
            env = os.environ.copy()
            env.update({"PATH": f"{fakebin}:/usr/bin:/bin", "AI_REVIEW_ROOT": str(review_root), "AI_REVIEW_RUN_ID": "failed-review", "CODEX_HOME": "", "CLAUDE_REVIEW_AUTH_TOKEN": "review-token"})
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((review_root / "failed-review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["reviewers"]["codex"]["status"], "failed")
            self.assertFalse(manifest["quality_gate"]["passed"])

    def test_review_quality_gate_fails_on_reported_p1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"result":"%s"}\\n' "${P1_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir]\\n'
                  exit 0
                fi
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${PASSING_REVIEW:?}" > "$last"
                printf '{}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "p1-review",
                    "CODEX_HOME": "",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "P1_REVIEW_JSON": P1_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                    "PASSING_REVIEW": PASSING_REVIEW,
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((review_root / "p1-review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["quality_gate"]["passed"])
            self.assertFalse(manifest["quality_gate"]["reviewers"]["claude-code"]["p1_clear"])

    def test_review_quality_gate_fails_on_missing_required_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            write_executable(
                fakebin / "claude",
                """
                #!/usr/bin/env bash
                printf '{"result":"%s"}\\n' "${PASSING_REVIEW_JSON:?}"
                """,
            )
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir]\\n'
                  exit 0
                fi
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${TRUNCATED_REVIEW:?}" > "$last"
                printf '{}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "truncated-review",
                    "CODEX_HOME": "",
                    "CLAUDE_REVIEW_AUTH_TOKEN": "review-token",
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                    "TRUNCATED_REVIEW": TRUNCATED_REVIEW,
                }
            )
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((review_root / "truncated-review" / "manifest.json").read_text(encoding="utf-8"))
            codex_gate = manifest["quality_gate"]["reviewers"]["codex"]
            self.assertFalse(codex_gate["required_sections_present"])
            self.assertFalse(codex_gate["exact_section_order"])

    def test_claude_reviewer_requires_explicit_fresh_home_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            review_root = root / "reviews"
            fakebin.mkdir()
            write_executable(fakebin / "claude", "#!/usr/bin/env bash\nprintf '{\"result\":\"%s\"}\\n' \"${PASSING_REVIEW_JSON:?}\"\n")
            write_executable(
                fakebin / "codex",
                """
                #!/usr/bin/env bash
                if [ "${1:-}" = "exec" ] && [ "${2:-}" = "--help" ]; then
                  printf 'usage: codex exec [--json] [--output-last-message] [--dangerously-bypass-approvals-and-sandbox] [--add-dir]\\n'
                  exit 0
                fi
                last=''
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--output-last-message" ]; then last="$2"; shift 2; continue; fi
                  shift
                done
                printf '%s' "${PASSING_REVIEW:?}" > "$last"
                printf '{}\\n'
                """,
            )
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:/usr/bin:/bin",
                    "AI_REVIEW_ROOT": str(review_root),
                    "AI_REVIEW_RUN_ID": "claude-auth-review",
                    "CODEX_HOME": "",
                    "PASSING_REVIEW": PASSING_REVIEW,
                    "PASSING_REVIEW_JSON": PASSING_REVIEW.replace("\\", "\\\\").replace("\n", "\\n"),
                }
            )
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            env.pop("ANTHROPIC_API_KEY", None)
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "run-independent-review.sh"), "--user-requirements", "full access"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((review_root / "claude-auth-review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["reviewers"]["claude-code"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
