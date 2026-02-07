"""HTTP callback client for posting events to the orchestrator."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

JOBS_DIR = Path("/runner/jobs")
EVENT_TIMEOUT = 10  # seconds


class CallbackClient:
    """Posts events to the orchestrator's callback endpoint.

    Events are also appended to a local JSONL file as a fallback log.
    Delivery is best-effort: failures are logged but do not crash the agent.
    """

    def __init__(self, callback_url: str, job_id: str):
        self.callback_url = callback_url
        self.job_id = job_id
        self._events_path = JOBS_DIR / job_id / "events.jsonl"
        self._events_path.parent.mkdir(parents=True, exist_ok=True)

    def post_event(self, event_type: str, data: dict | None = None) -> None:
        """Post an event to the orchestrator callback URL."""
        payload = {
            "job_id": self.job_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        # Always log locally
        self._append_local(payload)

        if not self.callback_url:
            return

        try:
            with httpx.Client(timeout=EVENT_TIMEOUT) as client:
                resp = client.post(self.callback_url, json=payload)
                if resp.status_code >= 400:
                    log.warning(
                        "Callback POST returned %d for event %s",
                        resp.status_code, event_type,
                    )
        except Exception:
            log.debug("Failed to POST event %s to %s", event_type, self.callback_url)

    def _append_local(self, payload: dict) -> None:
        """Append an event to the local JSONL log."""
        try:
            with open(self._events_path, "a") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception:
            log.debug("Failed to write event to %s", self._events_path)


class NullCallbackClient:
    """No-op callback for testing."""

    def __init__(self):
        self.events: list[dict] = []

    def post_event(self, event_type: str, data: dict | None = None) -> None:
        self.events.append({"event_type": event_type, "data": data or {}})
