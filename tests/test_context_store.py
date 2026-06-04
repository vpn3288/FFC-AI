from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_remote_runner.context_store import ContextStore


class ContextStoreTests(unittest.TestCase):
    def test_context_accumulates_across_exchanges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            first = store.add_exchange("c1", "claude-code", "hello")
            second = store.add_exchange("c1", "claude-code", "world")
            self.assertGreater(second["context_used_tokens"], first["context_used_tokens"])

    def test_compact_creates_summary_and_new_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            store.add_exchange("c1", "claude-code", "prompt", "answer")
            result = store.compact("c1", "claude-code")
            self.assertNotEqual(result["old_conversation_id"], result["new_conversation_id"])
            summary = Path(result["summary_artifact"])
            self.assertTrue(summary.exists())
            self.assertIn("prompt", summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
