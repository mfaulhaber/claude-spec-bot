"""Tests for poc.event_bridge — SDK message to callback event mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

from poc.event_bridge import (
    _summarize_input,
    map_approval_needed,
    map_approval_timeout,
    map_assistant_message,
    map_hook_tool_call,
    map_hook_tool_result,
    map_result_message,
)


class TestMapAssistantMessage:
    def _make_message(self, content):
        msg = MagicMock()
        msg.content = content
        return msg

    def test_thinking_block(self):
        block = MagicMock()
        block.__class__.__name__ = "ThinkingBlock"
        block.thinking = "Let me think about this..."
        # Patch isinstance check
        events = _map_with_types([("ThinkingBlock", block)])
        assert len(events) == 1
        assert events[0]["event_type"] == "thinking"
        assert "think" in events[0]["data"]["thinking"]

    def test_text_block(self):
        block = MagicMock()
        block.__class__.__name__ = "TextBlock"
        block.text = "Here is the answer."
        events = _map_with_types([("TextBlock", block)])
        assert len(events) == 1
        assert events[0]["event_type"] == "progress"
        assert events[0]["data"]["message"] == "Here is the answer."

    def test_empty_text_block_skipped(self):
        block = MagicMock()
        block.__class__.__name__ = "TextBlock"
        block.text = "   "
        events = _map_with_types([("TextBlock", block)])
        assert len(events) == 0

    def test_tool_use_block(self):
        block = MagicMock()
        block.__class__.__name__ = "ToolUseBlock"
        block.name = "Bash"
        block.input = {"command": "echo hi"}
        block.id = "tu-1"
        events = _map_with_types([("ToolUseBlock", block)])
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_call"
        assert events[0]["data"]["tool_name"] == "Bash"

    def test_tool_result_block(self):
        block = MagicMock()
        block.__class__.__name__ = "ToolResultBlock"
        block.tool_use_id = "tu-1"
        block.content = "output text"
        events = _map_with_types([("ToolResultBlock", block)])
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_result"
        assert events[0]["data"]["tool_use_id"] == "tu-1"

    def test_mixed_content(self):
        blocks = [
            ("ThinkingBlock", MagicMock(thinking="hmm")),
            ("TextBlock", MagicMock(text="answer")),
            ("ToolUseBlock", MagicMock(name="Read", input={"file_path": "x.py"}, id="tu-1")),
        ]
        events = _map_with_types(blocks)
        assert len(events) == 3
        types = [e["event_type"] for e in events]
        assert types == ["thinking", "progress", "tool_call"]


def _map_with_types(typed_blocks):
    """Helper: create real SDK-typed blocks for testing map_assistant_message.

    Since we can't instantiate the real SDK types easily, we test the
    _summarize_input and map_* functions directly and use this helper
    to simulate isinstance checks via duck typing.
    """
    from claude_agent_sdk import (
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    real_blocks = []
    for type_name, mock in typed_blocks:
        if type_name == "ThinkingBlock":
            real_blocks.append(ThinkingBlock(thinking=mock.thinking, signature="sig"))
        elif type_name == "TextBlock":
            real_blocks.append(TextBlock(text=mock.text))
        elif type_name == "ToolUseBlock":
            real_blocks.append(ToolUseBlock(id=mock.id, name=mock.name, input=mock.input))
        elif type_name == "ToolResultBlock":
            real_blocks.append(
                ToolResultBlock(tool_use_id=mock.tool_use_id, content=mock.content)
            )

    msg = MagicMock()
    msg.content = real_blocks
    return map_assistant_message(msg)


class TestMapResultMessage:
    def test_completed(self):
        msg = MagicMock()
        msg.is_error = False
        msg.result = "All done!"
        msg.num_turns = 5
        msg.duration_ms = 12000
        msg.total_cost_usd = 0.05
        event = map_result_message(msg)
        assert event["event_type"] == "completed"
        assert event["data"]["status"] == "completed"
        assert event["data"]["message"] == "All done!"
        assert event["data"]["num_turns"] == 5

    def test_failed(self):
        msg = MagicMock()
        msg.is_error = True
        msg.result = "Something broke"
        event = map_result_message(msg)
        assert event["event_type"] == "failed"
        assert event["data"]["error"] == "Something broke"

    def test_completed_no_result(self):
        msg = MagicMock()
        msg.is_error = False
        msg.result = None
        msg.num_turns = 1
        msg.duration_ms = 1000
        msg.total_cost_usd = None
        event = map_result_message(msg)
        assert event["event_type"] == "completed"
        assert event["data"]["message"] == ""


class TestMapHookToolCall:
    def test_basic(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        event = map_hook_tool_call(data, "tu-1")
        assert event["event_type"] == "tool_call"
        assert event["data"]["tool_name"] == "Bash"
        assert event["data"]["tool_input"] == "ls"
        assert event["data"]["tool_use_id"] == "tu-1"

    def test_none_tool_use_id(self):
        data = {"tool_name": "Read", "tool_input": {"file_path": "x.py"}}
        event = map_hook_tool_call(data, None)
        assert event["data"]["tool_use_id"] == ""


class TestMapHookToolResult:
    def test_string_response(self):
        data = {
            "tool_name": "Bash",
            "tool_response": "hello world output",
        }
        event = map_hook_tool_result(data, "tu-1")
        assert event["event_type"] == "tool_result"
        assert event["data"]["result_preview"] == "hello world output"

    def test_dict_response(self):
        data = {
            "tool_name": "Read",
            "tool_response": {"content": "file data"},
        }
        event = map_hook_tool_result(data, "tu-1")
        assert event["event_type"] == "tool_result"
        assert "content" in event["data"]["result_preview"]

    def test_empty_response(self):
        data = {"tool_name": "Write", "tool_response": ""}
        event = map_hook_tool_result(data, "tu-1")
        assert event["data"]["result_preview"] == ""


class TestMapApprovalNeeded:
    def test_basic(self):
        data = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
        event = map_approval_needed(data, "tu-1")
        assert event["event_type"] == "approval_needed"
        assert event["data"]["tool_name"] == "Bash"
        assert event["data"]["tool_use_id"] == "tu-1"


class TestMapApprovalTimeout:
    def test_basic(self):
        data = {"tool_name": "Bash"}
        event = map_approval_timeout(data, "tu-1", 600)
        assert event["event_type"] == "approval_timeout"
        assert event["data"]["timeout"] == 600


class TestSummarizeInput:
    def test_bash(self):
        assert _summarize_input("Bash", {"command": "echo hi"}) == "echo hi"

    def test_read(self):
        assert _summarize_input("Read", {"file_path": "src/main.py"}) == "src/main.py"

    def test_write(self):
        assert _summarize_input("Write", {"file_path": "out.txt"}) == "out.txt"

    def test_edit(self):
        assert _summarize_input("Edit", {"file_path": "x.py"}) == "x.py"

    def test_glob(self):
        assert _summarize_input("Glob", {"pattern": "*.py"}) == "*.py"

    def test_grep(self):
        assert _summarize_input("Grep", {"pattern": "TODO"}) == "TODO"

    def test_web_search(self):
        assert _summarize_input("WebSearch", {"query": "weather"}) == "weather"

    def test_web_fetch(self):
        assert _summarize_input("WebFetch", {"url": "https://example.com"}) == "https://example.com"

    def test_unknown_tool(self):
        result = _summarize_input("Custom", {"a": 1, "b": 2})
        assert "a" in result

    def test_long_command_truncated(self):
        cmd = "x" * 300
        result = _summarize_input("Bash", {"command": cmd})
        assert len(result) <= 203
