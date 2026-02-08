"""Entry point for the Slack-controlled orchestrator."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load orchestrator .env from the same directory as this file
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path)

log = logging.getLogger(__name__)


def check_prerequisites() -> list[str]:
    """Check that required tools and env vars are available."""
    errors = []

    # Docker
    if not shutil.which("docker"):
        errors.append("'docker' not found on PATH")

    # Docker Compose (try v2 plugin syntax)
    if shutil.which("docker"):
        import subprocess

        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            errors.append("'docker compose' not available (need Docker Compose v2)")

    # Slack tokens
    if not os.environ.get("SLACK_BOT_TOKEN"):
        errors.append("SLACK_BOT_TOKEN not set")
    if not os.environ.get("SLACK_APP_TOKEN"):
        errors.append("SLACK_APP_TOKEN not set")

    return errors


def main() -> None:
    """Run the orchestrator."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    logging.getLogger("slack_sdk").setLevel(logging.INFO)

    log.info("POC Orchestrator starting...")

    # Prerequisite checks
    errors = check_prerequisites()
    if errors:
        for e in errors:
            log.error("Prerequisite failed: %s", e)
        sys.exit(1)

    # Recover stale jobs from previous crash
    from orchestrator_host.jobs import JobQueue, recover_stale_jobs

    recovered = recover_stale_jobs()
    if recovered:
        log.warning("Recovered %d stale jobs: %s", len(recovered), recovered)

    # Create Slack app with all components wired together
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from orchestrator_host.approvals import ApprovalManager
    from orchestrator_host.callback_server import start_callback_server
    from orchestrator_host.progress import SlackProgressReporter
    from orchestrator_host.slack_bot import SlackCallback, create_slack_app
    from orchestrator_host.state import load_state, save_state

    # Build queue with null callback (will be replaced after app creation)
    queue = JobQueue()

    # We need the Slack client first — create a temporary app to get it
    # Then rebuild with all components wired
    from slack_bolt import App

    temp_app = App(token=os.environ["SLACK_BOT_TOKEN"])
    slack_client = temp_app.client

    # Create components
    progress_reporter = SlackProgressReporter(client=slack_client)
    approval_manager = ApprovalManager(slack_client=slack_client)

    # Wire callback server → progress reporter + approval manager + queue
    def handle_callback_event(event: dict):
        """Dispatch runner callback events."""
        event_type = event.get("event_type", "")
        job_id = event.get("job_id", "")
        data = event.get("data", {})

        # Forward to progress reporter for Slack updates
        progress_reporter.handle_event(event)

        # Track approval requests
        if event_type == "approval_needed":
            job = progress_reporter._jobs.get(job_id)
            if job:
                approval_manager.register_pending(
                    job_id=job_id,
                    tool_use_id=data.get("tool_use_id", ""),
                    tool_name=data.get("tool_name", ""),
                    channel_id=job["channel_id"],
                    thread_ts=job["thread_ts"],
                )

        # Clear pending approval on timeout
        if event_type == "approval_timeout":
            approval_manager.clear_job(job_id)

        # Update job state for waiting_input
        if event_type == "waiting_input":
            try:
                state = load_state(job_id)
                state.set_phase("WAITING_INPUT")
                save_state(state)
            except Exception:
                log.exception("Failed to update state for waiting_input: %s", job_id)

        # Mark job as completed in the queue
        if event_type in ("completed", "failed", "session_ended"):
            queue.mark_completed(job_id)

    # Start callback server on port 8001
    start_callback_server(handle_callback_event)
    log.info("Callback server running on port 8001")

    # Create the Slack app with all components
    slack_app = create_slack_app(
        queue=queue,
        progress_reporter=progress_reporter,
        approval_manager=approval_manager,
    )

    # Wire up the lifecycle callback with the Slack client
    queue.callback = SlackCallback(client=slack_app.client)

    log.info("Connecting to Slack via Socket Mode...")
    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


if __name__ == "__main__":
    main()
