"""Tests for orchestrator_host.slack_bot (mocked Slack client)."""

from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator_host.slack_bot import (
    SlackCallback,
    format_job_status,
    parse_command,
)
from orchestrator_host.state import JobState, StepState


class TestParseCommand:
    def test_run(self):
        assert parse_command("!poc run test the pipeline") == ("run", "test the pipeline")

    def test_status_no_args(self):
        assert parse_command("!poc status") == ("status", "")

    def test_status_with_job_id(self):
        assert parse_command("!poc status abc-123") == ("status", "abc-123")

    def test_cancel(self):
        assert parse_command("!poc cancel abc-123") == ("cancel", "abc-123")

    def test_help(self):
        assert parse_command("!poc help") == ("help", "")

    def test_bare_prefix(self):
        assert parse_command("!poc") == ("help", "")

    def test_not_a_command(self):
        assert parse_command("hello world") == ("", "")

    def test_case_insensitive_prefix(self):
        assert parse_command("!POC run foo") == ("run", "foo")

    def test_list(self):
        assert parse_command("!poc list") == ("list", "")

    def test_unknown_action(self):
        assert parse_command("!poc frobnicate stuff") == ("frobnicate", "stuff")

    def test_extra_whitespace(self):
        assert parse_command("  !poc   run   my goal  ") == ("run", "my goal")


class TestFormatJobStatus:
    def test_queued(self):
        state = JobState(job_id="test-1", goal="Test", phase="QUEUED")
        text = format_job_status(state)
        assert "test-1" in text
        assert "QUEUED" in text

    def test_with_steps(self):
        state = JobState(
            job_id="test-2",
            goal="With steps",
            phase="RUNNING",
            steps=[
                StepState(id="bootstrap", command="c", status="done", exit_code=0),
                StepState(id="test", command="c", status="running"),
            ],
        )
        text = format_job_status(state)
        assert "bootstrap" in text
        assert "done" in text
        assert "running" in text

    def test_failed_with_error(self):
        state = JobState(
            job_id="test-3",
            goal="Fail",
            phase="FAILED",
            error="Step test failed",
        )
        text = format_job_status(state)
        assert "FAILED" in text
        assert "Step test failed" in text


class TestSlackCallback:
    def test_on_step_start_posts(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")
        step = StepState(id="bootstrap", command="scripts/bootstrap.sh")

        cb.on_step_start(state, step)

        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "C123"
        assert call_kwargs["thread_ts"] == "123.456"
        assert "bootstrap" in call_kwargs["text"]

    def test_on_job_done_posts(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")

        cb.on_job_done(state)
        client.chat_postMessage.assert_called_once()
        assert "passed" in client.chat_postMessage.call_args[1]["text"]

    def test_skips_when_no_channel(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g")

        cb.on_step_start(state, StepState(id="s", command="c"))
        client.chat_postMessage.assert_not_called()

    def test_on_job_failed_includes_log_path(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")
        step = StepState(id="test", command="c", exit_code=1)

        cb.on_job_failed(state, step)
        text = client.chat_postMessage.call_args[1]["text"]
        assert "test.log" in text

    def test_on_job_cancelled(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")

        cb.on_job_cancelled(state)
        text = client.chat_postMessage.call_args[1]["text"]
        assert "cancelled" in text.lower()
