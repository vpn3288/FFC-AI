from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.providers import (
    CLAUDE_MODEL_FALLBACKS,
    SUPPORTED_PROVIDER_NAMES,
    codex_command,
    configured_provider_names_from_env,
    invoke_claude,
    invoke_codex,
    invoke_vscode,
    is_supported_provider,
    normalize_provider_name,
    provider_status,
)
from ai_remote_runner.providers import _emit_codex_jsonl_events
from ai_remote_runner.providers import discover_codex
from ai_remote_runner.budget import BudgetLedger


class ProviderTests(unittest.TestCase):
    def test_provider_registry_normalizes_aliases_and_filters_env(self) -> None:
        self.assertEqual(normalize_provider_name("claude"), "claude-code")
        self.assertEqual(normalize_provider_name("code"), "vscode")
        self.assertEqual(normalize_provider_name("openai"), "codex")
        self.assertTrue(is_supported_provider("vs-code"))
        with patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "code,codex,code,unknown"}, clear=False):
            self.assertEqual(configured_provider_names_from_env(), ["vscode", "codex"])

    def test_provider_registry_returns_all_supported_when_unconfigured(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(configured_provider_names_from_env(default_all=True), list(SUPPORTED_PROVIDER_NAMES))

    def test_codex_command_uses_supported_approval_config(self) -> None:
        def supported(_: list[str], *needles: str) -> bool:
            flags = {
                "--dangerously-bypass-approvals-and-sandbox",
                "--dangerously-bypass-hook-trust",
                "--ignore-rules",
                "--add-dir",
                "--skip-git-repo-check",
                "--output-last-message",
                "--ephemeral",
                "--cd",
                "--json",
            }
            return all(needle in flags for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=supported):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("-c", command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn("sandbox_workspace_write.network_access=true", command)
        self.assertIn("shell_environment_policy.inherit=all", command)
        self.assertNotIn("network_access=\"enabled\"", command)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn("--dangerously-bypass-hook-trust", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--add-dir", command)
        self.assertEqual(command[command.index("--add-dir") + 1], "/")
        self.assertNotIn("--sandbox", command)
        self.assertNotIn("--ask-for-approval", command)
        self.assertNotIn("--ignore-user-config", command)

    def test_codex_command_includes_instruction_prompt(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=True):
            command = codex_command("do work", Path("/tmp/work"), Path("/tmp/out.txt"), "# Global Instructions\nBe careful")
        self.assertNotIn("# Global Instructions\nBe careful\n\n# User Task\ndo work", command)
        self.assertEqual(command[-1], "-")

    def test_claude_model_fallbacks_are_configured(self) -> None:
        self.assertIn("claude-opus-4-7", CLAUDE_MODEL_FALLBACKS)
        self.assertIn("claude-opus-4-8-thinking", CLAUDE_MODEL_FALLBACKS)

    def test_codex_discovery_reports_approval_config_key(self) -> None:
        capabilities = discover_codex()["capabilities"]
        self.assertIn("approval_config_available", capabilities)
        self.assertIn("sandbox_available", capabilities)
        self.assertIn("full_access_available", capabilities)
        self.assertIn("full_access_mode", capabilities)
        self.assertIn("full_access_flags", capabilities)
        self.assertIn("telegram_live_status_available", capabilities)
        self.assertIn("output_last_message_available", capabilities)

    def test_provider_status_marks_unconfigured_provider_unavailable(self) -> None:
        with (
            patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "codex"}, clear=False),
            patch("ai_remote_runner.providers.discover_claude") as discover_claude,
            patch("ai_remote_runner.providers.discover_codex", return_value={"provider": "codex", "available": True}),
        ):
            status = provider_status()
        discover_claude.assert_not_called()
        claude = next(item for item in status if item["provider"] == "claude-code")
        codex = next(item for item in status if item["provider"] == "codex")
        self.assertFalse(claude["available"])
        self.assertFalse(claude["configured"])
        self.assertEqual(claude["status"], "not_configured_on_this_machine")
        self.assertTrue(codex["available"])
        self.assertTrue(codex["configured"])

    def test_provider_status_does_not_probe_any_provider_in_management_only_mode(self) -> None:
        with (
            patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": ""}, clear=False),
            patch("ai_remote_runner.providers.discover_claude") as discover_claude,
            patch("ai_remote_runner.providers.discover_codex") as discover_codex,
        ):
            status = provider_status()
        discover_claude.assert_not_called()
        discover_codex.assert_not_called()
        self.assertFalse(any(item["available"] for item in status))
        self.assertFalse(any(item["configured"] for item in status))

    def test_codex_command_fails_when_full_access_flag_unavailable(self) -> None:
        def no_full_access(_: list[str], *needles: str) -> bool:
            supported = {"--json", "--cd", "--output-last-message"}
            return all(needle in supported for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=no_full_access):
            with self.assertRaisesRegex(RuntimeError, "codex_full_access_unavailable"):
                codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))

    def test_codex_command_uses_danger_full_access_when_bypass_flag_unavailable(self) -> None:
        def sandbox_only(_: list[str], *needles: str) -> bool:
            supported = {"--json", "--sandbox", "--cd", "--output-last-message"}
            return all(needle in supported for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=sandbox_only):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "danger-full-access")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_codex_command_skips_git_repo_trust_check_when_supported(self) -> None:
        with patch("ai_remote_runner.providers._help_has", return_value=True):
            command = codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))
        self.assertIn("--skip-git-repo-check", command)
        self.assertLess(command.index("--skip-git-repo-check"), command.index("--cd"))

    def test_codex_command_requires_json_events(self) -> None:
        def without_json(_: list[str], *needles: str) -> bool:
            supported = {"--dangerously-bypass-approvals-and-sandbox", "--cd", "--output-last-message"}
            return all(needle in supported for needle in needles)

        with patch("ai_remote_runner.providers._help_has", side_effect=without_json):
            with self.assertRaisesRegex(RuntimeError, "codex_json_unavailable"):
                codex_command("hello", Path("/tmp/work"), Path("/tmp/out.txt"))

    def test_codex_jsonl_events_emit_realtime_status(self) -> None:
        events: list[dict[str, object]] = []
        output = "\n".join(
            [
                '{"type":"turn.started"}',
                '{"type":"item.started","item":{"id":"cmd1","type":"command_execution","command":"bash -lc ls","status":"in_progress"}}',
                '{"type":"item.completed","item":{"id":"cmd1","type":"command_execution","command":"bash -lc ls","exit_code":0,"status":"completed"}}',
                '{"type":"item.completed","item":{"id":"file1","type":"file_change","path":"src/app.py","status":"completed"}}',
                '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5,"reasoning_output_tokens":2}}',
            ]
        )

        _emit_codex_jsonl_events(output, "run-1", events.append)

        phases = [event["phase"] for event in events]
        messages = [str(event.get("public_message_zh") or "") for event in events]
        self.assertIn("thinking", phases)
        self.assertIn("running_command", phases)
        self.assertIn("writing_files", phases)
        self.assertTrue(any("bash -lc ls" in message for message in messages))
        self.assertTrue(any("exit=0" in message for message in messages))
        self.assertTrue(any("src/app.py" in message for message in messages))

    def test_codex_jsonl_events_support_tool_patch_and_redaction_variants(self) -> None:
        events: list[dict[str, object]] = []
        output = "\n".join(
            [
                '{"type":"item.started","item":{"id":"cmd1","type":"exec_command","arguments":{"command":"curl https://api.example -H token=sk-secretsecretsecretsecretsecret"},"status":"in_progress"}}',
                '{"type":"item.started","item":{"id":"patch1","type":"patch","files":["src/app.py","tests/test_app.py"],"status":"in_progress"}}',
                '{"type":"item.started","item":{"id":"tool1","type":"function_call","name":"web.run","status":"in_progress"}}',
                '{"type":"item.started","item":{"id":"search1","type":"web_search_call","status":"in_progress"}}',
            ]
        )

        _emit_codex_jsonl_events(output, "run-1", events.append)

        phases = [event["phase"] for event in events]
        messages = "\n".join(str(event.get("public_message_zh") or "") for event in events)
        self.assertIn("running_command", phases)
        self.assertIn("writing_files", phases)
        self.assertIn("calling_model", phases)
        self.assertIn("src/app.py", messages)
        self.assertIn("web.run", messages)
        self.assertIn("<redacted>", messages)
        self.assertNotIn("sk-secretsecretsecretsecretsecret", messages)

    def test_codex_jsonl_thread_started_is_visible(self) -> None:
        events: list[dict[str, object]] = []
        _emit_codex_jsonl_events('{"type":"thread.started","thread_id":"thread-1"}\n', "run-1", events.append)

        self.assertEqual(events[0]["phase"], "queued")
        self.assertIn("thread-1", str(events[0]["public_message_zh"]))

    def test_invoke_codex_returns_stderr_when_last_message_missing(self) -> None:
        import subprocess
        import tempfile

        completed = subprocess.CompletedProcess(["codex"], 1, stdout="", stderr="not inside a trusted directory")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed),
            ):
                result = invoke_codex("noop", Path(tmp), ledger, timeout_seconds=1, reserved_usd=0.01)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.output_text, "not inside a trusted directory")

    def test_invoke_codex_sends_prompt_on_stdin(self) -> None:
        import subprocess
        import tempfile

        completed = subprocess.CompletedProcess(["codex"], 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(command[command.index("--output-last-message") + 1]).write_text("ok", encoding="utf-8")
                self.assertEqual(command[-1], "-")
                self.assertNotIn("do work", command)
                self.assertEqual(kwargs["input"], "# Global Instructions\nBe careful\n\n# User Task\ndo work")
                return completed

            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", side_effect=run),
            ):
                result = invoke_codex(
                    "do work",
                    Path(tmp),
                    BudgetLedger(Path(tmp) / "ledger.json"),
                    instruction_prompt="# Global Instructions\nBe careful",
                    timeout_seconds=1,
                    reserved_usd=0.01,
                )
        self.assertEqual(result.status, "completed")

    def test_invoke_codex_falls_back_to_jsonl_agent_message(self) -> None:
        import subprocess
        import tempfile

        stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"jsonl final"}}\n'
        completed = subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed),
            ):
                result = invoke_codex("noop", Path(tmp), BudgetLedger(Path(tmp) / "ledger.json"), timeout_seconds=1, reserved_usd=0.01)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output_text, "jsonl final")

    def test_invoke_codex_timeout_releases_run(self) -> None:
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("ai_remote_runner.providers._help_has", return_value=True),
                patch("ai_remote_runner.providers.subprocess.run", side_effect=subprocess.TimeoutExpired(["codex"], 1)),
            ):
                result = invoke_codex("noop", Path(tmp), BudgetLedger(Path(tmp) / "ledger.json"), timeout_seconds=1, reserved_usd=0.01)
            self.assertIn(result.status, {"completed", "failed", "timeout"})

    def test_claude_permission_scope_uses_edit_template_and_actual_cost(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.02}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch.dict("os.environ", {"CLAUDE_MODEL": ""}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1, permission_scope="edit")
            self.assertEqual(result.status, "completed")
            self.assertIn("Read,Grep,Glob,Edit,Write", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--permission-mode") + 1], "bypassPermissions")
            self.assertNotIn("--model", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["input"], "prompt")
            self.assertNotIn("prompt", run.call_args.args[0])
            self.assertAlmostEqual(ledger.load()["daily_used_usd_estimate"], 0.02)

    def test_vscode_adapter_uses_vscode_claude_controls_and_identity(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.02}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch.dict(
                    "os.environ",
                    {
                        "CLAUDE_MODEL": "claude-model",
                        "CLAUDE_MAX_TURNS": "2",
                        "VSCODE_CLAUDE_MODEL": "vscode-model",
                        "VSCODE_CLAUDE_MAX_TURNS": "7",
                    },
                    clear=False,
                ),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                result = invoke_vscode("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.0, permission_scope="full")
            command = run.call_args.args[0]
            self.assertEqual(result.provider, "vscode")
            self.assertEqual(ledger.load()["runs"][result.run_id]["provider"], "vscode")
            self.assertEqual(command[command.index("--model") + 1], "vscode-model")
            self.assertEqual(command[command.index("--max-turns") + 1], "7")
            self.assertNotIn("claude-model", command)

    def test_claude_full_permission_scope_uses_bypass_template(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.02}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1, permission_scope="full")
            command = run.call_args.args[0]
            self.assertEqual(result.status, "completed")
            self.assertIn("--permission-mode", command)
            self.assertEqual(command[command.index("--permission-mode") + 1], "acceptEdits")
            self.assertNotIn("--dangerously-skip-permissions", command)
            self.assertIn("--add-dir", command)
            self.assertEqual(command[command.index("--add-dir") + 1], "/")
            self.assertEqual(command[command.index("--tools") + 1], "Bash,Read,Write,Edit,Grep,Glob")
            self.assertIn("--allowedTools", command)
            self.assertEqual(command[command.index("--allowedTools") + 1], "Bash(*)")
            self.assertNotIn("--no-session-persistence", command)

    def test_claude_root_full_access_template_avoids_root_rejected_flag(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1, permission_scope="full")
            command = run.call_args.args[0]
            self.assertIn("acceptEdits", command)
            self.assertNotIn("bypassPermissions", command)
            self.assertIn("--add-dir", command)
            self.assertIn("/", command)
            self.assertIn("--tools", command)
            self.assertIn("Bash,Read,Write,Edit,Grep,Glob", command)
            self.assertNotIn("--dangerously-skip-permissions", command)

    def test_invoke_claude_uses_model_only_when_explicitly_configured(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict("os.environ", {"CLAUDE_MODEL": "sonnet"}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1)
            command = run.call_args.args[0]
            self.assertIn("--model", command)
            self.assertEqual(command[command.index("--model") + 1], "sonnet")

    def test_invoke_claude_omits_max_turns_by_default(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.0)
            command = run.call_args.args[0]
            self.assertNotIn("--max-turns", command)

    def test_invoke_claude_uses_explicit_positive_max_turns(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict("os.environ", {"CLAUDE_MAX_TURNS": "40"}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run,
            ):
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.0)
            command = run.call_args.args[0]
            self.assertIn("--max-turns", command)
            self.assertEqual(command[command.index("--max-turns") + 1], "40")

    def test_invoke_claude_chat_template_disables_all_tools_when_requested(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed) as run:
                invoke_claude("prompt", Path(tmp), "instructions", BudgetLedger(Path(tmp) / "ledger.json"), reserved_usd=0.1, permission_scope="chat")
            command = run.call_args.args[0]
            self.assertIn("--tools", command)
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertNotIn("--disallowedTools", command)

    def test_invoke_claude_treats_empty_success_as_error(self) -> None:
        import tempfile
        import subprocess
        import json

        completed = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": ""}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=completed):
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "empty_output")
            self.assertIn("空内容", result.output_text)
            self.assertEqual(ledger.load()["runs"][result.run_id]["status"], "empty_output")

    def test_invoke_claude_retries_empty_chat_output_once(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        ok = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "你好，我在。", "total_cost_usd": 0.02}), stderr="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, ok]) as run:
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1, emit=events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_text, "你好，我在。")
            self.assertEqual(run.call_count, 2)
            self.assertIn("上一轮 Claude Code 返回了空字符串", run.call_args.kwargs["input"])
            self.assertIn("收到，我在。", run.call_args.kwargs["input"])
            retry_command = run.call_args.args[0]
            self.assertEqual(retry_command[retry_command.index("--max-budget-usd") + 1], "0.09")
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.03)
            self.assertTrue(any(event.get("phase") == "warning" and "重试" in event.get("public_message_zh", "") for event in events))

    def test_invoke_claude_retries_transient_api_error(self) -> None:
        import tempfile
        import subprocess
        import json

        malformed = subprocess.CompletedProcess(
            ["claude"],
            1,
            stdout=json.dumps({"type": "result", "is_error": True, "total_cost_usd": 0.5}),
            stderr="API Error: API returned an empty or malformed response (HTTP 200) — check for a proxy or gateway intercepting the request",
        )
        ok = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "OK", "total_cost_usd": 0.03}), stderr="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch.dict("os.environ", {"CLAUDE_API_RETRY_ATTEMPTS": "2", "CLAUDE_API_RETRY_SLEEP_SECONDS": "0"}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", side_effect=[malformed, ok]) as run,
            ):
                result = invoke_claude("请继续处理任务", Path(tmp), "instructions", ledger, reserved_usd=0.0, emit=events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_text, "OK")
            self.assertEqual(run.call_count, 2)
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.53)
            self.assertTrue(any("网关/API" in event.get("public_message_zh", "") for event in events))

    def test_invoke_claude_does_not_retry_terminal_json_error(self) -> None:
        import tempfile
        import subprocess
        import json

        max_turns = subprocess.CompletedProcess(
            ["claude"],
            1,
            stdout=json.dumps({"type": "result", "subtype": "error_max_turns", "total_cost_usd": 2.0, "errors": ["Reached maximum number of turns (12)"]}),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with (
                patch.dict("os.environ", {"CLAUDE_API_RETRY_ATTEMPTS": "2", "CLAUDE_API_RETRY_SLEEP_SECONDS": "0"}, clear=False),
                patch("ai_remote_runner.providers.subprocess.run", return_value=max_turns) as run,
            ):
                result = invoke_claude("prompt", Path(tmp), "instructions", ledger, reserved_usd=0.0)
            self.assertEqual(result.status, "failed")
            self.assertEqual(run.call_count, 1)
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 2.0)

    def test_invoke_claude_omits_native_budget_when_unlimited(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": ""}), stderr="")
        ok = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "ok", "total_cost_usd": 0.03}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, ok]) as run:
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.0)
            self.assertEqual(result.status, "completed")
            self.assertEqual(run.call_count, 2)
            self.assertNotIn("--max-budget-usd", run.call_args_list[0].args[0])
            self.assertNotIn("--max-budget-usd", run.call_args_list[1].args[0])
            self.assertEqual(ledger.load()["runs"][result.run_id]["reserved_usd"], 0.0)
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.03)

    def test_invoke_claude_short_empty_chat_uses_safe_fallback(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=empty) as run:
                result = invoke_claude("你好", Path(tmp), "instructions", ledger, reserved_usd=0.1, emit=events.append)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_text, "收到，我在。")
            self.assertEqual(run.call_count, 1)
            self.assertAlmostEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.01)
            self.assertTrue(any("安全回复" in event.get("public_message_zh", "") for event in events))

    def test_invoke_claude_does_not_retry_empty_output_without_cost_visibility(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": ""}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", return_value=empty) as run:
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "empty_output")
            self.assertEqual(run.call_count, 1)

    def test_invoke_claude_records_reserved_cost_when_retry_cost_missing(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        ok_without_cost = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "收到，我在。"}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, ok_without_cost]):
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "completed")
            self.assertEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.1)

    def test_invoke_claude_records_reserved_cost_when_retry_times_out(self) -> None:
        import tempfile
        import subprocess
        import json

        empty = subprocess.CompletedProcess(["claude"], 0, stdout=json.dumps({"result": "", "total_cost_usd": 0.01}), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BudgetLedger(Path(tmp) / "ledger.json")
            with patch("ai_remote_runner.providers.subprocess.run", side_effect=[empty, subprocess.TimeoutExpired(["claude"], 1)]):
                result = invoke_claude("请介绍一下你自己", Path(tmp), "instructions", ledger, reserved_usd=0.1)
            self.assertEqual(result.status, "timeout")
            self.assertEqual(ledger.load()["runs"][result.run_id]["actual_usd"], 0.1)

    def test_provider_probe_timeout_marks_unavailable(self) -> None:
        import subprocess

        with (
            patch("ai_remote_runner.providers.shutil.which", return_value="/bin/tool"),
            patch("ai_remote_runner.providers.subprocess.run", side_effect=subprocess.TimeoutExpired(["tool"], 1)),
        ):
            from ai_remote_runner.providers import _version

            self.assertEqual(_version("tool")["error"], "probe_timeout")


if __name__ == "__main__":
    unittest.main()
