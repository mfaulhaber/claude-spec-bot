"""Job queue and pipeline execution."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Protocol

from orchestrator_host.docker_exec import run_in_runner
from orchestrator_host.state import (
    JobState,
    StepState,
    _utcnow_iso,
    list_jobs,
    load_state,
    save_state,
)

log = logging.getLogger(__name__)


class PipelineCallback(Protocol):
    """Callback interface for pipeline progress notifications."""

    def on_step_start(self, state: JobState, step: StepState) -> None: ...
    def on_step_done(self, state: JobState, step: StepState) -> None: ...
    def on_job_done(self, state: JobState) -> None: ...
    def on_job_failed(self, state: JobState, step: StepState) -> None: ...
    def on_job_blocked(self, state: JobState, blockers: list[str]) -> None: ...
    def on_job_cancelled(self, state: JobState) -> None: ...


class NullCallback:
    """No-op callback for testing and standalone use."""

    def on_step_start(self, state: JobState, step: StepState) -> None:
        pass

    def on_step_done(self, state: JobState, step: StepState) -> None:
        pass

    def on_job_done(self, state: JobState) -> None:
        pass

    def on_job_failed(self, state: JobState, step: StepState) -> None:
        pass

    def on_job_blocked(self, state: JobState, blockers: list[str]) -> None:
        pass

    def on_job_cancelled(self, state: JobState) -> None:
        pass


class JobQueue:
    """Single-concurrency job queue with background execution."""

    def __init__(self, callback: PipelineCallback | None = None):
        self._lock = threading.Lock()
        self._queue: deque[str] = deque()
        self._current_job_id: str | None = None
        self._cancel_event = threading.Event()
        self.callback: PipelineCallback = callback or NullCallback()

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
                self._cancel_event.set()
                return True
        return False

    def _start_next(self) -> None:
        """Start the next job from the queue (must hold _lock)."""
        if not self._queue:
            self._current_job_id = None
            return
        job_id = self._queue.popleft()
        self._current_job_id = job_id
        self._cancel_event.clear()
        thread = threading.Thread(
            target=self._run_pipeline,
            args=(job_id,),
            daemon=True,
            name=f"pipeline-{job_id}",
        )
        thread.start()

    def _run_pipeline(self, job_id: str) -> None:
        """Execute the pipeline steps for a job."""
        try:
            state = load_state(job_id)
            state.set_phase("RUNNING")
            save_state(state)

            for step in state.steps:
                if self._cancel_event.is_set():
                    state = load_state(job_id)
                    state.set_phase("CANCELLED")
                    for s in state.steps:
                        if s.status == "pending":
                            s.status = "skipped"
                    save_state(state)
                    self.callback.on_job_cancelled(state)
                    return

                if step.status == "done":
                    continue
                if step.status == "skipped":
                    continue

                # Mark step running
                step.status = "running"
                step.started_at = _utcnow_iso()
                save_state(state)
                self.callback.on_step_start(state, step)

                # Execute
                result = run_in_runner(job_id, step.id, step.command)

                # Check cancellation after execution
                if self._cancel_event.is_set() or result.was_cancelled:
                    state = load_state(job_id)
                    state.set_phase("CANCELLED")
                    for s in state.steps:
                        if s.status in ("pending", "running"):
                            s.status = "skipped"
                    save_state(state)
                    self.callback.on_job_cancelled(state)
                    return

                # Update step result
                step.status = "done" if result.exit_code == 0 else "failed"
                step.exit_code = result.exit_code
                step.finished_at = _utcnow_iso()
                save_state(state)
                self.callback.on_step_done(state, step)

                if result.exit_code != 0:
                    state.set_phase("FAILED")
                    state.error = f"Step '{step.id}' failed with exit code {result.exit_code}"
                    save_state(state)
                    self.callback.on_job_failed(state, step)
                    return

            # All steps done
            state.set_phase("DONE")
            save_state(state)
            self.callback.on_job_done(state)

        except Exception:
            log.exception("Pipeline error for job %s", job_id)
            try:
                state = load_state(job_id)
                state.set_phase("FAILED")
                state.error = "Internal pipeline error"
                save_state(state)
                self.callback.on_job_failed(state, state.steps[0])
            except Exception:
                log.exception("Failed to save error state for job %s", job_id)
        finally:
            with self._lock:
                if self._current_job_id == job_id:
                    self._current_job_id = None
                    self._start_next()


def recover_stale_jobs() -> list[str]:
    """On startup, mark any RUNNING jobs as FAILED (cannot reconnect to subprocess)."""
    recovered = []
    for job_id in list_jobs():
        try:
            state = load_state(job_id)
            if state.phase == "RUNNING":
                log.warning("Recovering stale RUNNING job %s -> FAILED", job_id)
                state.set_phase("FAILED")
                state.error = "Orchestrator restarted while job was running"
                for step in state.steps:
                    if step.status == "running":
                        step.status = "failed"
                save_state(state)
                recovered.append(job_id)
        except Exception:
            log.exception("Error recovering job %s", job_id)
    return recovered
