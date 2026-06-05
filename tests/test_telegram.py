from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.executor import RunnerRuntime
from ai_remote_runner.providers import ProviderResult
from ai_remote_runner.telegram import TelegramBot, TelegramClient, TelegramConfig


class FakeTelegramClient(TelegramClient):
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class TelegramBotTests(unittest.TestCase):
    def test_default_reserved_budget_is_chat_sized(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOWED_CHAT_IDS": "123"}, clear=True):
            config = TelegramConfig.from_env()
        self.assertEqual(config.reserved_usd, 0.05)

    def test_unpaired_chat_gets_pairing_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            handled = bot.handle_update({"message": {"chat": {"id": 456}, "text": "/ai 状态", "message_id": 1}})

            self.assertTrue(handled)
            self.assertEqual(client.sent[0][0], "456")
            self.assertIn("chat_id: 456", client.sent[0][1])

    def test_status_command_replies_to_allowed_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")

            bot.handle_update({"message": {"chat": {"id": 123}, "from": {"id": 1, "username": "alice"}, "text": "/ai 状态", "message_id": 7}})

            self.assertEqual(client.sent[0][0], "123")
            self.assertIn("状态已生成", client.sent[0][1])

    def test_plain_text_runs_as_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
            client = FakeTelegramClient(config)
            runtime = RunnerRuntime(Path(tmp) / "state", Path(tmp) / "workspaces")
            bot = TelegramBot(config, client, runtime, Path(tmp) / "state")
            fake = ProviderResult("run", "claude-code", "completed", "telegram ok", None, 0)

            def invoke(*args, **kwargs):
                kwargs["emit"]({"run_id": "run", "provider": "claude-code", "phase": "calling_model"})
                return fake

            with patch("ai_remote_runner.executor.invoke_claude", side_effect=invoke):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "请回复 telegram ok", "message_id": 8}})

            sent_text = "\n".join(text for _, text in client.sent)
            self.assertIn("已收到任务", sent_text)
            self.assertIn("正在调用 claude-code", sent_text)
            self.assertIn("telegram ok", sent_text)

    def test_long_task_sends_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
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

            sent_text = "\n".join(text for _, text in client.sent)
            self.assertIn("仍在运行", sent_text)
            self.assertIn("slow ok", sent_text)

    def test_confirmed_task_uses_status_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = TelegramConfig(token="token", allowed_chat_ids={"123"})
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

            sent_text = "\n".join(text for _, text in client.sent)
            self.assertIn("已收到任务", sent_text)
            self.assertIn("正在调用 claude-code", sent_text)
            self.assertIn("仍在运行", sent_text)
            self.assertIn("confirmed ok", sent_text)


if __name__ == "__main__":
    unittest.main()
