from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.executor import RunnerRuntime
from ai_remote_runner.providers import ProviderResult
from ai_remote_runner.telegram import TelegramBot, TelegramClient, TelegramConfig, load_config_env


class FakeTelegramClient(TelegramClient):
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.sent: list[tuple[str, str]] = []
        self.actions: list[tuple[str, str]] = []
        self.edits: list[tuple[str, int, str]] = []
        self.deleted_webhook = False
        self.next_message_id = 100

    def send_message(self, chat_id: str, text: str) -> dict[str, object]:
        self.sent.append((chat_id, text))
        self.next_message_id += 1
        return {"message_id": self.next_message_id}

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        self.actions.append((chat_id, action))

    def get_me(self) -> dict[str, object]:
        return {"id": 1, "is_bot": True, "username": "ffc_test_bot"}

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        self.deleted_webhook = True
        return True


class FailingHeartbeatClient(FakeTelegramClient):
    def send_message(self, chat_id: str, text: str) -> dict[str, object]:
        if "仍在运行" in text:
            raise RuntimeError("simulated send failure")
        return super().send_message(chat_id, text)

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
    def test_default_reserved_budget_is_chat_sized(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_CHAT_IDS": "123"}, clear=True):
            config = TelegramConfig.from_env()
        self.assertEqual(config.reserved_usd, 0.20)

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

            with patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke):
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
            self.assertIn("没有配置 Claude Code 或 Codex", sent_text)
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

            self.assertIn("没有配置 Claude Code 或 Codex", "\n".join(text for _, text in client.sent))
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
                patch.dict("os.environ", {"TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "慢任务测试", "message_id": 9}})
                self.assertTrue(wait_for_text(client, "slow ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("仍在运行", visible_text)
            self.assertIn("模型思考", visible_text)
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
                patch.dict("os.environ", {"TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
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
                patch.dict("os.environ", {"TELEGRAM_STATUS_INTERVAL_SECONDS": "0.01"}, clear=False),
                patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke),
            ):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "/ai 确认 abc", "message_id": 10}})
                self.assertTrue(wait_for_text(client, "confirmed ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("AI 正在运行", visible_text)
            self.assertIn("模型正在思考", visible_text)
            self.assertIn("仍在运行", visible_text)
            self.assertIn("confirmed ok", visible_text)

    def test_startup_check_validates_token_and_clears_webhook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.startup_check()

            self.assertTrue(client.deleted_webhook)

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
                return fake

            with patch("ai_remote_runner.executor.invoke_codex", side_effect=invoke):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "请检查并修改", "message_id": 14}})
                self.assertTrue(wait_for_text(client, "codex ok"))

            visible_text = "\n".join([text for _, text in client.sent] + [text for _, _, text in client.edits])
            self.assertIn("codex 正在运行命令", visible_text)
            self.assertIn("bash -lc ls", visible_text)
            self.assertIn("codex 正在修改文件", visible_text)
            self.assertIn("src/app.py", visible_text)


if __name__ == "__main__":
    unittest.main()
