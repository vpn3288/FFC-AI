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

    def test_transcript_includes_recent_user_and_assistant_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            store.add_exchange("c1", "claude-code", "instructions", "remember alpha", "alpha saved")
            transcript = store.transcript("c1", "claude-code")
            self.assertIn("remember alpha", transcript)
            self.assertIn("alpha saved", transcript)

    def test_context_isolated_by_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            store.add_exchange("c1", "claude-code", "instructions", "claude memory", "claude answer")
            store.add_exchange("c1", "codex", "instructions", "codex memory", "codex answer")

            self.assertIn("claude memory", store.transcript("c1", "claude-code"))
            self.assertNotIn("codex memory", store.transcript("c1", "claude-code"))
            self.assertIn("codex memory", store.transcript("c1", "codex"))
            self.assertNotEqual(store.path("c1", "claude-code"), store.path("c1", "codex"))

    def test_compact_creates_summary_and_new_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            store.add_exchange("c1", "claude-code", "prompt", "answer")
            result = store.compact("c1", "claude-code")
            self.assertNotEqual(result["old_conversation_id"], result["new_conversation_id"])
            summary = Path(result["summary_artifact"])
            self.assertTrue(summary.exists())
            self.assertIn("prompt", summary.read_text(encoding="utf-8"))

    def test_compact_summary_paths_are_provider_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ContextStore(Path(tmp))
            store.add_exchange("c1", "claude-code", "claude prompt", "claude answer")
            store.add_exchange("c1", "codex", "codex prompt", "codex answer")
            claude = store.compact("c1", "claude-code")
            codex = store.compact("c1", "codex")

            self.assertNotEqual(claude["summary_artifact"], codex["summary_artifact"])
            self.assertIn("claude-code", Path(claude["summary_artifact"]).name)
            self.assertIn("codex", Path(codex["summary_artifact"]).name)


if __name__ == "__main__":
    unittest.main()
