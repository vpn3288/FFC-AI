from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GuidanceConsistencyTests(unittest.TestCase):
    def test_guides_allow_telegram_only_as_optional_paired_channel(self) -> None:
        guide_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "outputs").glob("*.md"))
        forbidden = [
            "Telegram MUST NOT be used",
            "Telegram absent except as explicit prohibition",
            "Telegram is prohibited",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, guide_text)
        self.assertIn("Telegram optional", guide_text)
        self.assertIn("explicit pairing", guide_text)
        self.assertIn("Mattermost is primary", guide_text)


if __name__ == "__main__":
    unittest.main()
