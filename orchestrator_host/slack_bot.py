"""Slack bot: command parsing, handlers, and progress callbacks for agent mode."""

from __future__ import annotations

import logging
import re
from typing import Any

from orchestrator_host.approvals import ApprovalManager
from orchestrator_host.docker_exec import send_message
from orchestrator_host.jobs import AgentCallback, JobQueue
from orchestrator_host.progress import SlackProgressReporter
from orchestrator_host.state import JobState, create_job, list_jobs, load_state, save_state

log = logging.getLogger(__name__)

COMMAND_PREFIX = "!poc"

# --- Command parsing ---


def parse_command(text: str) -> tuple[str, str]:
    """Parse a !poc command. Returns (action, rest_of_text).

    Examples:
        "!poc run test the pipeline"  -> ("run", "test the pipeline")
        "!poc status"                 -> ("status", "")
        "!poc cancel abc123"          -> ("cancel", "abc123")
        "!poc help"                   -> ("help", "")
        "hello world"                 -> ("", "")
    """
    text = text.strip()
    if not text.lower().startswith(COMMAND_PREFIX):
        return ("", "")

    rest = text[len(COMMAND_PREFIX) :].strip()
    if not rest:
        return ("help", "")

    parts = rest.split(None, 1)
    action = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return (action, args)


def _parse_model_flag(args: str) -> tuple[str, str]:
    """Extract --model flag from args. Returns (model, remaining_args)."""
    model_map = {
        "opus": "claude-opus-4-20250514",
        "sonnet": "claude-sonnet-4-5-20250929",
        "haiku": "claude-haiku-4-5-20251001",
    }
    match = re.match(r"--model\s+(\S+)\s*(.*)", args, re.DOTALL)
    if match:
        model_key = match.group(1).lower()
        remaining = match.group(2).strip()
        model = model_map.get(model_key, model_key)
        return model, remaining
    return "", args


# --- Help text ---

HELP_TEXT = """\
*POC Agent Commands*

`!poc run [--model opus|sonnet] <task>` — Start the agent with a task
`!poc status [job_id]` — Show agent status
`!poc cancel [job_id]` — Cancel a running agent
`!poc list` — List recent jobs
`!poc help` — Show this help message

The agent will request approval for bash commands and file writes.
Reply "yes"/"approve" or "no"/"deny" in the thread, or use the buttons.
"""


# --- SlackCallback (AgentCallback implementation) ---


class SlackCallback(AgentCallback):
    """Reports agent lifecycle events to Slack threads."""

    def __init__(self, client: Any):
        self.client = client

    def on_job_started(self, state: JobState) -> None:
        self._post(state, f":robot_face: Agent started (model: `{state.model}`)")

    def on_job_done(self, state: JobState) -> None:
        self._post(state, ":white_check_mark: Agent completed!")

    def on_job_failed(self, state: JobState) -> None:
        error = state.error or "Unknown error"
        self._post(state, f":x: Agent failed: {error}")

    def on_job_cancelled(self, state: JobState) -> None:
        self._post(state, ":stop_sign: Agent cancelled.")

    def _post(self, state: JobState, text: str) -> None:
        if not state.channel_id or not state.thread_ts:
            log.warning("Cannot post to Slack: missing channel_id or thread_ts")
            return
        try:
            self.client.chat_postMessage(
                channel=state.channel_id,
                thread_ts=state.thread_ts,
                text=text,
            )
        except Exception:
            log.exception("Failed to post Slack message")


# --- Status formatting ---


def format_job_status(state: JobState) -> str:
    """Format a job state into a human-readable Slack message."""
    phase_emoji = {
        "QUEUED": ":hourglass:",
        "RUNNING": ":gear:",
        "WAITING_APPROVAL": ":lock:",
        "DONE": ":white_check_mark:",
        "FAILED": ":x:",
        "CANCELLED": ":stop_sign:",
        "BLOCKED": ":warning:",
    }
    emoji = phase_emoji.get(state.phase, ":question:")

    lines = [
        f"{emoji} *Job {state.job_id}* — {state.phase}",
        f"Goal: _{state.goal}_",
        f"Model: `{state.model}`",
        f"Iteration: {state.agent_iteration}/{state.max_iterations}",
    ]

    if state.input_tokens or state.output_tokens:
        lines.append(f"Tokens: {state.input_tokens:,} in / {state.output_tokens:,} out")

    if state.approved_tools:
        lines.append(f"Approved tools: {', '.join(state.approved_tools)}")

    if state.error:
        lines.append(f"\n:rotating_light: Error: {state.error}")

    return "\n".join(lines)


# --- Slack app factory ---


def create_slack_app(
    queue: JobQueue,
    progress_reporter: SlackProgressReporter | None = None,
    approval_manager: ApprovalManager | None = None,
):
    """Create and configure the Slack Bolt app with !poc command handlers.

    Requires slack_bolt to be installed (orchestrator extra).
    """
    from slack_bolt import App

    app = App(token=_get_env("SLACK_BOT_TOKEN"))

    # Track job_id -> thread_ts mapping for text reply handling
    _job_threads: dict[str, str] = {}  # thread_ts -> job_id

    @app.message(re.compile(r"^!poc\b", re.IGNORECASE))
    def handle_poc_command(message, say, client):
        text = message.get("text", "")
        user = message.get("user", "unknown")
        channel = message["channel"]
        thread_ts = message.get("ts", "")
        log.debug("Matched !poc command: user=%s channel=%s text=%r", user, channel, text)
        action, args = parse_command(text)
        log.debug("Parsed command: action=%r args=%r", action, args)

        if action == "help" or action == "":
            say(text=HELP_TEXT, thread_ts=thread_ts)

        elif action == "run":
            model, goal = _parse_model_flag(args)
            goal = goal or "Complete the task"

            state = create_job(
                goal=goal, requested_by=user, channel_id=channel,
                model=model or "claude-sonnet-4-5-20250929",
            )
            state.thread_ts = thread_ts
            state.original_message_ts = thread_ts
            save_state(state)

            # Register with progress reporter
            if progress_reporter:
                progress_reporter.register_job(state.job_id, channel, thread_ts)

            # Track thread for text replies
            _job_threads[thread_ts] = state.job_id

            say(
                text=f":rocket: Job `{state.job_id}` started: _{goal}_\nModel: `{state.model}`",
                thread_ts=thread_ts,
            )

            queue.enqueue(state.job_id)

        elif action == "status":
            job_id = args.strip() if args.strip() else queue.current_job_id
            if not job_id:
                say(text="No active job. Use `!poc list` to see recent jobs.", thread_ts=thread_ts)
                return
            try:
                state = load_state(job_id)
                say(text=format_job_status(state), thread_ts=thread_ts)
            except FileNotFoundError:
                say(text=f":x: Job `{job_id}` not found.", thread_ts=thread_ts)

        elif action == "cancel":
            job_id = args.strip() if args.strip() else queue.current_job_id
            if not job_id:
                say(text="No active job to cancel.", thread_ts=thread_ts)
                return
            if queue.cancel(job_id):
                say(text=f":stop_sign: Cancellation requested for `{job_id}`.", thread_ts=thread_ts)
            else:
                say(text=f":x: Job `{job_id}` not found or already finished.", thread_ts=thread_ts)

        elif action == "list":
            job_ids = list_jobs()
            if not job_ids:
                say(text="No jobs found.", thread_ts=thread_ts)
                return
            # Show last 10
            recent = job_ids[-10:]
            lines = ["*Recent jobs:*"]
            for jid in reversed(recent):
                try:
                    st = load_state(jid)
                    lines.append(f"  `{jid}` — {st.phase} — _{st.goal[:60]}_")
                except Exception:
                    lines.append(f"  `{jid}` — (error reading state)")
            say(text="\n".join(lines), thread_ts=thread_ts)

        else:
            say(
                text=f":question: Unknown command `{action}`. Try `!poc help`.",
                thread_ts=thread_ts,
            )

    # --- Interactive action handlers (Block Kit buttons) ---

    @app.action("approve_tool")
    def handle_approve(ack, body, client):
        ack()
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        if approval_manager:
            approval_manager.handle_approve(job_id, tool_use_id, message_ts=message_ts)

    @app.action("approve_tool_all")
    def handle_approve_all(ack, body, client):
        ack()
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        if approval_manager:
            approval_manager.handle_approve(
                job_id, tool_use_id, auto_all=True, message_ts=message_ts,
            )

    @app.action("deny_tool")
    def handle_deny(ack, body, client):
        ack()
        value = body["actions"][0]["value"]
        job_id, tool_use_id, tool_name = value.split("|", 2)
        message_ts = body.get("container", {}).get("message_ts", "")
        if approval_manager:
            approval_manager.handle_deny(job_id, tool_use_id, message_ts=message_ts)

    # --- Thread reply handler ---

    @app.event("message")
    def handle_other_messages(event, client):
        """Handle thread replies: approval text or follow-up instructions."""
        text = event.get("text", "")
        thread_ts = event.get("thread_ts")

        if not thread_ts or not text:
            return

        # Check if this thread is associated with a job
        job_id = _job_threads.get(thread_ts)
        if not job_id:
            return

        # Skip if it's a !poc command (handled above)
        if text.strip().lower().startswith("!poc"):
            return

        # Try as approval text reply
        if approval_manager and approval_manager.handle_text_reply(job_id, text):
            return

        # Otherwise, forward as a follow-up message to the agent
        send_message(job_id, text)

    return app


def _get_env(name: str) -> str:
    """Get a required environment variable."""
    import os

    val = os.environ.get(name, "")
    if not val:
        raise EnvironmentError(f"Required environment variable {name} is not set")
    return val
