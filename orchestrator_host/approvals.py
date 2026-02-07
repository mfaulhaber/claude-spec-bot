"""Approval manager — tracks pending approvals and handles Slack actions."""

from __future__ import annotations

import logging

from orchestrator_host.docker_exec import send_approval

log = logging.getLogger(__name__)


class ApprovalManager:
    """Manages pending tool approvals from the runner.

    When the runner pauses for approval, the orchestrator tracks it here.
    When the user clicks a Slack button or replies in the thread, the
    manager sends the decision to the runner.
    """

    def __init__(self, slack_client=None):
        self.slack_client = slack_client
        # job_id -> {tool_use_id, tool_name, channel_id, thread_ts, message_ts}
        self._pending: dict[str, dict] = {}

    def register_pending(
        self,
        job_id: str,
        tool_use_id: str,
        tool_name: str,
        channel_id: str,
        thread_ts: str,
    ) -> None:
        """Record a pending approval from the runner."""
        self._pending[job_id] = {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        }

    def get_pending(self, job_id: str) -> dict | None:
        """Get the pending approval for a job, if any."""
        return self._pending.get(job_id)

    def handle_approve(
        self, job_id: str, tool_use_id: str, auto_all: bool = False, message_ts: str = ""
    ) -> bool:
        """Approve a pending tool call. Returns True if found and processed."""
        pending = self._pending.pop(job_id, None)
        if not pending or pending["tool_use_id"] != tool_use_id:
            return False

        send_approval(job_id, tool_use_id, approved=True, auto_approve_tool=auto_all)
        self._update_slack_message(pending, approved=True, auto_all=auto_all, message_ts=message_ts)
        return True

    def handle_deny(self, job_id: str, tool_use_id: str, message_ts: str = "") -> bool:
        """Deny a pending tool call. Returns True if found and processed."""
        pending = self._pending.pop(job_id, None)
        if not pending or pending["tool_use_id"] != tool_use_id:
            return False

        send_approval(job_id, tool_use_id, approved=False)
        self._update_slack_message(pending, approved=False, message_ts=message_ts)
        return True

    def handle_text_reply(self, job_id: str, text: str) -> bool:
        """Handle a text reply in a job thread (yes/approve or no/deny)."""
        pending = self._pending.get(job_id)
        if not pending:
            return False

        normalized = text.strip().lower()
        if normalized in ("yes", "y", "approve", "ok", "go"):
            return self.handle_approve(job_id, pending["tool_use_id"])
        elif normalized in ("no", "n", "deny", "reject", "stop"):
            return self.handle_deny(job_id, pending["tool_use_id"])
        return False

    def clear_job(self, job_id: str) -> None:
        """Remove any pending approval for a job."""
        self._pending.pop(job_id, None)

    def _update_slack_message(
        self, pending: dict, approved: bool, auto_all: bool = False, message_ts: str = ""
    ) -> None:
        """Replace approval buttons with the decision text.

        If *message_ts* is provided (from the Slack action payload), the
        original button message is edited in place via ``chat_update``.
        Otherwise falls back to posting a new follow-up message.
        """
        if not self.slack_client:
            return
        tool_name = pending["tool_name"]
        if approved:
            suffix = " (all future calls)" if auto_all else ""
            text = f":white_check_mark: `{tool_name}` — *Approved*{suffix}"
        else:
            text = f":no_entry_sign: `{tool_name}` — *Denied*"

        try:
            if message_ts:
                self.slack_client.chat_update(
                    channel=pending["channel_id"],
                    ts=message_ts,
                    text=text,
                    blocks=[],
                )
            else:
                self.slack_client.chat_postMessage(
                    channel=pending["channel_id"],
                    thread_ts=pending["thread_ts"],
                    text=text,
                )
        except Exception:
            log.debug("Failed to update approval decision in Slack")
