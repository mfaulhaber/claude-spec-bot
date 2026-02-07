"""Slack bot: command parsing, handlers, and SlackCallback for pipeline progress."""

from __future__ import annotations

import logging
import re
from typing import Any

from orchestrator_host.jobs import JobQueue, PipelineCallback
from orchestrator_host.state import JobState, StepState, create_job, list_jobs, load_state

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


# --- Help text ---

HELP_TEXT = """\
*POC Orchestrator Commands*

`!poc run <goal>` — Start a new pipeline run with the given goal
`!poc status [job_id]` — Show status of a job (or current job)
`!poc cancel [job_id]` — Cancel a running or queued job
`!poc list` — List recent jobs
`!poc help` — Show this help message
"""


# --- SlackCallback (PipelineCallback implementation) ---


class SlackCallback(PipelineCallback):
    """Reports pipeline progress to Slack threads."""

    def __init__(self, client: Any):
        self.client = client

    def on_step_start(self, state: JobState, step: StepState) -> None:
        self._post(state, f":arrow_forward: Starting step `{step.id}`: `{step.command}`")

    def on_step_done(self, state: JobState, step: StepState) -> None:
        if step.status == "done":
            self._post(state, f":white_check_mark: Step `{step.id}` passed (exit 0)")
        else:
            self._post(
                state,
                f":x: Step `{step.id}` failed (exit {step.exit_code})",
            )

    def on_job_done(self, state: JobState) -> None:
        self._post(state, ":tada: All pipeline steps passed!")

    def on_job_failed(self, state: JobState, step: StepState) -> None:
        self._post(
            state,
            f":rotating_light: Pipeline failed at step `{step.id}` "
            f"(exit {step.exit_code}). "
            f"Check logs: `runner/jobs/{state.job_id}/logs/{step.id}.log`",
        )

    def on_job_blocked(self, state: JobState, blockers: list[str]) -> None:
        blocker_text = "\n".join(f"• {b}" for b in blockers)
        self._post(
            state,
            f":warning: Pipeline is *BLOCKED*:\n{blocker_text}",
        )

    def on_job_cancelled(self, state: JobState) -> None:
        self._post(state, ":stop_sign: Pipeline cancelled.")

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
        "DONE": ":tada:",
        "FAILED": ":x:",
        "CANCELLED": ":stop_sign:",
        "BLOCKED": ":warning:",
    }
    emoji = phase_emoji.get(state.phase, ":question:")

    lines = [
        f"{emoji} *Job {state.job_id}* — {state.phase}",
        f"Goal: _{state.goal}_",
    ]

    for step in state.steps:
        step_emoji = {
            "pending": ":white_circle:",
            "running": ":blue_circle:",
            "done": ":white_check_mark:",
            "failed": ":x:",
            "skipped": ":fast_forward:",
        }.get(step.status, ":question:")
        exit_info = f" (exit {step.exit_code})" if step.exit_code is not None else ""
        lines.append(f"  {step_emoji} `{step.id}`: {step.status}{exit_info}")

    if state.error:
        lines.append(f"\n:rotating_light: Error: {state.error}")

    return "\n".join(lines)


# --- Slack app factory ---


def create_slack_app(queue: JobQueue):
    """Create and configure the Slack Bolt app with !poc command handlers.

    Requires slack_bolt to be installed (orchestrator extra).
    """
    from slack_bolt import App

    app = App(token=_get_env("SLACK_BOT_TOKEN"))

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
            goal = args or "Run default pipeline"
            state = create_job(goal=goal, requested_by=user, channel_id=channel)
            state.thread_ts = thread_ts
            state.original_message_ts = thread_ts
            from orchestrator_host.state import save_state

            save_state(state)

            say(
                text=f":rocket: Job `{state.job_id}` sending to runner: _{goal}_",
                thread_ts=thread_ts,
            )

            from orchestrator_host.docker_exec import send_message_to_runner

            response = send_message_to_runner(state.job_id, goal)
            reply = response.get("reply", response.get("error", "No response from runner"))
            say(text=reply, thread_ts=thread_ts)

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

    @app.event("message")
    def handle_other_messages(event):
        """Acknowledge non-!poc messages so Bolt doesn't log warnings."""
        text = event.get("text", "")
        user = event.get("user", "unknown")
        channel = event.get("channel", "unknown")
        log.debug("Ignored message: user=%s channel=%s text=%r", user, channel, text)

    return app


def _get_env(name: str) -> str:
    """Get a required environment variable."""
    import os

    val = os.environ.get(name, "")
    if not val:
        raise EnvironmentError(f"Required environment variable {name} is not set")
    return val
