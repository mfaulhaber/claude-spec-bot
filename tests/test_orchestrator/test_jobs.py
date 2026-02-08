"""Tests for orchestrator_host.jobs (agent-mode queue)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from orchestrator_host.jobs import JobQueue, NullCallback, recover_stale_jobs
from orchestrator_host.state import JobState, create_job, load_state, save_state


class TestNullCallback:
    def test_all_methods_callable(self):
        cb = NullCallback()
        state = JobState(job_id="x", goal="g")
        cb.on_job_started(state)
        cb.on_job_done(state)
        cb.on_job_failed(state)
        cb.on_job_cancelled(state)


class TestJobQueue:
    @patch("orchestrator_host.jobs.start_agent_job")
    def test_enqueue_starts_agent(self, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"status": "started", "job_id": "test-1"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)
        state = create_job(goal="test agent")

        started_event = threading.Event()
        callback.on_job_started.side_effect = lambda s: started_event.set()

        queue.enqueue(state.job_id)
        assert started_event.wait(timeout=10)

        final = load_state(state.job_id)
        assert final.phase == "RUNNING"
        mock_start.assert_called_once()

    @patch("orchestrator_host.jobs.start_agent_job")
    def test_enqueue_handles_runner_error(self, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"error": "Runner unreachable", "status": "failed"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)
        state = create_job(goal="fail test")

        failed_event = threading.Event()
        callback.on_job_failed.side_effect = lambda s: failed_event.set()

        queue.enqueue(state.job_id)
        assert failed_event.wait(timeout=10)

        final = load_state(state.job_id)
        assert final.phase == "FAILED"
        assert "Runner unreachable" in final.error

    @patch("orchestrator_host.jobs.start_agent_job")
    @patch("orchestrator_host.jobs.cancel_agent_job")
    def test_cancel_queued_job(self, mock_cancel, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")

        # Make first job block to allow queuing a second
        start_event = threading.Event()

        def slow_start(*args, **kwargs):
            start_event.wait(timeout=10)
            return {"status": "started"}

        mock_start.side_effect = slow_start

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
        start_event.set()

    @patch("orchestrator_host.jobs.start_agent_job")
    @patch("orchestrator_host.jobs.cancel_agent_job")
    def test_cancel_running_job(self, mock_cancel, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"status": "started"}
        mock_cancel.return_value = {"status": "cancel_requested"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)

        started_event = threading.Event()
        callback.on_job_started.side_effect = lambda s: started_event.set()

        job = create_job(goal="cancel me")
        queue.enqueue(job.job_id)
        assert started_event.wait(timeout=10)

        assert queue.cancel(job.job_id)
        final = load_state(job.job_id)
        assert final.phase == "CANCELLED"
        callback.on_job_cancelled.assert_called_once()

    @patch("orchestrator_host.jobs.start_agent_job")
    def test_mark_completed_starts_next(self, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"status": "started"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)

        job1 = create_job(goal="job1")
        job2 = create_job(goal="job2")

        started_event = threading.Event()
        started_count = {"n": 0}

        def on_started(s):
            started_count["n"] += 1
            if started_count["n"] == 1:
                started_event.set()

        callback.on_job_started.side_effect = on_started

        queue.enqueue(job1.job_id)
        assert started_event.wait(timeout=10)
        queue.enqueue(job2.job_id)

        # Mark job1 as completed — should trigger job2
        second_started = threading.Event()
        callback.on_job_started.side_effect = lambda s: second_started.set()
        queue.mark_completed(job1.job_id)
        assert second_started.wait(timeout=10)

        assert mock_start.call_count == 2


class TestEndSession:
    @patch("orchestrator_host.jobs.start_agent_job")
    @patch("orchestrator_host.jobs.end_agent_job")
    def test_end_session(self, mock_end, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"status": "started"}
        mock_end.return_value = {"status": "end_requested"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)

        started_event = threading.Event()
        callback.on_job_started.side_effect = lambda s: started_event.set()

        job = create_job(goal="persistent task")
        queue.enqueue(job.job_id)
        assert started_event.wait(timeout=10)

        queue.end_session(job.job_id)

        final = load_state(job.job_id)
        assert final.phase == "DONE"
        mock_end.assert_called_once_with(job.job_id)
        callback.on_job_done.assert_called_once()
        assert queue.current_job_id is None


class TestHasActiveSession:
    @patch("orchestrator_host.jobs.start_agent_job")
    def test_no_active_session(self, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        queue = JobQueue()
        assert not queue.has_active_session()

    @patch("orchestrator_host.jobs.start_agent_job")
    def test_active_session(self, mock_start, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_start.return_value = {"status": "started"}

        callback = MagicMock()
        queue = JobQueue(callback=callback)

        started_event = threading.Event()
        callback.on_job_started.side_effect = lambda s: started_event.set()

        job = create_job(goal="active task")
        queue.enqueue(job.job_id)
        assert started_event.wait(timeout=10)

        assert queue.has_active_session()


class TestRecoverStaleJobs:
    def test_recovers_running_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="stale-1", goal="stale", phase="RUNNING")
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == ["stale-1"]

        final = load_state("stale-1")
        assert final.phase == "FAILED"
        assert "restarted" in final.error

    def test_recovers_waiting_approval_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="stale-2", goal="stale", phase="WAITING_APPROVAL")
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == ["stale-2"]

        final = load_state("stale-2")
        assert final.phase == "FAILED"

    def test_recovers_waiting_input_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="stale-3", goal="stale", phase="WAITING_INPUT")
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == ["stale-3"]

        final = load_state("stale-3")
        assert final.phase == "FAILED"

    def test_ignores_done_jobs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="done-1", goal="done", phase="DONE")
        save_state(state)

        recovered = recover_stale_jobs()
        assert recovered == []
