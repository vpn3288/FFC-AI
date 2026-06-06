from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GuidanceConsistencyTests(unittest.TestCase):
    def test_guides_treat_telegram_and_mattermost_as_equal_first_class_channels(self) -> None:
        guide_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "outputs").glob("*.md"))
        forbidden = [
            "Telegram MUST NOT be used",
            "Telegram absent except as explicit prohibition",
            "Telegram is prohibited",
            "Mattermost is primary",
            "Mattermost selected as primary platform",
            "MUST NOT replace Mattermost as the default platform",
            "default Mattermost",
            "optional-only",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, guide_text)
        self.assertIn("Telegram and Mattermost MUST be treated as equal first-class communication platforms", guide_text)
        self.assertIn("Telegram may be the user's primary daily phone entrypoint", guide_text)
        self.assertIn("explicit pairing", guide_text)
        self.assertIn("Mattermost remains equal", guide_text)

    def test_guides_match_root_full_access_requirement(self) -> None:
        guide_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "outputs").glob("*.md"))
        forbidden = [
            "MUST NOT use `sudo npm install -g`",
            "`chat_only` MUST be default",
            "--sandbox workspace-write",
            "--ignore-user-config",
            "--ephemeral",
            "external_prerequisite",
            "remote shell disabled by default",
            "Bash` MUST NOT be enabled by default",
            "  --dangerously-skip-permissions \\\n",
            "not `--allowedTools`",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, guide_text)
        self.assertIn("VM itself is the security and privacy boundary", guide_text)
        self.assertIn("Default Claude mode for this project is `full_access`", guide_text)
        self.assertIn("root full-access runner commands MUST NOT include it by default", guide_text)
        self.assertIn("--permission-mode acceptEdits", guide_text)
        self.assertIn("--tools Bash,Read,Write,Edit,Grep,Glob", guide_text)
        self.assertIn("--allowedTools Bash(*)", guide_text)
        self.assertIn("root-compatible full-access permission mode", guide_text)
        self.assertIn("--add-dir /", guide_text)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", guide_text)
        self.assertIn('sandbox_mode = "danger-full-access"', guide_text)
        self.assertIn("/ai 完全访问 开启", guide_text)

    def test_guides_require_single_tool_runner_installs(self) -> None:
        guide_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "outputs").glob("*.md"))
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("one AI/tool per VM", guide_text)
        self.assertIn("Mixed primary tool selections", guide_text)
        self.assertIn("MUST be rejected by default", guide_text)
        self.assertIn("stage 01b: remove stale provider configs for unrequested AI tools", guide_text)
        self.assertIn("`all`、`full`、`core` 这类混装入口已默认拒绝", readme_text)
        self.assertNotIn("AI_RUNNER_COMPONENTS=all sudo", readme_text)


if __name__ == "__main__":
    unittest.main()
