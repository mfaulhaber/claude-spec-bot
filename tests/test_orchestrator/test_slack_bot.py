"""Tests for orchestrator_host.slack_bot (mocked Slack client)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orchestrator_host.slack_bot import (
    SlackCallback,
    _parse_model_flag,
    format_job_status,
    parse_command,
)
from orchestrator_host.state import JobState


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


class TestParseModelFlag:
    def test_no_flag(self):
        model, args = _parse_model_flag("run the tests")
        assert model == ""
        assert args == "run the tests"

    def test_opus(self):
        model, args = _parse_model_flag("--model opus run the tests")
        assert model == "claude-opus-4-20250514"
        assert args == "run the tests"

    def test_sonnet(self):
        model, args = _parse_model_flag("--model sonnet do stuff")
        assert model == "claude-sonnet-4-5-20250929"
        assert args == "do stuff"

    def test_custom_model(self):
        model, args = _parse_model_flag("--model my-custom-model fix bugs")
        assert model == "my-custom-model"
        assert args == "fix bugs"


class TestFormatJobStatus:
    def test_queued(self):
        state = JobState(job_id="test-1", goal="Test", phase="QUEUED")
        text = format_job_status(state)
        assert "test-1" in text
        assert "QUEUED" in text
        assert "claude-sonnet" in text

    def test_running_with_iterations(self):
        state = JobState(
            job_id="test-2",
            goal="Run task",
            phase="RUNNING",
            model="claude-opus-4-20250514",
            agent_iteration=5,
            max_turns=200,
        )
        text = format_job_status(state)
        assert "RUNNING" in text
        assert "5/200" in text
        assert "claude-opus" in text

    def test_with_tokens(self):
        state = JobState(
            job_id="test-3",
            goal="Task",
            phase="DONE",
            input_tokens=5000,
            output_tokens=2000,
        )
        text = format_job_status(state)
        assert "5,000" in text
        assert "2,000" in text

    def test_with_approved_tools(self):
        state = JobState(
            job_id="test-4",
            goal="Task",
            phase="RUNNING",
            approved_tools=["bash", "write_file"],
        )
        text = format_job_status(state)
        assert "bash" in text
        assert "write_file" in text

    def test_failed_with_error(self):
        state = JobState(
            job_id="test-5",
            goal="Fail",
            phase="FAILED",
            error="Agent crashed",
        )
        text = format_job_status(state)
        assert "FAILED" in text
        assert "Agent crashed" in text

    def test_waiting_approval(self):
        state = JobState(
            job_id="test-6",
            goal="Wait",
            phase="WAITING_APPROVAL",
        )
        text = format_job_status(state)
        assert "WAITING_APPROVAL" in text


class TestSlackCallback:
    def test_on_job_started_posts(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")

        cb.on_job_started(state)

        client.chat_postMessage.assert_called_once()
        call_kwargs = client.chat_postMessage.call_args[1]
        assert call_kwargs["channel"] == "C123"
        assert call_kwargs["thread_ts"] == "123.456"
        assert "Agent started" in call_kwargs["text"]

    def test_on_job_done_posts(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")

        cb.on_job_done(state)
        client.chat_postMessage.assert_called_once()
        assert "completed" in client.chat_postMessage.call_args[1]["text"].lower()

    def test_skips_when_no_channel(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g")

        cb.on_job_started(state)
        client.chat_postMessage.assert_not_called()

    def test_on_job_failed_includes_error(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(
            job_id="j1", goal="g", channel_id="C123", thread_ts="123.456",
            error="API key missing",
        )

        cb.on_job_failed(state)
        text = client.chat_postMessage.call_args[1]["text"]
        assert "API key missing" in text

    def test_on_job_cancelled(self):
        client = MagicMock()
        cb = SlackCallback(client)
        state = JobState(job_id="j1", goal="g", channel_id="C123", thread_ts="123.456")

        cb.on_job_cancelled(state)
        text = client.chat_postMessage.call_args[1]["text"]
        assert "cancelled" in text.lower()


class TestButtonHandlers:
    """Test that button handler logic extracts message_ts and passes it correctly.

    We simulate the handler logic inline because Slack Bolt's App() validates
    the token on init, making it hard to create a real app in tests.
    """

    def _simulate_approve_handler(self, approval_manager, body):
        """Replicate the logic from slack_bot.handle_approve."""
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        approval_manager.handle_approve(job_id, tool_use_id, message_ts=message_ts)

    def _simulate_approve_all_handler(self, approval_manager, body):
        """Replicate the logic from slack_bot.handle_approve_all."""
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        approval_manager.handle_approve(job_id, tool_use_id, auto_all=True, message_ts=message_ts)

    def _simulate_deny_handler(self, approval_manager, body):
        """Replicate the logic from slack_bot.handle_deny."""
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        approval_manager.handle_deny(job_id, tool_use_id, message_ts=message_ts)

    @patch("orchestrator_host.approvals.send_approval")
    def test_approve_extracts_message_ts(self, mock_send):
        from orchestrator_host.approvals import ApprovalManager

        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        body = {
            "actions": [{"value": "job-1|tu-1|bash"}],
            "container": {"message_ts": "msg-ts-1"},
        }
        self._simulate_approve_handler(mgr, body)

        client.chat_update.assert_called_once()
        assert client.chat_update.call_args[1]["ts"] == "msg-ts-1"
        assert "Approved" in client.chat_update.call_args[1]["text"]

    @patch("orchestrator_host.approvals.send_approval")
    def test_deny_extracts_message_ts(self, mock_send):
        from orchestrator_host.approvals import ApprovalManager

        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        body = {
            "actions": [{"value": "job-1|tu-1|bash"}],
            "container": {"message_ts": "msg-ts-1"},
        }
        self._simulate_deny_handler(mgr, body)

        client.chat_update.assert_called_once()
        assert client.chat_update.call_args[1]["ts"] == "msg-ts-1"
        assert "Denied" in client.chat_update.call_args[1]["text"]

    @patch("orchestrator_host.approvals.send_approval")
    def test_approve_all_passes_message_ts(self, mock_send):
        from orchestrator_host.approvals import ApprovalManager

        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        body = {
            "actions": [{"value": "job-1|tu-1|bash"}],
            "container": {"message_ts": "msg-ts-1"},
        }
        self._simulate_approve_all_handler(mgr, body)

        client.chat_update.assert_called_once()
        assert "Approved" in client.chat_update.call_args[1]["text"]
        assert "all future calls" in client.chat_update.call_args[1]["text"]

    @patch("orchestrator_host.approvals.send_approval")
    def test_approve_without_container_falls_back(self, mock_send):
        from orchestrator_host.approvals import ApprovalManager

        client = MagicMock()
        mgr = ApprovalManager(slack_client=client)
        mgr.register_pending("job-1", "tu-1", "bash", "C123", "ts-1")

        body = {
            "actions": [{"value": "job-1|tu-1|bash"}],
        }
        self._simulate_approve_handler(mgr, body)

        # No message_ts → falls back to chat_postMessage
        client.chat_postMessage.assert_called_once()
        client.chat_update.assert_not_called()
