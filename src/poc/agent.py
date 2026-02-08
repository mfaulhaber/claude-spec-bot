"""Core agent loop: wraps the Claude Agent SDK with approval workflow."""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
)
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from poc import event_bridge
from poc.callback import CallbackClient, NullCallbackClient

log = logging.getLogger(__name__)

JOBS_DIR = Path("/runner/jobs")

# Tools that require user approval before execution
APPROVAL_REQUIRED_TOOLS = {"Bash", "Write", "Edit"}

SYSTEM_PROMPT = """\
You are an autonomous agent running inside a Docker container.

Directory layout:
- /sandbox — your working directory, create all files here

IMPORTANT: Act autonomously. Do NOT ask the user for permission or confirmation \
before using tools. Just use them directly. A separate approval system handles \
permissions — you should always attempt the action and let that system decide.

Work methodically:
1. Understand the task
2. Plan your approach
3. Implement using your tools (write files to /sandbox)
4. Verify your work (run tests, check output)
5. Report what you did

You have access to web search and web fetch tools for answering questions that \
require current information.

Be concise in your responses. Focus on completing the task efficiently.
"""


@dataclass
class AgentSession:
    """An autonomous agent session that processes a goal via the Claude Agent SDK.

    The session runs in a background thread with its own asyncio event loop.
    When a tool requires approval, the ``can_use_tool`` callback pauses on
    an ``asyncio.Event`` and waits for the orchestrator to call ``approve()``
    or ``deny()``.
    """

    job_id: str
    goal: str
    model: str = ""
    callback_url: str = ""
    max_turns: int = 200
    approval_timeout: int = 600  # seconds to wait for approval before auto-deny
    # --- exposed state for HTTP status endpoint ---
    approved_tools: set[str] = field(default_factory=set)
    pending_approval: dict | None = field(default=None, repr=False)
    iteration: int = 0
    # pending | running | waiting_approval | waiting_input | completed | failed | cancelled
    status: str = "pending"
    result_text: str = ""
    # --- async internals ---
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _approval_event: asyncio.Event | None = field(default=None, repr=False)
    _message_event: asyncio.Event | None = field(default=None, repr=False)
    _approval_granted: bool = field(default=False, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)
    _end_requested: bool = field(default=False, repr=False)
    _callback: CallbackClient | NullCallbackClient = field(
        default_factory=NullCallbackClient, repr=False
    )
    _thread: threading.Thread | None = field(default=None, repr=False)
    _queued_messages: list[str] = field(default_factory=list, repr=False)

    def start(self) -> None:
        """Start the agent loop in a background thread with its own event loop."""
        if self.callback_url:
            self._callback = CallbackClient(self.callback_url, self.job_id)
        self.status = "running"
        self._thread = threading.Thread(
            target=self._run_in_thread, daemon=True, name=f"agent-{self.job_id}"
        )
        self._thread.start()

    def approve(self, tool_use_id: str, auto_approve_tool: bool = False) -> bool:
        """Approve a pending tool call. Returns False if no matching pending call."""
        if not self.pending_approval or self.pending_approval["tool_use_id"] != tool_use_id:
            return False
        if auto_approve_tool:
            tool_name = self.pending_approval["tool_name"]
            self.approved_tools.add(tool_name)
        self._approval_granted = True
        if self._loop and self._approval_event:
            self._loop.call_soon_threadsafe(self._approval_event.set)
        return True

    def deny(self, tool_use_id: str) -> bool:
        """Deny a pending tool call."""
        if not self.pending_approval or self.pending_approval["tool_use_id"] != tool_use_id:
            return False
        self._approval_granted = False
        if self._loop and self._approval_event:
            self._loop.call_soon_threadsafe(self._approval_event.set)
        return True

    def add_message(self, message: str) -> None:
        """Queue a follow-up message to send after the current response completes."""
        self._queued_messages.append(message)
        if self._loop and self._message_event:
            self._loop.call_soon_threadsafe(self._message_event.set)

    def end(self) -> None:
        """Request graceful end of the persistent session."""
        self._end_requested = True
        if self._loop and self._message_event:
            self._loop.call_soon_threadsafe(self._message_event.set)
        if self._loop and self._approval_event:
            self._loop.call_soon_threadsafe(self._approval_event.set)

    def cancel(self) -> None:
        """Request cancellation of the agent loop."""
        self._cancel_requested = True
        if self.pending_approval:
            self._approval_granted = False
            if self._loop and self._approval_event:
                self._loop.call_soon_threadsafe(self._approval_event.set)
        if self._loop and self._message_event:
            self._loop.call_soon_threadsafe(self._message_event.set)

    # ------------------------------------------------------------------
    # Internal: background thread + async agent loop
    # ------------------------------------------------------------------

    def _run_in_thread(self) -> None:
        """Create a new event loop in this thread and run the async agent."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_agent())
        finally:
            self._loop.close()

    async def _run_agent(self) -> None:
        """Main agent loop — persistent session that waits for follow-up messages."""
        self._approval_event = asyncio.Event()
        self._message_event = asyncio.Event()
        self._callback.post_event("progress", {"message": "Agent started", "iteration": 0})

        try:
            options = ClaudeAgentOptions(
                model=self.model or "claude-sonnet-4-5-20250929",
                system_prompt=SYSTEM_PROMPT,
                permission_mode="bypassPermissions",
                can_use_tool=self._can_use_tool,
                max_turns=self.max_turns,
                cwd="/sandbox",
                hooks={
                    "PostToolUse": [
                        HookMatcher(hooks=[self._post_tool_hook]),
                    ],
                },
            )

            async with ClaudeSDKClient(options=options) as client:
                await client.query(self.goal)
                async for message in client.receive_messages():
                    if self._cancel_requested:
                        await client.interrupt()
                        self.status = "cancelled"
                        self._callback.post_event("completed", {
                            "status": "cancelled",
                            "message": "Agent cancelled by user",
                        })
                        return

                    if self._end_requested:
                        await client.interrupt()
                        self.status = "completed"
                        self._callback.post_event("session_ended", {
                            "message": "Session ended by user",
                        })
                        return

                    if isinstance(message, AssistantMessage):
                        self.iteration += 1
                        events = event_bridge.map_assistant_message(message)
                        for ev in events:
                            self._callback.post_event(ev["event_type"], ev["data"])
                    elif isinstance(message, ResultMessage):
                        if message.is_error:
                            ev = event_bridge.map_result_message(message)
                            self._callback.post_event(ev["event_type"], ev["data"])
                            self.status = "failed"
                            self.result_text = message.result or "Unknown error"
                            return

                        self.result_text = (message.result or "")[:2000]
                        self._callback.post_event("assistant_response", {
                            "message": self.result_text,
                            "num_turns": message.num_turns,
                            "duration_ms": message.duration_ms,
                            "total_cost_usd": message.total_cost_usd,
                        })

                        # Check for queued messages first (sent while processing)
                        if self._queued_messages:
                            next_msg = self._queued_messages.pop(0)
                        else:
                            self.status = "waiting_input"
                            self._callback.post_event("waiting_input", {})
                            next_msg = await self._wait_for_message()
                            if next_msg is None:
                                if self._cancel_requested:
                                    self.status = "cancelled"
                                    self._callback.post_event("completed", {
                                        "status": "cancelled",
                                        "message": "Agent cancelled by user",
                                    })
                                else:
                                    self.status = "completed"
                                    self._callback.post_event("session_ended", {
                                        "message": "Session ended by user",
                                    })
                                return

                        self.status = "running"
                        await client.query(next_msg)

        except Exception as exc:
            log.exception("Agent loop failed for job %s", self.job_id)
            self.status = "failed"
            self.result_text = str(exc)
            self._callback.post_event("failed", {"error": str(exc)})

    async def _wait_for_message(self) -> str | None:
        """Block until a message arrives or end/cancel is requested."""
        while not self._queued_messages and not self._end_requested and not self._cancel_requested:
            self._message_event.clear()
            try:
                await asyncio.wait_for(self._message_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
        if self._end_requested or self._cancel_requested:
            return None
        return self._queued_messages.pop(0)

    async def _can_use_tool(
        self,
        tool_name: str,
        tool_input: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Permission callback: auto-approve safe tools, ask for dangerous ones."""
        if tool_name not in APPROVAL_REQUIRED_TOOLS:
            return PermissionResultAllow()

        if tool_name in self.approved_tools:
            return PermissionResultAllow()

        # Need approval — pause and wait
        tool_use_id = f"sdk-{self.job_id}-{self.iteration}-{tool_name}"
        self.pending_approval = {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        self.status = "waiting_approval"
        self._approval_event.clear()
        self._approval_granted = False

        ev = event_bridge.map_approval_needed(
            {"tool_name": tool_name, "tool_input": tool_input},
            tool_use_id,
        )
        self._callback.post_event(ev["event_type"], ev["data"])

        # Block until approval/denial or timeout
        try:
            await asyncio.wait_for(
                self._approval_event.wait(), timeout=self.approval_timeout
            )
        except asyncio.TimeoutError:
            ev = event_bridge.map_approval_timeout(
                {"tool_name": tool_name}, tool_use_id, self.approval_timeout
            )
            self._callback.post_event(ev["event_type"], ev["data"])
            self.pending_approval = None
            self.status = "running"
            return PermissionResultDeny(
                message=(
                    f"Tool call '{tool_name}' was denied automatically — "
                    f"approval timed out after {self.approval_timeout} seconds."
                )
            )

        self.pending_approval = None
        self.status = "running"

        if self._cancel_requested:
            return PermissionResultDeny(message="Agent cancelled by user.")

        if not self._approval_granted:
            return PermissionResultDeny(
                message=f"Tool call '{tool_name}' was denied by the user."
            )

        return PermissionResultAllow()

    async def _post_tool_hook(self, input_data: dict, tool_use_id: str | None, context) -> dict:
        """PostToolUse hook: report tool results to the callback."""
        ev = event_bridge.map_hook_tool_result(input_data, tool_use_id)
        self._callback.post_event(ev["event_type"], ev["data"])
        return {}
