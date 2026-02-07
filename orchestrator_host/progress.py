"""Slack progress reporter — maps runner events to Slack messages."""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# Throttle status updates to 1 per N seconds
STATUS_THROTTLE_SECONDS = 3


class SlackProgressReporter:
    """Receives runner callback events and posts progress to Slack threads.

    Each job maps to a Slack thread. The reporter tracks per-job state so it
    can *edit* status messages (avoiding spam) and post new messages for
    discrete events like tool calls and approvals.
    """

    def __init__(self, client: Any):
        self.client = client
        # job_id -> {channel_id, thread_ts, status_ts, last_status_time, tool_call_ts}
        self._jobs: dict[str, dict] = {}

    def register_job(
        self, job_id: str, channel_id: str, thread_ts: str
    ) -> None:
        """Register a job's Slack thread for progress updates."""
        self._jobs[job_id] = {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "status_ts": None,
            "last_status_time": 0.0,
            "tool_call_ts": {},  # tool_use_id -> message ts
        }

    def handle_event(self, event: dict) -> None:
        """Dispatch a runner event to the appropriate handler."""
        job_id = event.get("job_id", "")
        event_type = event.get("event_type", "")
        data = event.get("data", {})

        job = self._jobs.get(job_id)
        if not job:
            log.debug("Event for unknown job %s: %s", job_id, event_type)
            return

        handler = getattr(self, f"_on_{event_type}", None)
        if handler:
            handler(job_id, job, data)
        else:
            log.debug("Unhandled event type: %s", event_type)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_thinking(self, job_id: str, job: dict, data: dict) -> None:
        now = time.time()
        if now - job["last_status_time"] < STATUS_THROTTLE_SECONDS:
            return

        iteration = data.get("iteration", "?")
        text = f":thought_balloon: Agent is thinking... (iteration {iteration})"
        self._update_status(job, text)
        job["last_status_time"] = now

    def _on_tool_call(self, job_id: str, job: dict, data: dict) -> None:
        tool_name = data.get("tool_name", "unknown")
        tool_input = data.get("tool_input", "")
        tool_use_id = data.get("tool_use_id", "")

        text = f":gear: `{tool_name}`: `{tool_input[:200]}`"
        ts = self._post(job, text)
        if ts and tool_use_id:
            job["tool_call_ts"][tool_use_id] = ts

    def _on_tool_result(self, job_id: str, job: dict, data: dict) -> None:
        tool_use_id = data.get("tool_use_id", "")
        tool_name = data.get("tool_name", "")
        preview = data.get("result_preview", "")

        msg_ts = job["tool_call_ts"].get(tool_use_id)
        if msg_ts and preview:
            # Edit the tool_call message to append the result
            short = preview[:300].replace("```", "` ` `")
            text = (
                f":gear: `{tool_name}`: `{data.get('tool_input', '')[:200]}`\n"
                f"```\n{short}\n```"
            )
            self._edit(job, msg_ts, text)

    def _on_approval_needed(self, job_id: str, job: dict, data: dict) -> None:
        tool_name = data.get("tool_name", "unknown")
        tool_input = data.get("tool_input", "")
        tool_use_id = data.get("tool_use_id", "")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":lock: *Approval needed* — `{tool_name}`\n"
                        f"`{tool_input[:300]}`"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve_tool",
                        "value": f"{job_id}|{tool_use_id}|{tool_name}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve All"},
                        "action_id": "approve_tool_all",
                        "value": f"{job_id}|{tool_use_id}|{tool_name}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "action_id": "deny_tool",
                        "value": f"{job_id}|{tool_use_id}|{tool_name}",
                    },
                ],
            },
        ]
        self._post_blocks(job, blocks, fallback=f"Approval needed: {tool_name}")

    def _on_progress(self, job_id: str, job: dict, data: dict) -> None:
        message = data.get("message", "")
        iteration = data.get("iteration", "")
        text = f":information_source: {message}"
        if iteration:
            text += f" (iteration {iteration})"
        self._update_status(job, text)

    def _on_completed(self, job_id: str, job: dict, data: dict) -> None:
        status = data.get("status", "completed")
        message = data.get("message", "")
        iterations = data.get("iterations", "?")
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)

        if status == "cancelled":
            text = ":stop_sign: Agent cancelled."
        elif status == "max_iterations":
            text = f":warning: Agent reached max iterations ({iterations})."
        else:
            text = f":white_check_mark: Agent completed in {iterations} iterations."

        if message:
            text += f"\n\n{message[:1500]}"
        if input_tokens or output_tokens:
            text += f"\n_Tokens: {input_tokens:,} in / {output_tokens:,} out_"

        self._post(job, text)
        # Clear the status message
        job["status_ts"] = None

    def _on_failed(self, job_id: str, job: dict, data: dict) -> None:
        error = data.get("error", "Unknown error")
        self._post(job, f":x: Agent failed: {error[:500]}")

    def _on_approval_timeout(self, job_id: str, job: dict, data: dict) -> None:
        tool_name = data.get("tool_name", "unknown")
        timeout = data.get("timeout", "?")
        text = (
            f":hourglass: `{tool_name}` — approval timed out"
            f" after {timeout}s, denied automatically"
        )
        self._post(job, text)

    def _on_token_usage(self, job_id: str, job: dict, data: dict) -> None:
        # Silently track, no Slack post needed
        pass

    # ------------------------------------------------------------------
    # Slack helpers
    # ------------------------------------------------------------------

    def _post(self, job: dict, text: str) -> str | None:
        """Post a new message to the job's thread. Returns the message ts."""
        try:
            resp = self.client.chat_postMessage(
                channel=job["channel_id"],
                thread_ts=job["thread_ts"],
                text=text,
            )
            return resp.get("ts") or resp.data.get("ts")
        except Exception:
            log.exception("Failed to post to Slack")
            return None

    def _post_blocks(self, job: dict, blocks: list, fallback: str) -> str | None:
        """Post a Block Kit message."""
        try:
            resp = self.client.chat_postMessage(
                channel=job["channel_id"],
                thread_ts=job["thread_ts"],
                text=fallback,
                blocks=blocks,
            )
            return resp.get("ts") or resp.data.get("ts")
        except Exception:
            log.exception("Failed to post blocks to Slack")
            return None

    def _edit(self, job: dict, ts: str, text: str) -> None:
        """Edit an existing message."""
        try:
            self.client.chat_update(
                channel=job["channel_id"],
                ts=ts,
                text=text,
            )
        except Exception:
            log.debug("Failed to edit Slack message")

    def _update_status(self, job: dict, text: str) -> None:
        """Update or create the rolling status message."""
        if job["status_ts"]:
            self._edit(job, job["status_ts"], text)
        else:
            ts = self._post(job, text)
            if ts:
                job["status_ts"] = ts
