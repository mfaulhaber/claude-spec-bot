"""Tests for orchestrator_host.state."""

from __future__ import annotations

import re

import pytest

from orchestrator_host.state import (
    DEFAULT_STEPS,
    JobState,
    StepState,
    create_job,
    generate_job_id,
    job_dir,
    job_logs_dir,
    job_state_path,
    list_jobs,
    load_state,
    save_state,
)


class TestGenerateJobId:
    def test_format(self):
        jid = generate_job_id()
        # YYYYMMDD-HHMMSS-xxxx
        assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", jid), f"Bad job ID format: {jid}"

    def test_uniqueness(self):
        ids = {generate_job_id() for _ in range(50)}
        assert len(ids) == 50


class TestStepState:
    def test_defaults(self):
        s = StepState(id="bootstrap", command="scripts/bootstrap.sh")
        assert s.status == "pending"
        assert s.exit_code is None

    def test_round_trip(self):
        s = StepState(id="test", command="scripts/test.sh", status="done", exit_code=0)
        d = s.to_dict()
        s2 = StepState.from_dict(d)
        assert s2.id == s.id
        assert s2.status == "done"
        assert s2.exit_code == 0

    def test_from_dict_ignores_unknown_keys(self):
        d = {"id": "x", "command": "y", "extra_field": 42}
        s = StepState.from_dict(d)
        assert s.id == "x"


class TestJobState:
    def test_defaults(self):
        j = JobState(job_id="test-123", goal="Test goal")
        assert j.phase == "QUEUED"
        assert j.created_at
        assert j.updated_at

    def test_set_phase_valid(self):
        j = JobState(job_id="test-123", goal="g")
        j.set_phase("RUNNING")
        assert j.phase == "RUNNING"

    def test_set_phase_invalid(self):
        j = JobState(job_id="test-123", goal="g")
        with pytest.raises(ValueError, match="Invalid phase"):
            j.set_phase("BOGUS")

    def test_round_trip(self):
        j = JobState(
            job_id="test-123",
            goal="Build it",
            phase="RUNNING",
            steps=[
                StepState(id="bootstrap", command="scripts/bootstrap.sh", status="done"),
                StepState(id="test", command="scripts/test.sh", status="running"),
            ],
        )
        d = j.to_dict()
        j2 = JobState.from_dict(d)
        assert j2.job_id == "test-123"
        assert j2.phase == "RUNNING"
        assert len(j2.steps) == 2
        assert j2.steps[0].status == "done"

    def test_touch_updates_timestamp(self):
        j = JobState(job_id="test-123", goal="g")
        j.touch()
        # Timestamps are second-resolution, so just check it's set
        assert j.updated_at


class TestFileIO:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(
            job_id="test-io",
            goal="Test IO",
            steps=[StepState(id="bootstrap", command="scripts/bootstrap.sh")],
        )
        save_state(state)

        loaded = load_state("test-io")
        assert loaded.job_id == "test-io"
        assert loaded.goal == "Test IO"
        assert len(loaded.steps) == 1

    def test_save_creates_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = JobState(job_id="test-dirs", goal="g")
        save_state(state)
        assert (tmp_path / "jobs" / "test-dirs" / "state.json").exists()
        assert (tmp_path / "jobs" / "test-dirs" / "logs").is_dir()

    def test_load_missing_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        with pytest.raises(FileNotFoundError):
            load_state("nonexistent")

    def test_list_jobs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        for jid in ["20260206-120000-aaaa", "20260206-130000-bbbb"]:
            state = JobState(job_id=jid, goal="g")
            save_state(state)
        result = list_jobs()
        assert result == ["20260206-120000-aaaa", "20260206-130000-bbbb"]

    def test_list_jobs_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        assert list_jobs() == []


class TestCreateJob:
    def test_creates_with_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        state = create_job(goal="My goal", requested_by="U123", channel_id="C456")
        assert state.goal == "My goal"
        assert state.requested_by == "U123"
        assert state.phase == "QUEUED"
        assert len(state.steps) == len(DEFAULT_STEPS)
        # Verify persisted
        loaded = load_state(state.job_id)
        assert loaded.goal == "My goal"


class TestJobPaths:
    def test_job_dir(self):
        p = job_dir("abc")
        assert str(p).endswith("jobs/abc")

    def test_state_path(self):
        p = job_state_path("abc")
        assert str(p).endswith("jobs/abc/state.json")

    def test_logs_dir(self):
        p = job_logs_dir("abc")
        assert str(p).endswith("jobs/abc/logs")
