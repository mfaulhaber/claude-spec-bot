"""Job queue for agent-mode execution.

The queue tracks which job is active (single concurrency) and dispatches
start/cancel requests to the runner via HTTP.  The runner drives the actual
execution; the orchestrator just relays events and manages lifecycle state.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Protocol

from orchestrator_host.docker_exec import cancel_agent_job, start_agent_job
from orchestrator_host.state import (
    JobState,
    list_jobs,
    load_state,
    save_state,
)

log = logging.getLogger(__name__)

CALLBACK_URL = "http://host.docker.internal:8001/events"


class AgentCallback(Protocol):
    """Callback interface for agent job lifecycle notifications."""

    def on_job_started(self, state: JobState) -> None: ...
    def on_job_done(self, state: JobState) -> None: ...
    def on_job_failed(self, state: JobState) -> None: ...
    def on_job_cancelled(self, state: JobState) -> None: ...


class NullCallback:
    """No-op callback for testing and standalone use."""

    def on_job_started(self, state: JobState) -> None:
        pass

    def on_job_done(self, state: JobState) -> None:
        pass

    def on_job_failed(self, state: JobState) -> None:
        pass

    def on_job_cancelled(self, state: JobState) -> None:
        pass


class JobQueue:
    """Single-concurrency job queue that dispatches to the runner agent."""

    def __init__(self, callback: AgentCallback | None = None):
        self._lock = threading.Lock()
        self._queue: deque[str] = deque()
        self._current_job_id: str | None = None
        self.callback: AgentCallback = callback or NullCallback()

    @property
    def current_job_id(self) -> str | None:
        return self._current_job_id

    def enqueue(self, job_id: str) -> int:
        """Add a job to the queue. Returns queue position (0 = running next)."""
        with self._lock:
            self._queue.append(job_id)
            position = len(self._queue) - 1
            if self._current_job_id is None:
                self._start_next()
            return position

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running job. Returns True if found and cancelled."""
        with self._lock:
            # Check queue first
            if job_id in self._queue:
                self._queue.remove(job_id)
                state = load_state(job_id)
                state.set_phase("CANCELLED")
                save_state(state)
                self.callback.on_job_cancelled(state)
                return True
            # Cancel running job
            if self._current_job_id == job_id:
                cancel_agent_job(job_id)
                state = load_state(job_id)
                state.set_phase("CANCELLED")
                save_state(state)
                self.callback.on_job_cancelled(state)
                self._current_job_id = None
                self._start_next()
                return True
        return False

    def mark_completed(self, job_id: str) -> None:
        """Called by the callback server when the runner reports completion."""
        with self._lock:
            if self._current_job_id == job_id:
                self._current_job_id = None
                self._start_next()

    def _start_next(self) -> None:
        """Start the next job from the queue (must hold _lock)."""
        if not self._queue:
            self._current_job_id = None
            return
        job_id = self._queue.popleft()
        self._current_job_id = job_id

        # Start in a background thread so we don't block the lock
        thread = threading.Thread(
            target=self._dispatch_start,
            args=(job_id,),
            daemon=True,
            name=f"job-start-{job_id}",
        )
        thread.start()

    def _dispatch_start(self, job_id: str) -> None:
        """Send the start request to the runner."""
        try:
            state = load_state(job_id)
            state.set_phase("RUNNING")
            save_state(state)

            response = start_agent_job(
                job_id=job_id,
                goal=state.goal,
                callback_url=state.callback_url or CALLBACK_URL,
                model=state.model,
                max_iterations=state.max_iterations,
            )

            if response.get("error"):
                log.error("Failed to start agent for job %s: %s", job_id, response["error"])
                state = load_state(job_id)
                state.set_phase("FAILED")
                state.error = f"Failed to start agent: {response['error']}"
                save_state(state)
                self.callback.on_job_failed(state)
                with self._lock:
                    if self._current_job_id == job_id:
                        self._current_job_id = None
                        self._start_next()
                return

            self.callback.on_job_started(state)

        except Exception:
            log.exception("Error starting job %s", job_id)
            try:
                state = load_state(job_id)
                state.set_phase("FAILED")
                state.error = "Internal error starting job"
                save_state(state)
                self.callback.on_job_failed(state)
            except Exception:
                log.exception("Failed to save error state for job %s", job_id)
            with self._lock:
                if self._current_job_id == job_id:
                    self._current_job_id = None
                    self._start_next()


def recover_stale_jobs() -> list[str]:
    """On startup, mark any RUNNING or WAITING_APPROVAL jobs as FAILED."""
    recovered = []
    for job_id in list_jobs():
        try:
            state = load_state(job_id)
            if state.phase in ("RUNNING", "WAITING_APPROVAL"):
                log.warning("Recovering stale %s job %s -> FAILED", state.phase, job_id)
                state.set_phase("FAILED")
                state.error = "Orchestrator restarted while job was running"
                save_state(state)
                recovered.append(job_id)
        except Exception:
            log.exception("Error recovering job %s", job_id)
    return recovered
