"""Tests for poc.agent — SDK-based agent loop, approval workflow."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

from poc.agent import APPROVAL_REQUIRED_TOOLS, AgentSession
from poc.callback import NullCallbackClient


@pytest.fixture(autouse=True)
def _patch_jobs_dir(tmp_path, monkeypatch):
    """Redirect conversation persistence to a temp directory."""
    monkeypatch.setattr("poc.agent.JOBS_DIR", tmp_path / "jobs")


def _make_session(**kwargs) -> AgentSession:
    defaults = {
        "job_id": "test-1",
        "goal": "List all Python files",
        "model": "claude-sonnet-4-5-20250929",
        "max_turns": 5,
    }
    defaults.update(kwargs)
    session = AgentSession(**defaults)
    session._callback = NullCallbackClient()
    return session


def _make_result_message(result="Done.", is_error=False, num_turns=1):
    """Create a ResultMessage mock."""
    msg = MagicMock(spec=ResultMessage)
    msg.result = result
    msg.is_error = is_error
    msg.num_turns = num_turns
    msg.duration_ms = 5000
    msg.total_cost_usd = 0.01
    # Make isinstance checks work
    msg.__class__ = ResultMessage
    return msg


def _make_assistant_message(content):
    """Create an AssistantMessage mock."""
    msg = MagicMock(spec=AssistantMessage)
    msg.content = content
    msg.__class__ = AssistantMessage
    return msg


class TestCanUseTool:
    """Test the _can_use_tool permission callback directly (async)."""

    @pytest.mark.asyncio
    async def test_auto_approve_read(self):
        """Read tool should be auto-approved."""
        session = _make_session()
        session._approval_event = asyncio.Event()
        result = await session._can_use_tool("Read", {"file_path": "x.py"}, MagicMock())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_auto_approve_glob(self):
        session = _make_session()
        session._approval_event = asyncio.Event()
        result = await session._can_use_tool("Glob", {"pattern": "*.py"}, MagicMock())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_auto_approve_grep(self):
        session = _make_session()
        session._approval_event = asyncio.Event()
        result = await session._can_use_tool("Grep", {"pattern": "TODO"}, MagicMock())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_bash_needs_approval(self):
        """Bash requires approval - verify it pauses."""
        session = _make_session(approval_timeout=1)
        session._approval_event = asyncio.Event()
        # Don't approve -> should time out
        result = await session._can_use_tool("Bash", {"command": "ls"}, MagicMock())
        assert isinstance(result, PermissionResultDeny)
        assert "timed out" in result.message

    @pytest.mark.asyncio
    async def test_write_needs_approval(self):
        session = _make_session(approval_timeout=1)
        session._approval_event = asyncio.Event()
        result = await session._can_use_tool("Write", {"file_path": "x.py"}, MagicMock())
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_edit_needs_approval(self):
        session = _make_session(approval_timeout=1)
        session._approval_event = asyncio.Event()
        result = await session._can_use_tool("Edit", {"file_path": "x.py"}, MagicMock())
        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_approved_tool_auto_allows(self):
        """Previously approved tools don't require re-approval."""
        session = _make_session()
        session._approval_event = asyncio.Event()
        session.approved_tools.add("Bash")
        result = await session._can_use_tool("Bash", {"command": "ls"}, MagicMock())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approval_granted(self):
        """Approval flow: approve in a background task, verify Allow is returned."""
        session = _make_session()
        session._approval_event = asyncio.Event()

        async def approve_after_delay():
            await asyncio.sleep(0.1)
            session.pending_approval = {
                "tool_use_id": session.pending_approval["tool_use_id"],
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
            }
            session._approval_granted = True
            session._approval_event.set()

        task = asyncio.create_task(approve_after_delay())
        result = await session._can_use_tool("Bash", {"command": "echo hi"}, MagicMock())
        await task
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_approval_denied(self):
        """Denial flow: deny in a background task, verify Deny is returned."""
        session = _make_session()
        session._approval_event = asyncio.Event()

        async def deny_after_delay():
            await asyncio.sleep(0.1)
            session._approval_granted = False
            session._approval_event.set()

        task = asyncio.create_task(deny_after_delay())
        result = await session._can_use_tool("Bash", {"command": "rm -rf /"}, MagicMock())
        await task
        assert isinstance(result, PermissionResultDeny)
        assert "denied by the user" in result.message

    @pytest.mark.asyncio
    async def test_approval_timeout_events(self):
        """Timeout posts approval_timeout event."""
        session = _make_session(approval_timeout=1)
        session._approval_event = asyncio.Event()

        await session._can_use_tool("Bash", {"command": "echo timeout"}, MagicMock())

        timeout_events = [
            e for e in session._callback.events if e["event_type"] == "approval_timeout"
        ]
        assert len(timeout_events) == 1
        assert timeout_events[0]["data"]["tool_name"] == "Bash"


class TestAgentSessionSync:
    """Test sync methods (approve, deny, cancel, end, add_message)."""

    def test_approve_matching(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._approval_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}

        ok = session.approve("tu-1")
        assert ok
        assert session._approval_granted

        session._loop.close()

    def test_approve_wrong_id(self):
        session = _make_session()
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}
        ok = session.approve("wrong-id")
        assert not ok

    def test_approve_no_pending(self):
        session = _make_session()
        ok = session.approve("tu-1")
        assert not ok

    def test_approve_auto_approve_tool(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._approval_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}

        session.approve("tu-1", auto_approve_tool=True)
        assert "Bash" in session.approved_tools

        session._loop.close()

    def test_deny_matching(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._approval_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}

        ok = session.deny("tu-1")
        assert ok
        assert not session._approval_granted

        session._loop.close()

    def test_deny_wrong_id(self):
        session = _make_session()
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}
        ok = session.deny("wrong-id")
        assert not ok

    def test_cancel(self):
        session = _make_session()
        session.cancel()
        assert session._cancel_requested

    def test_cancel_while_waiting_approval(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._approval_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.pending_approval = {"tool_use_id": "tu-1", "tool_name": "Bash"}

        session.cancel()
        assert session._cancel_requested
        assert not session._approval_granted

        session._loop.close()

    def test_cancel_signals_message_event(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._message_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.cancel()
        assert session._cancel_requested
        session._loop.close()

    def test_end(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._message_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session._approval_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.end()
        assert session._end_requested
        session._loop.close()

    def test_end_without_loop(self):
        session = _make_session()
        session.end()
        assert session._end_requested

    def test_add_message(self):
        session = _make_session()
        session.add_message("do something else")
        assert session._queued_messages == ["do something else"]

    def test_add_message_signals_message_event(self):
        session = _make_session()
        session._loop = asyncio.new_event_loop()
        session._message_event = session._loop.run_until_complete(
            _create_async_event(session._loop)
        )
        session.add_message("follow up")
        assert session._queued_messages == ["follow up"]
        session._loop.close()


class _AsyncIterFromGen:
    """Wraps an async generator so it works as an async iterator from a non-async mock."""

    def __init__(self, gen_func):
        self._gen_func = gen_func

    def __call__(self):
        return self._gen_func()

    def __aiter__(self):
        return self._gen_func().__aiter__()

    async def __anext__(self):
        pass  # Not used directly


def _setup_mock_client(mock_sdk_cls, messages_gen):
    """Set up mock SDK client with an async message generator."""
    mock_client = AsyncMock()
    mock_sdk_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_sdk_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    # receive_messages must return an async iterator directly (not a coroutine)
    mock_client.receive_messages = _AsyncIterFromGen(messages_gen)
    return mock_client


class TestAgentSessionIntegration:
    """Integration tests using mocked SDK client."""

    @patch("poc.agent.ClaudeSDKClient")
    def test_simple_session_waits_for_input(self, mock_sdk_cls):
        """Agent processes messages, then enters waiting_input state."""
        result_msg = _make_result_message("Here are the Python files.")

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Found files.")])
            yield result_msg

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        # Wait for agent to reach waiting_input
        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)
        assert session.status == "waiting_input"
        assert session.result_text == "Here are the Python files."

        # End the session so the thread can exit
        session.end()
        session._thread.join(timeout=10)
        assert session.status == "completed"

    @patch("poc.agent.ClaudeSDKClient")
    def test_error_completion(self, mock_sdk_cls):
        """Agent handles error result and exits immediately."""
        result_msg = _make_result_message("API Error", is_error=True)

        async def fake_messages():
            yield result_msg

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        session._thread.join(timeout=10)

        assert session.status == "failed"
        assert session.result_text == "API Error"

    @patch("poc.agent.ClaudeSDKClient")
    def test_cancellation_during_messages(self, mock_sdk_cls):
        """Cancel while processing messages."""

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Working...")])
            # Wait for cancel
            for _ in range(50):
                await asyncio.sleep(0.1)
            yield _make_result_message()

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        time.sleep(0.5)
        session.cancel()
        session._thread.join(timeout=10)

        assert session.status == "cancelled"

    @patch("poc.agent.ClaudeSDKClient")
    def test_exception_handling(self, mock_sdk_cls):
        """Agent handles exceptions gracefully."""
        mock_sdk_cls.return_value.__aenter__ = AsyncMock(
            side_effect=RuntimeError("Connection failed")
        )
        mock_sdk_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        session = _make_session()
        session.start()
        session._thread.join(timeout=10)

        assert session.status == "failed"
        assert "Connection failed" in session.result_text

    @patch("poc.agent.ClaudeSDKClient")
    def test_callback_events_emitted(self, mock_sdk_cls):
        """Verify callback events are emitted for persistent session."""
        result_msg = _make_result_message("Done.")

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Working on it.")])
            yield result_msg

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        # Wait for waiting_input
        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)

        event_types = [e["event_type"] for e in session._callback.events]
        assert "progress" in event_types  # From "Agent started" + text block
        assert "assistant_response" in event_types
        assert "waiting_input" in event_types

        session.end()
        session._thread.join(timeout=10)

        event_types = [e["event_type"] for e in session._callback.events]
        assert "session_ended" in event_types

    @patch("poc.agent.ClaudeSDKClient")
    def test_iteration_count(self, mock_sdk_cls):
        """Verify iteration increments per AssistantMessage."""

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Step 1")])
            yield _make_assistant_message([TextBlock(text="Step 2")])
            yield _make_assistant_message([TextBlock(text="Step 3")])
            yield _make_result_message("All done.")

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        # Wait for waiting_input
        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)

        assert session.iteration == 3

        session.end()
        session._thread.join(timeout=10)

    @patch("poc.agent.ClaudeSDKClient")
    def test_persistent_follow_up(self, mock_sdk_cls):
        """Agent processes follow-up message after first response."""
        result1 = _make_result_message("First response.")
        result2 = _make_result_message("Second response.")

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Working...")])
            yield result1
            # After client.query() for follow-up, yield more messages
            yield _make_assistant_message([TextBlock(text="Follow up...")])
            yield result2

        mock_client = _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()

        # Wait for waiting_input
        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)
        assert session.status == "waiting_input"
        assert session.result_text == "First response."

        # Send follow-up message
        session.add_message("Tell me more")

        # Wait for second waiting_input
        for _ in range(100):
            if session.result_text == "Second response.":
                break
            time.sleep(0.1)

        # Wait for waiting_input again
        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)
        assert session.status == "waiting_input"
        assert session.result_text == "Second response."

        # client.query should have been called twice (initial + follow-up)
        assert mock_client.query.call_count == 2

        session.end()
        session._thread.join(timeout=10)

    @patch("poc.agent.ClaudeSDKClient")
    def test_message_queued_while_processing(self, mock_sdk_cls):
        """Messages sent while agent is processing are picked up immediately."""
        result1 = _make_result_message("First.")
        result2 = _make_result_message("Second.")

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Working...")])
            yield result1
            yield _make_assistant_message([TextBlock(text="On follow-up...")])
            yield result2

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        # Pre-queue a message before starting
        session.add_message("queued follow-up")
        session.start()

        # The agent should process the initial goal, then immediately pick up
        # the queued message without going to waiting_input
        for _ in range(100):
            if session.result_text == "Second.":
                break
            time.sleep(0.1)

        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)
        assert session.status == "waiting_input"

        session.end()
        session._thread.join(timeout=10)

    @patch("poc.agent.ClaudeSDKClient")
    def test_end_while_processing(self, mock_sdk_cls):
        """End request while agent is processing terminates cleanly."""

        async def fake_messages():
            yield _make_assistant_message([TextBlock(text="Working...")])
            # Simulate long processing
            for _ in range(50):
                await asyncio.sleep(0.1)
            yield _make_result_message()

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()
        time.sleep(0.3)
        session.end()
        session._thread.join(timeout=10)

        assert session.status == "completed"

    @patch("poc.agent.ClaudeSDKClient")
    def test_cancel_while_waiting_input(self, mock_sdk_cls):
        """Cancel while waiting for input terminates session."""
        result_msg = _make_result_message("Done.")

        async def fake_messages():
            yield result_msg

        _setup_mock_client(mock_sdk_cls, fake_messages)

        session = _make_session()
        session.start()

        for _ in range(100):
            if session.status == "waiting_input":
                break
            time.sleep(0.1)
        assert session.status == "waiting_input"

        session.cancel()
        session._thread.join(timeout=10)

        assert session.status == "cancelled"


class TestApprovalRequiredTools:
    def test_bash_requires_approval(self):
        assert "Bash" in APPROVAL_REQUIRED_TOOLS

    def test_write_requires_approval(self):
        assert "Write" in APPROVAL_REQUIRED_TOOLS

    def test_edit_requires_approval(self):
        assert "Edit" in APPROVAL_REQUIRED_TOOLS

    def test_read_does_not_require_approval(self):
        assert "Read" not in APPROVAL_REQUIRED_TOOLS

    def test_glob_does_not_require_approval(self):
        assert "Glob" not in APPROVAL_REQUIRED_TOOLS


async def _create_async_event(loop):
    """Helper to create an asyncio.Event in the given loop."""
    return asyncio.Event()
