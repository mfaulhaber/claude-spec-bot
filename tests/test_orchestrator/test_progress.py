"""Tests for orchestrator_host.progress — Slack progress reporter."""

from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator_host.progress import SlackProgressReporter


class TestSlackProgressReporter:
    def _make_reporter(self) -> tuple[SlackProgressReporter, MagicMock]:
        client = MagicMock()
        # Make chat_postMessage return a dict-like with ts
        client.chat_postMessage.return_value = {"ts": "msg-ts-1"}
        reporter = SlackProgressReporter(client)
        reporter.register_job("job-1", "C123", "thread-ts-1")
        return reporter, client

    def test_register_job(self):
        reporter, _ = self._make_reporter()
        assert "job-1" in reporter._jobs
        assert reporter._jobs["job-1"]["channel_id"] == "C123"

    def test_thinking_posts_status(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "thinking",
            "data": {"iteration": 1},
        })
        client.chat_postMessage.assert_called_once()
        text = client.chat_postMessage.call_args[1]["text"]
        assert "thinking" in text.lower()
        assert "1" in text

    def test_thinking_throttled(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "thinking",
            "data": {"iteration": 1},
        })
        # Immediate second call should be throttled
        client.chat_update = MagicMock()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "thinking",
            "data": {"iteration": 2},
        })
        # Should have edited (or skipped), not posted new
        assert client.chat_postMessage.call_count == 1

    def test_tool_call_posts_message(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "tool_call",
            "data": {"tool_name": "bash", "tool_input": "echo hello", "tool_use_id": "tu-1"},
        })
        client.chat_postMessage.assert_called_once()
        text = client.chat_postMessage.call_args[1]["text"]
        assert "bash" in text
        assert "echo hello" in text

    def test_approval_needed_posts_blocks(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "approval_needed",
            "data": {
                "tool_name": "bash",
                "tool_input": "rm -rf /tmp/test",
                "tool_use_id": "tu-1",
            },
        })
        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert "blocks" in call_kwargs
        blocks = call_kwargs["blocks"]
        assert len(blocks) == 2  # section + actions
        assert blocks[1]["type"] == "actions"

    def test_completed_posts_summary(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "completed",
            "data": {
                "status": "completed",
                "message": "All done",
                "iterations": 5,
                "input_tokens": 1000,
                "output_tokens": 500,
            },
        })
        client.chat_postMessage.assert_called_once()
        text = client.chat_postMessage.call_args[1]["text"]
        assert "completed" in text.lower()
        assert "5" in text

    def test_completed_cancelled(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "completed",
            "data": {"status": "cancelled", "message": ""},
        })
        text = client.chat_postMessage.call_args[1]["text"]
        assert "cancelled" in text.lower()

    def test_failed_posts_error(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "failed",
            "data": {"error": "API key expired"},
        })
        text = client.chat_postMessage.call_args[1]["text"]
        assert "API key expired" in text

    def test_approval_timeout_posts_message(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "job-1",
            "event_type": "approval_timeout",
            "data": {"tool_name": "bash", "timeout": 600},
        })
        client.chat_postMessage.assert_called_once()
        text = client.chat_postMessage.call_args[1]["text"]
        assert "timed out" in text
        assert "bash" in text
        assert "600" in text

    def test_unknown_job_ignored(self):
        reporter, client = self._make_reporter()
        reporter.handle_event({
            "job_id": "unknown-job",
            "event_type": "thinking",
            "data": {},
        })
        client.chat_postMessage.assert_not_called()
