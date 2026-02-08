"""HTTP client for communicating with the runner agent."""

from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

RUNNER_URL = "http://localhost:8000"


def start_agent_job(
    job_id: str,
    goal: str,
    callback_url: str,
    *,
    model: str = "",
    max_turns: int = 200,
    timeout: int = 30,
) -> dict:
    """Start an agent session on the runner. POSTs to /jobs/{job_id}/start."""
    payload: dict = {
        "goal": goal,
        "callback_url": callback_url,
    }
    if model:
        payload["model"] = model
    if max_turns != 200:
        payload["max_turns"] = max_turns

    return _post(f"/jobs/{job_id}/start", payload, timeout=timeout)


def send_approval(
    job_id: str,
    tool_use_id: str,
    *,
    approved: bool = True,
    auto_approve_tool: bool = False,
    timeout: int = 10,
) -> dict:
    """Send an approval/denial to the runner. POSTs to /jobs/{job_id}/approve."""
    payload = {
        "tool_use_id": tool_use_id,
        "approved": approved,
        "auto_approve_tool": auto_approve_tool,
    }
    return _post(f"/jobs/{job_id}/approve", payload, timeout=timeout)


def send_message(job_id: str, message: str, *, timeout: int = 10) -> dict:
    """Send a follow-up message to the agent. POSTs to /jobs/{job_id}/message."""
    return _post(f"/jobs/{job_id}/message", {"message": message}, timeout=timeout)


def cancel_agent_job(job_id: str, *, timeout: int = 10) -> dict:
    """Cancel a running agent session. POSTs to /jobs/{job_id}/cancel."""
    return _post(f"/jobs/{job_id}/cancel", {}, timeout=timeout)


def end_agent_job(job_id: str, *, timeout: int = 10) -> dict:
    """Gracefully end a persistent agent session. POSTs to /jobs/{job_id}/end."""
    return _post(f"/jobs/{job_id}/end", {}, timeout=timeout)


def get_agent_status(job_id: str, *, timeout: int = 10) -> dict:
    """Get agent session status. GETs /jobs/{job_id}/status."""
    return _get(f"/jobs/{job_id}/status", timeout=timeout)


def check_runner_health(*, timeout: int = 5) -> dict:
    """Check if the runner is healthy. GETs /health."""
    return _get("/health", timeout=timeout)


# ---------------------------------------------------------------------------
# Internal HTTP helpers
# ---------------------------------------------------------------------------


def _post(path: str, payload: dict, *, timeout: int = 30) -> dict:
    """POST JSON to the runner."""
    url = f"{RUNNER_URL}{path}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    log.debug("POST %s", url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        log.exception("Failed to POST to %s", url)
        return {"error": "Runner unreachable", "status": "failed"}


def _get(path: str, *, timeout: int = 10) -> dict:
    """GET JSON from the runner."""
    url = f"{RUNNER_URL}{path}"
    req = urllib.request.Request(url, method="GET")
    log.debug("GET %s", url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        log.exception("Failed to GET %s", url)
        return {"error": "Runner unreachable", "status": "failed"}
