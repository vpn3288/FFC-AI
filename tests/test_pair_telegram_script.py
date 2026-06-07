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


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class PairTelegramScriptTests(unittest.TestCase):
    def make_fakebin(self, root: Path) -> tuple[Path, Path]:
        fakebin = root / "bin"
        fakebin.mkdir()
        calls = root / "calls.log"
        write_executable(
            fakebin / "sudo",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            if [ "${1:-}" = "awk" ]; then shift; exec awk "$@"; fi
            if [ "${1:-}" = "cp" ]; then shift; exec cp "$@"; fi
            if [ "${1:-}" = "chmod" ]; then shift; exec chmod "$@"; fi
            if [ "${1:-}" = "mkdir" ]; then shift; exec mkdir "$@"; fi
            if [ "${1:-}" = "tee" ]; then
              shift
              if [ "${1:-}" = "-a" ]; then shift; exec tee -a "$@"; fi
              exec tee "$@"
            fi
            exec "$@"
            """,
        )
        write_executable(
            fakebin / "systemctl",
            """
            #!/usr/bin/env bash
            printf 'systemctl %s\\n' "$*" >> "${CALLS:?}"
            exit 0
            """,
        )
        return fakebin, calls

    def test_pair_telegram_writes_config_and_starts_service_without_printing_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            token_file = root / "telegram-token"
            state.mkdir()
            (state / "install-manifest.json").write_text(json.dumps({"telegram_status": "pending_pairing"}), encoding="utf-8")
            service.write_text("[Service]\n", encoding="utf-8")
            token = "test-token:ABC_def"
            token_file.write_text(token + "\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token-file",
                    str(token_file),
                    "--chat-id",
                    "123",
                    "--reserved-usd",
                    "0.25",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn(f"TELEGRAM_BOT_TOKEN={token}\n", config)
            self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS=123\n", config)
            self.assertIn("TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP=1\n", config)
            self.assertIn("TELEGRAM_RESERVED_USD=0.25\n", config)
            self.assertIn("TELEGRAM_STATUS_INTERVAL_SECONDS=5\n", config)
            self.assertIn("TELEGRAM_STATUS_MIN_UPDATE_SECONDS=0.8\n", config)
            self.assertIn("TELEGRAM_SYNC_COMMANDS_ON_STARTUP=1\n", config)
            self.assertIn("TELEGRAM_ALLOWED_UPDATES=message,edited_message,callback_query\n", config)
            self.assertIn("TELEGRAM_NATIVE_DRAFT_PROGRESS=0\n", config)
            self.assertIn("TELEGRAM_GROUP_MODE=mention\n", config)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["telegram_status"], "paired")
            self.assertTrue(manifest["telegram_paired"])
            self.assertEqual(manifest["telegram_allowed_chat_ids"], ["123"])
            self.assertTrue(manifest["telegram_webhook_cleared_for_polling"])
            self.assertEqual(manifest["telegram_allowed_updates"], ["message", "edited_message", "callback_query"])
            self.assertFalse(manifest["telegram_native_draft_progress"])
            self.assertEqual(manifest["telegram_group_mode"], "mention")
            self.assertEqual(manifest["telegram_reserved_usd_input"], "0.25")
            self.assertEqual(manifest["telegram_reserved_usd"], "0.25")
            self.assertIn("systemctl enable --now ai-telegram-bot.service", calls.read_text(encoding="utf-8"))

    def test_pair_telegram_accepts_unlimited_reserved_usd_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            state.mkdir()
            (state / "install-manifest.json").write_text(json.dumps({"telegram_status": "pending_pairing"}), encoding="utf-8")
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "new-token:ABC_def",
                    "--chat-id",
                    "123",
                    "--reserved-usd",
                    "无限",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_RESERVED_USD=0\n", config)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["telegram_reserved_usd_input"], "无限")
            self.assertEqual(manifest["telegram_reserved_usd"], "0")

    def test_pair_telegram_rerun_rewrites_status_stream_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            state.mkdir()
            (state / "config.env").write_text(
                "\n".join(
                    [
                        "OTHER_SETTING=keep",
                        "TELEGRAM_BOT_TOKEN=old-token",
                        "TELEGRAM_ALLOWED_CHAT_IDS=999",
                        "TELEGRAM_STATUS_INTERVAL_SECONDS=99",
                        "TELEGRAM_STATUS_MIN_UPDATE_SECONDS=99",
                        "TELEGRAM_SYNC_COMMANDS_ON_STARTUP=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "new-token:ABC_def",
                    "--chat-id",
                    "123",
                    "--status-interval",
                    "2",
                    "--status-min-update",
                    "0.4",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config_lines = (state / "config.env").read_text(encoding="utf-8").splitlines()
            self.assertIn("OTHER_SETTING=keep", config_lines)
            self.assertEqual(config_lines.count("TELEGRAM_STATUS_INTERVAL_SECONDS=2"), 1)
            self.assertEqual(config_lines.count("TELEGRAM_STATUS_MIN_UPDATE_SECONDS=0.4"), 1)
            self.assertNotIn("TELEGRAM_STATUS_INTERVAL_SECONDS=99", config_lines)
            self.assertNotIn("TELEGRAM_STATUS_MIN_UPDATE_SECONDS=99", config_lines)

    def test_discover_chat_id_mode_does_not_require_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            token_file = root / "telegram-token"
            state.mkdir()
            service.write_text("[Service]\n", encoding="utf-8")
            token_file.write_text("test-token:ABC_def\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token-file",
                    str(token_file),
                    "--discover-chat-id",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("discovery mode enabled", result.stdout)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS=\n", config)
            self.assertIn("TELEGRAM_CLEAR_WEBHOOK_ON_STARTUP=1\n", config)
            self.assertIn("TELEGRAM_SYNC_COMMANDS_ON_STARTUP=1\n", config)
            self.assertNotIn("TELEGRAM_ALLOW_ALL_CHATS", config)

    def test_discover_chat_id_mode_pairs_when_update_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            api_calls = root / "telegram-api-calls.log"
            state.mkdir()
            (state / "install-manifest.json").write_text(json.dumps({"telegram_status": "pending_pairing"}), encoding="utf-8")
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            fakebin.joinpath("python3").write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    script="$(cat)"
                    if printf '%s' "$script" | grep -q 'getUpdates'; then
                      printf 'getUpdates %s timeout=%s\\n' "$3" "$4" >> "{api_calls}"
                      printf '789\\n'
                      exit 0
                    fi
                    if printf '%s' "$script" | grep -q 'sendMessage'; then
                      printf 'sendMessage %s %s\\n' "$3" "$4" >> "{api_calls}"
                      if printf '%s' "$script" | grep -q 'editMessageText'; then
                        printf 'editMessageText %s %s\\n' "$3" "$4" >> "{api_calls}"
                      fi
                      exit 0
                    fi
                    if printf '%s' "$script" | grep -q 'setMyCommands'; then
                      printf 'setMyCommands %s\\n' "$3" >> "{api_calls}"
                      exit 0
                    fi
                    if printf '%s' "$script" | grep -q 'deleteWebhook'; then
                      printf 'deleteWebhook %s\\n' "$3" >> "{api_calls}"
                      exit 0
                    fi
                    if printf '%s' "$script" | grep -q 'getMe'; then
                      printf 'getMe %s\\n' "$3" >> "{api_calls}"
                      printf '{{"id":1,"username":"ffc_test_bot"}}\\n'
                      exit 0
                    fi
                    shift
                    printf '%s' "$script" | /usr/bin/python3 - "$@"
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (fakebin / "python3").chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                    "PAIR_TELEGRAM_DISCOVER_TIMEOUT_SECONDS": "1",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "test-token:ABC_def",
                    "--discover-chat-id",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("discovered Telegram chat_id(s): 789", result.stdout)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS=789\n", config)
            manifest = json.loads((state / "install-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["telegram_status"], "paired")
            self.assertTrue(manifest["telegram_paired"])
            self.assertEqual(manifest["telegram_allowed_chat_ids"], ["789"])
            api_log = api_calls.read_text(encoding="utf-8")
            self.assertIn("getUpdates test-token:ABC_def timeout=1", api_log)
            self.assertIn("sendMessage test-token:ABC_def 789", api_log)
            self.assertIn("editMessageText test-token:ABC_def 789", api_log)
            self.assertIn("setMyCommands test-token:ABC_def", api_log)

    def test_interactive_bot_token_and_telegram_id_pair_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            state.mkdir()
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--telegram-id",
                    "123",
                ],
                input="test-token:ABC_def\n",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("test-token:ABC_def", result.stdout + result.stderr)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_BOT_TOKEN=test-token:ABC_def\n", config)
            self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS=123\n", config)
            self.assertIn("systemctl restart ai-telegram-bot.service", calls.read_text(encoding="utf-8"))

    def test_direct_bot_token_remains_available_for_automation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            state.mkdir()
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "test-token:ABC_def",
                    "--chat-id",
                    "123,-456",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            config = (state / "config.env").read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_ALLOWED_CHAT_IDS=123,-456\n", config)
            self.assertIn("systemctl restart ai-telegram-bot.service", calls.read_text(encoding="utf-8"))

    def test_pair_telegram_runs_core_ready_validation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            validate = root / "validate-core-ready.sh"
            state.mkdir()
            service.write_text("[Service]\n", encoding="utf-8")
            validate.write_text("#!/usr/bin/env bash\nprintf 'validate %s\\n' \"$AI_REMOTE_STATE\" >> \"${VALIDATE_CALLS:?}\"\n", encoding="utf-8")
            validate.chmod(validate.stat().st_mode | stat.S_IXUSR)
            fakebin, calls = self.make_fakebin(root)
            validate_calls = root / "validate-calls.log"
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VERIFY_API": "false",
                    "VALIDATE_CORE_READY_SCRIPT": str(validate),
                    "VALIDATE_CALLS": str(validate_calls),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "test-token:ABC_def",
                    "--chat-id",
                    "123",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("running core readiness validation", result.stdout)
            self.assertIn(f"validate {state}", validate_calls.read_text(encoding="utf-8"))

    def test_pair_telegram_verifies_bot_token_and_chat_id_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            service = root / "ai-telegram-bot.service"
            fakebin, calls = self.make_fakebin(root)
            api_calls = root / "telegram-api-calls.log"
            state.mkdir()
            service.write_text("[Service]\n", encoding="utf-8")
            fakebin.joinpath("python3").write_text(
                textwrap.dedent(
                    f"""
                    #!/usr/bin/env bash
                    script="$(cat)"
                    if printf '%s' "$script" | grep -q 'urlopen'; then
                      if printf '%s' "$script" | grep -q 'sendMessage'; then
                        printf 'sendMessage %s %s\\n' "$3" "${{4:-}}" >> "{api_calls}"
                        if printf '%s' "$script" | grep -q 'editMessageText'; then
                          printf 'editMessageText %s %s\\n' "$3" "${{4:-}}" >> "{api_calls}"
                        fi
                      elif printf '%s' "$script" | grep -q 'setMyCommands'; then
                        printf 'setMyCommands %s\\n' "$3" >> "{api_calls}"
                      else
                        printf 'getMe %s\\n' "$3" >> "{api_calls}"
                        if printf '%s' "$script" | grep -q 'deleteWebhook'; then
                          printf 'deleteWebhook %s\\n' "$3" >> "{api_calls}"
                        fi
                      fi
                      exit 0
                    fi
                    exec /usr/bin/python3 - "$@"
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (fakebin / "python3").chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(service),
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token",
                    "test-token:ABC_def",
                    "--chat-id",
                    "123",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("verifying Telegram bot token with getMe", result.stdout)
            self.assertIn("clearing Telegram webhook for long polling", result.stdout)
            self.assertIn("syncing Telegram command menu", result.stdout)
            self.assertIn("sending and editing Telegram pairing test message", result.stdout)
            api_log = api_calls.read_text(encoding="utf-8")
            self.assertIn("getMe test-token:ABC_def", api_log)
            self.assertIn("deleteWebhook test-token:ABC_def", api_log)
            self.assertIn("setMyCommands test-token:ABC_def", api_log)
            self.assertIn("sendMessage test-token:ABC_def 123", api_log)
            self.assertIn("editMessageText test-token:ABC_def 123", api_log)

    def test_rejects_invalid_chat_id_before_writing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            token_file = root / "telegram-token"
            token_file.write_text("test-token:ABC_def\n", encoding="utf-8")
            fakebin, calls = self.make_fakebin(root)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}:{env['PATH']}",
                    "AI_REMOTE_STATE": str(state),
                    "CALLS": str(calls),
                    "TELEGRAM_SYSTEMD_UNIT": str(root / "missing.service"),
                    "PAIR_TELEGRAM_VALIDATE_CORE_READY": "false",
                }
            )

            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "pair-telegram.sh"),
                    "--bot-token-file",
                    str(token_file),
                    "--telegram-id",
                    "123\nTELEGRAM_RESERVED_USD=999",
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("must be a comma-separated list", result.stderr)
            self.assertFalse((state / "config.env").exists())


if __name__ == "__main__":
    unittest.main()
