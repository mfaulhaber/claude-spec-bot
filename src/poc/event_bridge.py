"""Maps Claude Agent SDK messages and hook data to our callback event protocol.

The SDK emits AssistantMessage, ResultMessage, SystemMessage, etc.
We convert these into the flat event types the orchestrator expects:
thinking, tool_call, tool_result, approval_needed, approval_timeout,
completed, failed, token_usage.
"""

from __future__ import annotations

import json
import logging

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

log = logging.getLogger(__name__)


def map_assistant_message(message: AssistantMessage) -> list[dict]:
    """Convert an AssistantMessage to a list of callback events.

    Emits:
    - 'thinking' for ThinkingBlock content
    - 'tool_call' for ToolUseBlock content
    - 'progress' for TextBlock content
    """
    events = []
    for block in message.content:
        if isinstance(block, ThinkingBlock):
            events.append({
                "event_type": "thinking",
                "data": {"thinking": block.thinking[:500]},
            })
        elif isinstance(block, ToolUseBlock):
            events.append({
                "event_type": "tool_call",
                "data": {
                    "tool_name": block.name,
                    "tool_input": _summarize_input(block.name, block.input),
                    "tool_use_id": block.id,
                },
            })
        elif isinstance(block, TextBlock):
            if block.text.strip():
                events.append({
                    "event_type": "progress",
                    "data": {"message": block.text[:2000]},
                })
        elif isinstance(block, ToolResultBlock):
            events.append({
                "event_type": "tool_result",
                "data": {
                    "tool_use_id": block.tool_use_id,
                    "result_preview": (block.content or "")[:500]
                    if isinstance(block.content, str)
                    else "",
                },
            })
    return events


def map_result_message(message: ResultMessage) -> dict:
    """Convert a ResultMessage to a callback event.

    Emits 'completed' or 'failed'.
    """
    if message.is_error:
        return {
            "event_type": "failed",
            "data": {"error": message.result or "Unknown error"},
        }
    return {
        "event_type": "completed",
        "data": {
            "status": "completed",
            "message": (message.result or "")[:2000],
            "num_turns": message.num_turns,
            "duration_ms": message.duration_ms,
            "total_cost_usd": message.total_cost_usd,
        },
    }


def map_hook_tool_call(input_data: dict, tool_use_id: str | None) -> dict:
    """Create a tool_call event from PreToolUse hook data."""
    return {
        "event_type": "tool_call",
        "data": {
            "tool_name": input_data.get("tool_name", ""),
            "tool_input": _summarize_input(
                input_data.get("tool_name", ""),
                input_data.get("tool_input", {}),
            ),
            "tool_use_id": tool_use_id or "",
        },
    }


def map_hook_tool_result(input_data: dict, tool_use_id: str | None) -> dict:
    """Create a tool_result event from PostToolUse hook data."""
    response = input_data.get("tool_response", "")
    preview = ""
    if isinstance(response, str):
        preview = response[:500]
    elif isinstance(response, dict):
        preview = json.dumps(response)[:500]
    return {
        "event_type": "tool_result",
        "data": {
            "tool_name": input_data.get("tool_name", ""),
            "tool_use_id": tool_use_id or "",
            "result_preview": preview,
        },
    }


def map_approval_needed(input_data: dict, tool_use_id: str | None) -> dict:
    """Create an approval_needed event from PreToolUse hook data."""
    return {
        "event_type": "approval_needed",
        "data": {
            "tool_use_id": tool_use_id or "",
            "tool_name": input_data.get("tool_name", ""),
            "tool_input": _summarize_input(
                input_data.get("tool_name", ""),
                input_data.get("tool_input", {}),
            ),
        },
    }


def map_approval_timeout(input_data: dict, tool_use_id: str | None, timeout: int) -> dict:
    """Create an approval_timeout event."""
    return {
        "event_type": "approval_timeout",
        "data": {
            "tool_use_id": tool_use_id or "",
            "tool_name": input_data.get("tool_name", ""),
            "timeout": timeout,
        },
    }


def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """Create a short summary of tool input for display."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:200] if len(cmd) <= 200 else cmd[:197] + "..."
    if tool_name in ("Read", "Write", "Edit"):
        return tool_input.get("file_path", "")
    if tool_name == "Glob":
        return tool_input.get("pattern", "")
    if tool_name == "Grep":
        return tool_input.get("pattern", "")
    if tool_name == "WebSearch":
        return tool_input.get("query", "")
    if tool_name == "WebFetch":
        return tool_input.get("url", "")
    return json.dumps(tool_input)[:200]
