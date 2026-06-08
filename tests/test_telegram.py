from __future__ import annotations

import json
import tempfile
import time
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.executor import RunnerRuntime
from ai_remote_runner.providers import ProviderResult
from ai_remote_runner.telegram import TelegramBot, TelegramClient, TelegramConfig, load_config_env


class FakeTelegramClient(TelegramClient):
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.sent: list[tuple[str, str]] = []
        self.sent_markup: list[dict[str, object] | None] = []
        self.actions: list[tuple[str, str]] = []
        self.edits: list[tuple[str, int, str]] = []
        self.drafts: list[tuple[str, int, str]] = []
        self.callback_answers: list[tuple[str, str]] = []
        self.commands: list[dict[str, str]] = []
        self.deleted_webhook = False
        self.next_message_id = 100

    def send_message(self, chat_id: str, text: str, reply_markup: dict[str, object] | None = None) -> dict[str, object]:
        self.sent.append((chat_id, text))
        self.sent_markup.append(reply_markup)
        self.next_message_id += 1
        return {"message_id": self.next_message_id}

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        self.actions.append((chat_id, action))

    def send_message_draft(self, chat_id: str, draft_id: int, text: str) -> bool:
        self.drafts.append((chat_id, draft_id, text))
        return True

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        self.callback_answers.append((callback_query_id, text))
        return True

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        self.commands = commands
        return True

    def get_me(self) -> dict[str, object]:
        return {"id": 1, "is_bot": True, "username": "ffc_test_bot"}

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        self.deleted_webhook = True
        return True


class FailingHeartbeatClient(FakeTelegramClient):
    def send_message(self, chat_id: str, text: str, reply_markup: dict[str, object] | None = None) -> dict[str, object]:
        if "仍在运行" in text:
            raise RuntimeError("simulated send failure")
        return super().send_message(chat_id, text, reply_markup=reply_markup)

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        if "仍在运行" in text:
            raise RuntimeError("simulated edit failure")
        return super().edit_message_text(chat_id, message_id, text)


def wait_for_text(client: FakeTelegramClient, text: str, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        combined = "\n".join([item[1] for item in client.sent] + [item[2] for item in client.edits])
        if text in combined:
            return True
        time.sleep(0.01)
    return False


class TelegramBotTests(unittest.TestCase):
    def test_default_reserved_budget_is_unlimited(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_CHAT_IDS": "123"}, clear=True):
            config = TelegramConfig.from_env()
        self.assertEqual(config.reserved_usd, 0.0)

    def test_unlimited_reserved_budget_alias_is_supported(self) -> None:
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_CHAT_IDS": "123", "TELEGRAM_RESERVED_USD": "无限"},
            clear=True,
        ):
            config = TelegramConfig.from_env()
        self.assertEqual(config.reserved_usd, 0.0)

    def test_unpaired_chat_gets_pairing_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            handled = bot.handle_update({"message": {"chat": {"id": 456}, "text": "/ai 状态", "message_id": 1}})

            self.assertTrue(handled)
            self.assertEqual(client.sent[0][0], "456")
            self.assertIn("chat_id: 456", client.sent[0][1])

    def test_group_mode_ignores_unaddressed_group_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, group_mode="mention")
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            bot.bot_username = "ffc_test_bot"
            bot.bot_user_id = "1"

            with patch("ai_remote_runner.executor.invoke_claude") as invoke:
                handled = bot.handle_update(
                    {
                        "message": {
                            "chat": {"id": 123, "type": "supergroup"},
                            "from": {"id": 2, "username": "alice"},
                            "text": "普通群聊消息",
                            "message_id": 6,
                        }
                    }
                )

            self.assertFalse(handled)
            self.assertEqual(client.sent, [])
            invoke.assert_not_called()

    def test_group_mode_accepts_bot_mention_as_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01, group_mode="mention")
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            bot.bot_username = "ffc_test_bot"
            bot.bot_user_id = "1"
            fake = ProviderResult("run", "codex", "completed", "mention ok", None, 0)

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "codex"}, clear=False),
                patch("ai_remote_runner.executor.invoke_codex", return_value=fake) as invoke,
            ):
                handled = bot.handle_update(
                    {
                        "message": {
                            "chat": {"id": 123, "type": "group"},
                            "from": {"id": 2, "username": "alice"},
                            "text": "@ffc_test_bot 请执行",
                            "message_id": 6,
                        }
                    }
                )
                self.assertTrue(wait_for_text(client, "mention ok"))

            self.assertTrue(handled)
            provider_prompt = invoke.call_args.args[0]
            self.assertIn("请执行", provider_prompt)
            self.assertNotIn("@ffc_test_bot", provider_prompt)

    def test_group_mentioned_shortcuts_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, group_mode="mention")
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123, "type": "group"}, "text": "/status@ffc_test_bot", "message_id": 7}})

            self.assertIn("状态已生成", client.sent[0][1])

    def test_status_command_replies_to_allowed_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123}, "from": {"id": 1, "username": "alice"}, "text": "/ai 状态", "message_id": 7}})

            self.assertEqual(client.sent[0][0], "123")
            self.assertIn("状态已生成", client.sent[0][1])

    def test_plain_text_runs_as_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "claude-code", "completed", "telegram ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "请回复 telegram ok", "message_id": 8}})
                self.assertTrue(wait_for_text(client, "telegram ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("AI 正在运行", visible_text)
            self.assertIn("模型正在思考", visible_text)
            self.assertIn("telegram ok", visible_text)
            self.assertIn(("123", "typing"), client.actions)
            self.assertTrue(client.edits)

    def test_plain_text_on_management_only_bot_does_not_start_ai_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": ""}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude") as invoke_claude,
                patch("ai_remote_runner.executor.invoke_codex") as invoke_codex,
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "你好", "message_id": 88}})

            sent_text = "\n".join(text for _, text in client.sent)
            self.assertIn("没有配置 Claude Code、VSCode 或 Codex", sent_text)
            invoke_claude.assert_not_called()
            invoke_codex.assert_not_called()

    def test_management_only_bot_still_answers_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            with patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": ""}, clear=False):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 状态", "message_id": 89}})

            sent_text = "\n".join(text for _, text in client.sent)
            self.assertIn("状态已生成", sent_text)
            self.assertIn("configured_providers", sent_text)
            self.assertIn("none", sent_text)

    def test_telegram_loads_empty_provider_config_from_state_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            (state / "config.env").write_text("AI_RUNNER_PROVIDERS=\n", encoding="utf-8")
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(state, Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, state)

            with (
                patch.dict("os.environ", {}, clear=True),
                patch("ai_remote_runner.executor.invoke_claude") as invoke_claude,
            ):
                load_config_env(state)
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "你好", "message_id": 90}})

            self.assertIn("没有配置 Claude Code、VSCode 或 Codex", "\n".join(text for _, text in client.sent))
            invoke_claude.assert_not_called()

    def test_long_task_sends_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "claude-code", "completed", "slow ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                time.sleep(0.04)
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code", "TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "慢任务测试", "message_id": 9}})
                self.assertTrue(wait_for_text(client, "slow ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("仍在运行", visible_text)
            self.assertIn("模型正在思考", visible_text)
            self.assertIn("slow ok", visible_text)
            self.assertIn(("123", "typing"), client.actions)

    def test_heartbeat_send_failure_does_not_abort_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FailingHeartbeatClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "claude-code", "completed", "still ok", None, 0)

            def invoke(*args, **kwargs):
                time.sleep(0.04)
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code", "TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                handled = bot.handle_update({"message": {"chat": {"id": 123}, "text": "慢任务测试", "message_id": 12}})
                self.assertTrue(wait_for_text(client, "still ok"))

            self.assertTrue(handled)
            self.assertTrue((Path(tmp) / "state" / "telegram-send-failures.jsonl").exists())

    def test_confirmed_task_uses_status_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            pending = bot.confirmations()
            pending["abc"] = {
                "created_at": int(time.time()),
                "parsed": {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "confirmed slow"}, "requires_confirmation": False},
                "envelope": {"request_id": "pending-1", "raw_text": "confirmed slow", "reserved_usd": 0.05},
            }
            bot.save_confirmations(pending)
            fake = ProviderResult("run", "claude-code", "completed", "confirmed ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                time.sleep(0.04)
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code", "TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 确认 abc", "message_id": 10}})
                self.assertTrue(wait_for_text(client, "confirmed ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("AI 正在运行", visible_text)
            self.assertIn("模型正在思考", visible_text)
            self.assertIn("仍在运行", visible_text)
            self.assertIn("confirmed ok", visible_text)

    def test_confirmed_task_runs_in_background_and_status_lists_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            pending = bot.confirmations()
            pending["abc"] = {
                "created_at": int(time.time()),
                "parsed": {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "confirmed background"}, "requires_confirmation": False},
                "envelope": {"request_id": "pending-bg", "raw_text": "confirmed background", "reserved_usd": 0.05},
            }
            bot.save_confirmations(pending)
            fake = ProviderResult("run", "claude-code", "completed", "background ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                time.sleep(0.2)
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code", "TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                started = time.time()
                handled = bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 确认 abc", "message_id": 15}})
                elapsed = time.time() - started
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 状态", "message_id": 16}})
                self.assertTrue(wait_for_text(client, "background ok", timeout=1.0))

            self.assertTrue(handled)
            self.assertLess(elapsed, 0.15)
            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("telegram_tasks", visible_text)
            self.assertIn("task_id", visible_text)
            self.assertIn("background ok", visible_text)

    def test_startup_check_validates_token_and_clears_webhook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.startup_check()

            self.assertTrue(client.deleted_webhook)
            self.assertIn({"command": "ai", "description": "运行 AI 或管理 runner"}, client.commands)
            self.assertIn({"command": "gptmodel", "description": "切换 GPT 模型"}, client.commands)
            self.assertIn({"command": "claudemodel", "description": "切换 Claude 模型"}, client.commands)

    def test_startup_command_sync_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, sync_commands_on_startup=False)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.startup_check()

            self.assertEqual(client.commands, [])

    def test_callback_confirmation_runs_background_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            pending = bot.confirmations()
            pending["abc"] = {
                "created_at": int(time.time()),
                "parsed": {"status": "accepted", "canonical_action": "task.run", "args": {"prompt": "callback slow"}, "requires_confirmation": False},
                "envelope": {"request_id": "callback-bg", "raw_text": "callback slow", "reserved_usd": 0.05},
            }
            bot.save_confirmations(pending)
            fake = ProviderResult("run", "claude-code", "completed", "callback ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                time.sleep(0.04)
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                handled = bot.handle_update(
                    {
                        "callback_query": {
                            "id": "cb-1",
                            "data": "confirm:abc",
                            "from": {"id": 1, "username": "alice"},
                            "message": {"chat": {"id": 123}, "message_id": 30},
                        }
                    }
                )
                self.assertTrue(wait_for_text(client, "callback ok"))

            self.assertTrue(handled)
            self.assertIn(("cb-1", "已确认，开始执行"), client.callback_answers)
            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("AI 正在运行", visible_text)
            self.assertIn("callback ok", visible_text)

    def test_needs_confirmation_response_uses_inline_keyboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 全局 替换 hello", "message_id": 31}})

            markup = next(item for item in client.sent_markup if item)
            self.assertIn("inline_keyboard", markup)
            self.assertIn("确认执行", str(markup))

    def test_codex_shortcut_runs_task_with_codex_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "codex", "completed", "codex shortcut ok", None, 0)

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "codex"}, clear=False),
                patch("ai_remote_runner.executor.invoke_codex", return_value=fake) as invoke,
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/codex 直接执行", "message_id": 32}})
                self.assertTrue(wait_for_text(client, "codex shortcut ok"))

            self.assertEqual(invoke.call_args.kwargs["run_id"] is not None, True)
            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("provider: codex", visible_text)

    def test_model_shortcut_switches_gpt_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(root / "state", root / "workspaces")
            bot = TelegramBot(config, client, runtime, root / "state")
            with patch.dict("os.environ", {"AI_TOOL_HOME": str(root / "root-home"), "CODEX_HOME": str(root / "root-home" / ".codex")}, clear=False):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/gptmodel vscode gpt", "message_id": 33}})

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("GPT 模型已更新", visible_text)
            self.assertIn("config_key: VSCODE_CLAUDE_MODEL", visible_text)
            config_env = (root / "state" / "config.env").read_text(encoding="utf-8")
            self.assertIn("VSCODE_CLAUDE_MODEL=gpt-5.5", config_env)

    def test_shell_shortcut_runs_local_exec_in_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123}, "text": "/shell printf shell-ok", "message_id": 33}})
            self.assertTrue(wait_for_text(client, "shell-ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("provider: runner", visible_text)
            self.assertIn("shell-ok", visible_text)

    def test_native_draft_progress_is_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(
                token="token",
                allowed_chat_ids={"123"},
                status_interval_seconds=0.01,
                native_draft_progress=True,
                native_draft_allow_chat_ids=frozenset({"123"}),
            )
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "claude-code", "completed", "draft ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "running_command", "public_message_zh": "运行命令：pwd"})
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "claude-code"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "draft test", "message_id": 34}})
                self.assertTrue(wait_for_text(client, "draft ok"))

            draft_ids = {draft_id for _, draft_id, _ in client.drafts}
            self.assertEqual(len(draft_ids), 1)
            self.assertTrue(next(iter(draft_ids)) > 0)
            self.assertTrue(any("pwd" in text for _, _, text in client.drafts))
            self.assertTrue(any(chat_id == "123" and text == "" for chat_id, _, text in client.drafts))

    def test_codex_bot_mention_without_prompt_selects_codex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123}, "text": "/codex@ffc_test_bot", "message_id": 35}})

            selected = json.loads((Path(tmp) / "state" / "provider-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(selected["provider"], "codex")

    def test_poll_forever_keeps_running_after_remote_disconnect(self) -> None:
        class DisconnectingPollClient(FakeTelegramClient):
            def __init__(self, config: TelegramConfig) -> None:
                super().__init__(config)
                self.poll_calls = 0

            def get_updates(self, offset: int | None, timeout_seconds: int) -> list[dict[str, object]]:
                self.poll_calls += 1
                if self.poll_calls == 1:
                    raise RemoteDisconnected("Remote end closed connection without response")
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = DisconnectingPollClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            state = Path(tmp) / "state"
            bot = TelegramBot(config, client, runtime, state)

            with patch("ai_remote_runner.telegram.time.sleep", return_value=None):
                with self.assertRaises(KeyboardInterrupt):
                    bot.poll_forever()

            self.assertEqual(client.poll_calls, 2)
            failure_log = state / "telegram-poll-failures.jsonl"
            self.assertTrue(failure_log.exists())
            self.assertIn("RemoteDisconnected", failure_log.read_text(encoding="utf-8"))

    def test_codex_realtime_events_are_visible_in_status_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"}, status_interval_seconds=0.01)
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            (Path(tmp) / "state").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "state" / "provider-selection.json").write_text('{"provider":"codex"}\n', encoding="utf-8")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "codex", "completed", "codex ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "codex", "phase": "running_command", "public_message_zh": "运行命令：bash -lc ls"})
                kwargs["emit"]({"run_id": "run", "provider": "codex", "phase": "writing_files", "public_message_zh": "正在修改文件：src/app.py"})
                kwargs["emit"]({"run_id": "run", "provider": "codex", "phase": "subagent", "public_message_zh": "子 agent 正在运行：独立审查者AI"})
                return fake

            with (
                patch.dict("os.environ", {"AI_RUNNER_PROVIDERS": "codex"}, clear=False),
                patch("ai_remote_runner.executor.invoke_codex", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "请检查并修改", "message_id": 14}})
                self.assertTrue(wait_for_text(client, "codex ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("codex 正在运行命令", visible_text)
            self.assertIn("bash -lc ls", visible_text)
            self.assertIn("codex 正在修改文件", visible_text)
            self.assertIn("src/app.py", visible_text)
            self.assertIn("codex 子 agent 状态", visible_text)
            self.assertIn("独立审查者AI", visible_text)


if __name__ == "__main__":
    unittest.main()
