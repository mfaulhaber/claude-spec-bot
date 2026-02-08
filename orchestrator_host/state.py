"""Job state management: dataclasses, ID generation, file I/O with locking."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RUNNER_DIR = Path("runner")
JOBS_DIR = RUNNER_DIR / "jobs"

# --- ID generation ---


def generate_job_id() -> str:
    """Generate a human-readable, sortable job ID: YYYYMMDD-HHMMSS-xxxx."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    suffix = os.urandom(2).hex()
    return f"{stamp}-{suffix}"


# --- Data model ---

VALID_PHASES = (
    "QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_INPUT", "BLOCKED",
    "DONE", "FAILED", "CANCELLED",
)


@dataclass
class JobState:
    job_id: str
    goal: str
    phase: str = "QUEUED"
    requested_by: str = ""
    channel_id: str = ""
    thread_ts: str = ""
    original_message_ts: str = ""
    created_at: str = ""
    updated_at: str = ""
    blockers: list[str] = field(default_factory=list)
    error: str | None = None
    # Agent-mode fields
    model: str = "claude-sonnet-4-5-20250929"
    agent_iteration: int = 0
    max_turns: int = 200
    approved_tools: list[str] = field(default_factory=list)
    callback_url: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self):
        now = _utcnow_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> JobState:
        known = {k for k in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = _utcnow_iso()

    def set_phase(self, phase: str) -> None:
        if phase not in VALID_PHASES:
            raise ValueError(f"Invalid phase {phase!r}, must be one of {VALID_PHASES}")
        self.phase = phase
        self.touch()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- File paths ---


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def job_state_path(job_id: str) -> Path:
    return job_dir(job_id) / "state.json"


def job_logs_dir(job_id: str) -> Path:
    return job_dir(job_id) / "logs"


def job_lock_path(job_id: str) -> Path:
    return job_dir(job_id) / "state.json.lock"


# --- File I/O with locking ---


def ensure_job_dirs(job_id: str) -> None:
    """Create the job directory tree."""
    job_logs_dir(job_id).mkdir(parents=True, exist_ok=True)


def save_state(state: JobState) -> None:
    """Atomically write job state to disk with file locking."""
    ensure_job_dirs(state.job_id)
    state.touch()
    lock = job_lock_path(state.job_id)
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            tmp = job_state_path(state.job_id).with_suffix(".tmp")
            tmp.write_text(json.dumps(state.to_dict(), indent=2) + "\n")
            tmp.replace(job_state_path(state.job_id))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def load_state(job_id: str) -> JobState:
    """Read job state from disk with a shared lock."""
    lock = job_lock_path(job_id)
    path = job_state_path(job_id)
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_SH)
        try:
            data = json.loads(path.read_text())
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return JobState.from_dict(data)


def list_jobs() -> list[str]:
    """Return sorted list of job IDs that have a state.json."""
    if not JOBS_DIR.exists():
        return []
    ids = []
    for entry in JOBS_DIR.iterdir():
        if entry.is_dir() and (entry / "state.json").exists():
            ids.append(entry.name)
    return sorted(ids)


def create_job(
    goal: str,
    requested_by: str = "",
    channel_id: str = "",
    model: str = "claude-sonnet-4-5-20250929",
) -> JobState:
    """Create a new job for the agent."""
    job_id = generate_job_id()
    state = JobState(
        job_id=job_id,
        goal=goal,
        requested_by=requested_by,
        channel_id=channel_id,
        model=model,
    )
    save_state(state)
    return state
