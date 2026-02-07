"""Tests for poc.agent — agent loop, tool dispatch, approvals."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from poc.agent import AgentSession, _summarize_input
from poc.callback import NullCallbackClient


@pytest.fixture(autouse=True)
def _patch_jobs_dir(tmp_path, monkeypatch):
    """Redirect conversation persistence to a temp directory."""
    monkeypatch.setattr("poc.agent.JOBS_DIR", tmp_path / "jobs")


class TestAgentSession:
    def _make_session(self, **kwargs) -> AgentSession:
        defaults = {
            "job_id": "test-1",
            "goal": "List all Python files",
            "model": "claude-sonnet-4-5-20250929",
            "max_iterations": 5,
        }
        defaults.update(kwargs)
        session = AgentSession(**defaults)
        session._callback = NullCallbackClient()
        return session

    @patch("poc.agent.ClaudeClient")
    def test_simple_text_response(self, mock_client_cls):
        """Agent gets a text response with no tool use -> completes."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Mock a simple text response
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Here are the Python files."

        response = MagicMock()
        response.content = [text_block]
        response.stop_reason = "end_turn"
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.create_message.return_value = response
        mock_client.usage = MagicMock(input_tokens=100, output_tokens=50)

        session = self._make_session()
        session.start()
        session._thread.join(timeout=5)

        assert session.status == "completed"
        assert "Python files" in session.result_text

    @patch("poc.agent.execute_tool")
    @patch("poc.agent.ClaudeClient")
    def test_tool_use_auto_approved(self, mock_client_cls, mock_exec):
        """Auto-approved tools (list_files) execute without waiting."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_exec.return_value = "1\thello.py\n2\tworld.py"

        # First call: tool use
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-1"
        tool_block.name = "list_files"
        tool_block.input = {"pattern": "*.py"}

        resp1 = MagicMock()
        resp1.content = [tool_block]
        resp1.stop_reason = "tool_use"
        resp1.usage = MagicMock(input_tokens=50, output_tokens=30)

        # Second call: text response
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Found 2 files."
        resp2 = MagicMock()
        resp2.content = [text_block]
        resp2.stop_reason = "end_turn"
        resp2.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client.create_message.side_effect = [resp1, resp2]
        mock_client.usage = MagicMock(input_tokens=150, output_tokens=80)

        session = self._make_session()
        session.start()
        session._thread.join(timeout=5)

        assert session.status == "completed"
        mock_exec.assert_called_once_with("list_files", {"pattern": "*.py"})

    @patch("poc.agent.execute_tool")
    @patch("poc.agent.ClaudeClient")
    def test_tool_approval_flow(self, mock_client_cls, mock_exec):
        """Tools requiring approval pause and resume correctly."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_exec.return_value = "ok"

        # Tool use for bash (needs approval)
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-bash"
        tool_block.name = "bash"
        tool_block.input = {"command": "echo hello"}

        resp1 = MagicMock()
        resp1.content = [tool_block]
        resp1.stop_reason = "tool_use"
        resp1.usage = MagicMock(input_tokens=50, output_tokens=30)

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Done."
        resp2 = MagicMock()
        resp2.content = [text_block]
        resp2.stop_reason = "end_turn"
        resp2.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client.create_message.side_effect = [resp1, resp2]
        mock_client.usage = MagicMock(input_tokens=150, output_tokens=80)

        session = self._make_session()
        session.start()

        # Wait for approval request
        for _ in range(50):
            if session.status == "waiting_approval":
                break
            time.sleep(0.1)
        assert session.status == "waiting_approval"
        assert session.pending_approval is not None
        assert session.pending_approval["tool_use_id"] == "tu-bash"

        # Approve
        session.approve("tu-bash")

        session._thread.join(timeout=5)
        assert session.status == "completed"
        mock_exec.assert_called_once_with("bash", {"command": "echo hello"})

    @patch("poc.agent.execute_tool")
    @patch("poc.agent.ClaudeClient")
    def test_tool_denial(self, mock_client_cls, mock_exec):
        """Denied tools return denial message to Claude."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Tool use for bash
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-bash"
        tool_block.name = "bash"
        tool_block.input = {"command": "rm -rf /"}

        resp1 = MagicMock()
        resp1.content = [tool_block]
        resp1.stop_reason = "tool_use"
        resp1.usage = MagicMock(input_tokens=50, output_tokens=30)

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "OK, I won't do that."
        resp2 = MagicMock()
        resp2.content = [text_block]
        resp2.stop_reason = "end_turn"
        resp2.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client.create_message.side_effect = [resp1, resp2]
        mock_client.usage = MagicMock(input_tokens=150, output_tokens=80)

        session = self._make_session()
        session.start()

        for _ in range(50):
            if session.status == "waiting_approval":
                break
            time.sleep(0.1)

        session.deny("tu-bash")
        session._thread.join(timeout=5)

        assert session.status == "completed"
        # Tool should not have been executed
        mock_exec.assert_not_called()
        # The conversation should contain the denial message
        tool_results = [m for m in session.conversation if isinstance(m.get("content"), list)]
        assert any(
            "denied" in str(r.get("content", "")).lower()
            for r in tool_results
            if isinstance(r.get("content"), list)
        )

    @patch("poc.agent.execute_tool")
    @patch("poc.agent.ClaudeClient")
    def test_cancellation(self, mock_client_cls, mock_exec):
        """Cancel stops the agent loop during tool processing."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_exec.return_value = "ok"

        # Return tool uses to keep the loop going, cancel during tool approval wait
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-cancel"
        tool_block.name = "bash"
        tool_block.input = {"command": "sleep 10"}

        response = MagicMock()
        response.content = [tool_block]
        response.stop_reason = "tool_use"
        response.usage = MagicMock(input_tokens=10, output_tokens=10)

        mock_client.create_message.return_value = response
        mock_client.usage = MagicMock(input_tokens=10, output_tokens=10)

        session = self._make_session()
        session.start()

        # Wait for approval request
        for _ in range(50):
            if session.status == "waiting_approval":
                break
            time.sleep(0.1)
        assert session.status == "waiting_approval"

        # Cancel while waiting for approval
        session.cancel()
        session._thread.join(timeout=5)

        assert session.status == "cancelled"

    @patch("poc.agent.ClaudeClient")
    def test_max_iterations(self, mock_client_cls):
        """Agent stops after max iterations."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Always return a tool use to keep the loop going
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-1"
        tool_block.name = "list_files"
        tool_block.input = {"pattern": "*.py"}

        response = MagicMock()
        response.content = [tool_block]
        response.stop_reason = "tool_use"
        response.usage = MagicMock(input_tokens=10, output_tokens=10)

        mock_client.create_message.return_value = response
        mock_client.usage = MagicMock(input_tokens=100, output_tokens=100)

        with patch("poc.agent.execute_tool", return_value="files"):
            session = self._make_session(max_iterations=3)
            session.start()
            session._thread.join(timeout=10)

        assert session.status == "completed"
        assert session.iteration == 3

    @patch("poc.agent.execute_tool")
    @patch("poc.agent.ClaudeClient")
    def test_approval_timeout(self, mock_client_cls, mock_exec):
        """Approval times out and is treated as a denial."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Tool use for bash (needs approval)
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu-timeout"
        tool_block.name = "bash"
        tool_block.input = {"command": "echo timeout"}

        resp1 = MagicMock()
        resp1.content = [tool_block]
        resp1.stop_reason = "tool_use"
        resp1.usage = MagicMock(input_tokens=50, output_tokens=30)

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "OK, timed out."
        resp2 = MagicMock()
        resp2.content = [text_block]
        resp2.stop_reason = "end_turn"
        resp2.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_client.create_message.side_effect = [resp1, resp2]
        mock_client.usage = MagicMock(input_tokens=150, output_tokens=80)

        session = self._make_session(approval_timeout=1)  # 1 second timeout
        session.start()

        # Wait for approval request
        for _ in range(50):
            if session.status == "waiting_approval":
                break
            time.sleep(0.1)
        assert session.status == "waiting_approval"

        # Don't approve — let it time out
        session._thread.join(timeout=5)

        assert session.status == "completed"
        # Tool should NOT have been executed (timed out = denied)
        mock_exec.assert_not_called()
        # Callback should have received approval_timeout event
        timeout_events = [
            e for e in session._callback.events if e["event_type"] == "approval_timeout"
        ]
        assert len(timeout_events) == 1
        assert timeout_events[0]["data"]["tool_name"] == "bash"

    def test_add_message(self):
        session = self._make_session()
        session.add_message("additional instructions")
        assert session.conversation[-1]["content"] == "additional instructions"


class TestSummarizeInput:
    def test_bash(self):
        assert _summarize_input("bash", {"command": "echo hi"}) == "echo hi"

    def test_read_file(self):
        assert _summarize_input("read_file", {"path": "src/main.py"}) == "src/main.py"

    def test_list_files(self):
        assert _summarize_input("list_files", {"pattern": "*.py"}) == "*.py"

    def test_long_command_truncated(self):
        cmd = "x" * 300
        result = _summarize_input("bash", {"command": cmd})
        assert len(result) <= 203
