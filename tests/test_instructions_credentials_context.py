from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_remote_runner.context import ContextState, estimate_tokens
from ai_remote_runner.context_store import ContextStore
from ai_remote_runner.credentials import CredentialBroker
from ai_remote_runner.instructions import InstructionStore


class StoreTests(unittest.TestCase):
    def test_instruction_append_creates_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = InstructionStore(root / "instructions" / "global.md", root / "workspaces")
            result = store.write("global", "hello")
            self.assertIn("snapshot", result)
            shown = store.show("global")
            self.assertEqual(shown["preview"], "hello")
            store.write("global", "changed")
            restored = store.rollback("global", result["snapshot"])
            self.assertEqual(restored["restored_snapshot"], result["snapshot"])
            self.assertEqual(store.show("global")["preview"], "")

    def test_credential_public_record_hides_secret_path_and_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = CredentialBroker(Path(tmp))
            record = broker.add_local_secret(
                {
                    "handle": "api://test",
                    "type": "api_token",
                    "allowed_agents": ["claude-code"],
                    "allowed_actions": ["api.call"],
                },
                "secret-value",
            )
            self.assertEqual(record["secret_material"], "never returned")
            self.assertNotIn("secret_path", record)
            self.assertNotIn("secret-value", str(record))
            secret_files = list(Path(tmp).glob("*.secret.enc"))
            self.assertEqual(len(secret_files), 1)
            self.assertNotIn(b"secret-value", secret_files[0].read_bytes())
            self.assertTrue(broker.test("api://test")["ok"])
            self.assertTrue(broker.delete("api://test")["deleted"])
            self.assertEqual(broker.list_public(), [])

    def test_credential_authorization_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = CredentialBroker(Path(tmp))
            broker.add_local_secret(
                {
                    "handle": "api://locked",
                    "type": "api_token",
                    "allowed_agents": ["claude-code"],
                    "allowed_actions": ["api.call"],
                },
                "secret-value",
            )
            with self.assertRaises(PermissionError):
                broker.authorize("api://locked", "runner", "ssh.exec")

    def test_ssh_password_uses_sshpass_environment_not_argv(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            broker = CredentialBroker(Path(tmp))
            broker.add_local_secret(
                {
                    "handle": "ssh://password",
                    "type": "ssh_password",
                    "host": "example.invalid",
                    "username": "deploy",
                    "allowed_agents": ["runner"],
                    "allowed_actions": ["ssh.exec.password"],
                },
                "secret-password",
            )
            completed = subprocess.CompletedProcess(["sshpass"], 0, stdout="", stderr="")
            with (
                patch("ai_remote_runner.credentials.shutil.which", return_value="/usr/bin/sshpass"),
                patch.object(broker, "_decrypt_file", return_value="secret-password"),
                patch("ai_remote_runner.credentials.subprocess.run", return_value=completed) as run,
            ):
                broker.ssh_exec("ssh://password", "true")
            command = run.call_args.args[0]
            env = run.call_args.kwargs["env"]
            self.assertEqual(command[0:3], ["sshpass", "-e", "ssh"])
            self.assertNotIn("secret-password", command)
            self.assertEqual(env["SSHPASS"], "secret-password")

    def test_ssh_password_rejects_generic_ssh_exec_action_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broker = CredentialBroker(Path(tmp))
            broker.add_local_secret(
                {
                    "handle": "ssh://generic-password",
                    "type": "ssh_password",
                    "host": "example.invalid",
                    "username": "deploy",
                    "allowed_agents": ["runner"],
                    "allowed_actions": ["ssh.exec"],
                },
                "secret-password",
            )
            with (
                patch("ai_remote_runner.credentials.shutil.which", return_value="/usr/bin/sshpass"),
                patch.object(broker, "_decrypt_file", return_value="secret-password"),
            ):
                with self.assertRaises(PermissionError):
                    broker.ssh_exec("ssh://generic-password", "true")

    def test_context_thresholds(self) -> None:
        used = estimate_tokens("x" * 320)
        state = ContextState("c1", "claude-code", 100, used)
        self.assertTrue(state.needs_warning)
        self.assertTrue(state.hard_stop)

    def test_legacy_context_without_provider_is_not_reused_across_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            (Path(tmp) / "default.json").write_text(
                json.dumps(
                    {
                        "conversation_id": "default",
                        "context_limit_tokens": 200000,
                        "context_used_tokens": 10,
                        "exchanges": [{"texts": ["old", "answer"]}],
                    }
                ),
                encoding="utf-8",
            )
            state = store.load("default", "codex")
            self.assertEqual(state["provider"], "codex")
            self.assertEqual(state["exchanges"], [])
            claude = store.load("default", "claude-code")
            self.assertEqual(claude["provider"], "claude-code")
            self.assertEqual(claude["exchanges"][0]["texts"], ["old", "answer"])
            self.assertTrue(store.path("default", "claude-code").exists())


if __name__ == "__main__":
    unittest.main()
