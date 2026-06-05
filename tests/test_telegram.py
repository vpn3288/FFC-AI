from __future__ import annotations

import tempfile
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

            with patch("ai_remote_runner.executor.invoke_claude", return_value=fake):
                bot.handle_update({"message": {"chat": {"id": 123}, "text": "请回复 telegram ok", "message_id": 8}})

            self.assertEqual(client.sent[0], ("123", "telegram ok"))


if __name__ == "__main__":
    unittest.main()
