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


if __name__ == "__main__":
    unittest.main()
