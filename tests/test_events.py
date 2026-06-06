from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_remote_runner.events import EventSink, status_event


class EventTests(unittest.TestCase):
    def test_mattermost_post_retries_before_recording_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = EventSink(Path(tmp) / "events.jsonl", "https://mattermost.example.invalid/hooks/id")
            with (
                patch("ai_remote_runner.events.time.sleep"),
                patch("ai_remote_runner.events.urllib.request.urlopen", side_effect=TimeoutError) as urlopen,
            ):
                sink.emit(status_event("run-1", "queued", "正在排队"))
            self.assertEqual(urlopen.call_count, 3)
            failures = Path(tmp) / "events.post-failures.jsonl"
            self.assertTrue(failures.exists())

    def test_mattermost_post_stops_retrying_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = EventSink(Path(tmp) / "events.jsonl", "https://mattermost.example.invalid/hooks/id")
            response = Mock()
            response.read.return_value = b"ok"
            with (
                patch("ai_remote_runner.events.time.sleep"),
                patch("ai_remote_runner.events.urllib.request.urlopen", return_value=response) as urlopen,
            ):
                sink.emit(status_event("run-2", "queued", "正在排队"))
            self.assertEqual(urlopen.call_count, 1)
            self.assertFalse((Path(tmp) / "events.post-failures.jsonl").exists())

    def test_mattermost_post_text_includes_visible_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = EventSink(Path(tmp) / "events.jsonl", "https://mattermost.example.invalid/hooks/id")
            response = Mock()
            response.read.return_value = b"ok"

            def capture(req, timeout):
                payload = json.loads(req.data.decode("utf-8"))
                self.assertIn("provider=claude-code", payload["text"])
                self.assertIn("claude-code", payload["text"])
                return response

            with patch("ai_remote_runner.events.urllib.request.urlopen", side_effect=capture):
                sink.emit(status_event("run-3", "running", "仍在运行", "claude-code"))


if __name__ == "__main__":
    unittest.main()
