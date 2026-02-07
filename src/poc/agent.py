"""Core agent loop: drives the Claude API and dispatches tool calls."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from poc.callback import CallbackClient, NullCallbackClient
from poc.claude_client import ClaudeClient
from poc.tools import TOOL_SCHEMAS, execute_tool

log = logging.getLogger(__name__)

JOBS_DIR = Path("/runner/jobs")

# Default permission levels for each tool
DEFAULT_PERMISSIONS: dict[str, str] = {
    "read_file": "auto",
    "list_files": "auto",
    "search_files": "auto",
    "bash": "ask_once",
    "write_file": "ask_once",
    "edit_file": "ask_once",
}


@dataclass
class AgentSession:
    """An autonomous agent session that processes a goal via Claude API calls.

    The session runs in a background thread.  When a tool requires approval,
    the loop pauses on ``_approval_event`` and waits for the orchestrator to
    call ``approve()`` or ``deny()``.
    """

    job_id: str
    goal: str
    model: str = ""
    callback_url: str = ""
    permissions: dict[str, str] = field(default_factory=dict)
    max_iterations: int = 200
    approval_timeout: int = 600  # seconds to wait for approval before auto-deny
    # --- internal state ---
    conversation: list[dict] = field(default_factory=list)
    approved_tools: set[str] = field(default_factory=set)
    pending_approval: dict | None = field(default=None, repr=False)
    iteration: int = 0
    status: str = "pending"  # pending | running | waiting_approval | completed | failed | cancelled
    result_text: str = ""
    _approval_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _approval_granted: bool = field(default=False, repr=False)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _claude: ClaudeClient | None = field(default=None, repr=False)
    _callback: CallbackClient | NullCallbackClient = field(
        default_factory=NullCallbackClient, repr=False
    )
    _thread: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.permissions:
            self.permissions = dict(DEFAULT_PERMISSIONS)

    def start(self) -> None:
        """Start the agent loop in a background thread."""
        self._claude = ClaudeClient(model=self.model or "claude-sonnet-4-5-20250929")
        if self.callback_url:
            self._callback = CallbackClient(self.callback_url, self.job_id)
        self.status = "running"
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"agent-{self.job_id}"
        )
        self._thread.start()

    def approve(self, tool_use_id: str, auto_approve_tool: bool = False) -> bool:
        """Approve a pending tool call. Returns False if no matching pending call."""
        if not self.pending_approval or self.pending_approval["tool_use_id"] != tool_use_id:
            return False
        if auto_approve_tool:
            tool_name = self.pending_approval["tool_name"]
            self.approved_tools.add(tool_name)
            self.permissions[tool_name] = "auto"
        self._approval_granted = True
        self._approval_event.set()
        return True

    def deny(self, tool_use_id: str) -> bool:
        """Deny a pending tool call."""
        if not self.pending_approval or self.pending_approval["tool_use_id"] != tool_use_id:
            return False
        self._approval_granted = False
        self._approval_event.set()
        return True

    def add_message(self, message: str) -> None:
        """Inject a user message into the conversation (for follow-up instructions)."""
        self.conversation.append({"role": "user", "content": message})

    def cancel(self) -> None:
        """Request cancellation of the agent loop."""
        self._cancel_event.set()
        if self.pending_approval:
            self._approval_granted = False
            self._approval_event.set()

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main agent loop — calls Claude API and dispatches tools."""
        try:
            # Initial user message
            self.conversation.append({"role": "user", "content": self.goal})
            self._callback.post_event("progress", {"message": "Agent started", "iteration": 0})

            while self.iteration < self.max_iterations:
                if self._cancel_event.is_set():
                    self.status = "cancelled"
                    self._callback.post_event("completed", {
                        "status": "cancelled",
                        "message": "Agent cancelled by user",
                    })
                    return

                self.iteration += 1
                self._callback.post_event("thinking", {"iteration": self.iteration})

                response = self._claude.create_message(
                    messages=self.conversation,
                    tools=TOOL_SCHEMAS,
                )

                # Build assistant turn content
                assistant_content = []
                text_parts = []
                tool_uses = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                        assistant_content.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif block.type == "tool_use":
                        tool_uses.append(block)
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                self.conversation.append({"role": "assistant", "content": assistant_content})

                # end_turn with no tool calls → done
                if response.stop_reason == "end_turn" and not tool_uses:
                    self.result_text = "\n".join(text_parts)
                    self.status = "completed"
                    self._callback.post_event("completed", {
                        "status": "completed",
                        "message": self.result_text[:2000],
                        "iterations": self.iteration,
                        "input_tokens": self._claude.usage.input_tokens,
                        "output_tokens": self._claude.usage.output_tokens,
                    })
                    self._persist_conversation()
                    return

                # Process tool calls
                tool_results = []
                for tool_use in tool_uses:
                    if self._cancel_event.is_set():
                        self.status = "cancelled"
                        self._callback.post_event("completed", {
                            "status": "cancelled",
                            "message": "Agent cancelled by user",
                        })
                        return

                    result_text = self._handle_tool_call(tool_use)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_text,
                    })

                self.conversation.append({"role": "user", "content": tool_results})
                self._persist_conversation()

                # Report token usage periodically
                if self.iteration % 10 == 0:
                    self._callback.post_event("token_usage", {
                        "input_tokens": self._claude.usage.input_tokens,
                        "output_tokens": self._claude.usage.output_tokens,
                        "iteration": self.iteration,
                    })

            # Max iterations reached
            self.status = "completed"
            self.result_text = f"Reached maximum iterations ({self.max_iterations})"
            self._callback.post_event("completed", {
                "status": "max_iterations",
                "message": self.result_text,
                "iterations": self.iteration,
            })
            self._persist_conversation()

        except Exception as exc:
            log.exception("Agent loop failed for job %s", self.job_id)
            self.status = "failed"
            self.result_text = str(exc)
            self._callback.post_event("failed", {"error": str(exc)})

    def _handle_tool_call(self, tool_use) -> str:
        """Check permissions, possibly wait for approval, then execute."""
        tool_name = tool_use.name
        tool_input = tool_use.input

        self._callback.post_event("tool_call", {
            "tool_name": tool_name,
            "tool_input": _summarize_input(tool_name, tool_input),
            "tool_use_id": tool_use.id,
        })

        # Check permission
        perm = self.permissions.get(tool_name, "ask_once")
        needs_approval = False
        if perm == "auto" or tool_name in self.approved_tools:
            needs_approval = False
        elif perm == "ask_always":
            needs_approval = True
        elif perm == "ask_once":
            if tool_name not in self.approved_tools:
                needs_approval = True

        if needs_approval:
            result_text = self._wait_for_approval(tool_use)
            if result_text is not None:
                return result_text

        # Execute the tool
        result = execute_tool(tool_name, tool_input)

        self._callback.post_event("tool_result", {
            "tool_name": tool_name,
            "tool_use_id": tool_use.id,
            "result_preview": result[:500] if result else "",
        })

        return result

    def _wait_for_approval(self, tool_use) -> str | None:
        """Pause the loop and wait for approval. Returns denial text or None if approved."""
        self.pending_approval = {
            "tool_use_id": tool_use.id,
            "tool_name": tool_use.name,
            "tool_input": tool_use.input,
        }
        self.status = "waiting_approval"
        self._approval_event.clear()
        self._approval_granted = False

        self._callback.post_event("approval_needed", {
            "tool_use_id": tool_use.id,
            "tool_name": tool_use.name,
            "tool_input": _summarize_input(tool_use.name, tool_use.input),
        })

        # Block until approval/denial or timeout
        got_response = self._approval_event.wait(timeout=self.approval_timeout)
        if not got_response:
            # Timed out — treat as denial
            self._callback.post_event("approval_timeout", {
                "tool_use_id": tool_use.id,
                "tool_name": tool_use.name,
                "timeout": self.approval_timeout,
            })
            self.pending_approval = None
            self.status = "running"
            return (
                f"Tool call '{tool_use.name}' was denied automatically — "
                f"approval timed out after {self.approval_timeout} seconds."
            )

        self.pending_approval = None
        self.status = "running"

        if not self._approval_granted:
            return f"Tool call '{tool_use.name}' was denied by the user."

        # If ask_once and approved, auto-approve future calls
        perm = self.permissions.get(tool_use.name, "ask_once")
        if perm == "ask_once":
            self.approved_tools.add(tool_use.name)

        return None  # approved — proceed to execute

    def _persist_conversation(self) -> None:
        """Save conversation to disk for recovery/debugging."""
        path = JOBS_DIR / self.job_id / "conversation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(self.conversation, indent=2))
        except Exception:
            log.debug("Failed to persist conversation for job %s", self.job_id)


def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """Create a short summary of tool input for display."""
    if tool_name == "bash":
        cmd = tool_input.get("command", "")
        return cmd[:200] if len(cmd) <= 200 else cmd[:197] + "..."
    if tool_name in ("read_file", "write_file", "edit_file"):
        return tool_input.get("path", "")
    if tool_name == "list_files":
        return tool_input.get("pattern", "")
    if tool_name == "search_files":
        return tool_input.get("pattern", "")
    return json.dumps(tool_input)[:200]
