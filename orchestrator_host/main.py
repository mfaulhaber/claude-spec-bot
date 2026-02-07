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

    # Create Slack app with callback
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from orchestrator_host.slack_bot import SlackCallback, create_slack_app

    # Build the queue with Slack callback wired in after app creation
    queue = JobQueue()
    slack_app = create_slack_app(queue)

    # Wire up the callback with the Slack client
    queue.callback = SlackCallback(client=slack_app.client)

    log.info("Connecting to Slack via Socket Mode...")
    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


if __name__ == "__main__":
    main()
