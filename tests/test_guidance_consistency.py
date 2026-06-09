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

    def test_guides_allow_full_tool_installs_with_one_active_provider(self) -> None:
        guide_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "outputs").glob("*.md"))
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        script_text = (ROOT / "scripts" / "run-independent-review.sh").read_text(encoding="utf-8")
        forbidden = [
            "reject mixed primary tools by default",
            "one AI/tool per VM",
            "每个VM/VPS只装一种主AI工具",
            "每个虚拟机/VPS只装一种主AI工具",
            "一台机器只装 `codex`",
            "是否拒绝混合安装",
            "是否强制单一AI工具策略",
        ]
        combined = "\n".join([guide_text, readme_text, script_text])
        for phrase in forbidden:
            self.assertNotIn(phrase, combined)
        self.assertIn("AI_RUNNER_COMPONENTS=all,telegram", guide_text)
        self.assertIn("Multiple providers may be configured on one machine", guide_text)
        self.assertIn("runner still chooses one default provider at a time", guide_text)
        self.assertIn("AI_RUNNER_COMPONENTS=all,telegram", readme_text)
        self.assertIn("bootstrap-debian12.sh", readme_text)
        self.assertIn("/ai 提供商 使用 codex", readme_text)


if __name__ == "__main__":
    unittest.main()
