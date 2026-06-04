from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_remote_runner.context import ContextState, estimate_tokens
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

    def test_context_thresholds(self) -> None:
        used = estimate_tokens("x" * 320)
        state = ContextState("c1", "claude-code", 100, used)
        self.assertTrue(state.needs_warning)
        self.assertTrue(state.hard_stop)


if __name__ == "__main__":
    unittest.main()
