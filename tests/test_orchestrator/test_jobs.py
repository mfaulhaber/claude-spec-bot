"""Tests for orchestrator_host.jobs (mocked docker_exec)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from orchestrator_host.docker_exec import RunResult
from orchestrator_host.jobs import JobQueue, NullCallback, recover_stale_jobs
from orchestrator_host.state import JobState, StepState, create_job, load_state, save_state


class TestNullCallback:
    def test_all_methods_callable(self):
        cb = NullCallback()
        state = JobState(job_id="x", goal="g")
        step = StepState(id="s", command="c")
        cb.on_step_start(state, step)
        cb.on_step_done(state, step)
        cb.on_job_done(state)
        cb.on_job_failed(state, step)
        cb.on_job_blocked(state, [])
        cb.on_job_cancelled(state)


class TestJobQueue:
    @patch("orchestrator_host.jobs.run_in_runner")
    def test_pipeline_success(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_run.return_value = RunResult(exit_code=0)

        callback = MagicMock()
        queue = JobQueue(callback=callback)
        state = create_job(goal="test pipeline")

        done_event = threading.Event()

        def on_done(s):
            done_event.set()

        callback.on_job_done.side_effect = on_done

        queue.enqueue(state.job_id)
        assert done_event.wait(timeout=10)

        final = load_state(state.job_id)
        assert final.phase == "DONE"
        assert all(s.status == "done" for s in final.steps)
        assert callback.on_step_start.call_count == 4
        assert callback.on_step_done.call_count == 4

    @patch("orchestrator_host.jobs.run_in_runner")
    def test_pipeline_failure(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        # First step succeeds, second fails
        mock_run.side_effect = [
            RunResult(exit_code=0),
            RunResult(exit_code=1),
        ]

        callback = MagicMock()
        queue = JobQueue(callback=callback)
        state = create_job(goal="fail test")

        done_event = threading.Event()
        callback.on_job_failed.side_effect = lambda s, step: done_event.set()

        queue.enqueue(state.job_id)
        assert done_event.wait(timeout=10)

        final = load_state(state.job_id)
        assert final.phase == "FAILED"
        assert final.error
        callback.on_job_failed.assert_called_once()

    @patch("orchestrator_host.jobs.run_in_runner")
    def test_cancel_queued_job(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        # Make first job block long enough to queue a second
        run_event = threading.Event()

        def slow_run(*args, **kwargs):
            run_event.wait(timeout=10)
            return RunResult(exit_code=0)

        mock_run.side_effect = slow_run

        callback = MagicMock()
        queue = JobQueue(callback=callback)

        job1 = create_job(goal="job1")
        job2 = create_job(goal="job2")

        queue.enqueue(job1.job_id)
        time.sleep(0.1)  # Let job1 start
        queue.enqueue(job2.job_id)

        # Cancel job2 (queued)
        assert queue.cancel(job2.job_id)
        final2 = load_state(job2.job_id)
        assert final2.phase == "CANCELLED"

        # Unblock job1
        run_event.set()

    @patch("orchestrator_host.jobs.run_in_runner")
    def test_cancel_running_job(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")

        cancel_detected = threading.Event()

        def slow_run(*args, **kwargs):
            # Simulate work that checks for cancellation
            time.sleep(0.5)
            return RunResult(exit_code=0)

        mock_run.side_effect = slow_run

        callback = MagicMock()
        callback.on_job_cancelled.side_effect = lambda s: cancel_detected.set()
        queue = JobQueue(callback=callback)

        job = create_job(goal="cancel me")
        queue.enqueue(job.job_id)
        time.sleep(0.1)  # Let it start

        assert queue.cancel(job.job_id)
        assert cancel_detected.wait(timeout=10)

        final = load_state(job.job_id)
        assert final.phase == "CANCELLED"


class TestRecoverStaleJobs:
    def test_recovers_running_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(
            job_id="stale-1",
            goal="stale",
            phase="RUNNING",
            steps=[StepState(id="test", command="c", status="running")],
        )
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == ["stale-1"]

        final = load_state("stale-1")
        assert final.phase == "FAILED"
        assert "restarted" in final.error
        assert final.steps[0].status == "failed"

    def test_ignores_done_jobs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="done-1", goal="done", phase="DONE")
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == []
