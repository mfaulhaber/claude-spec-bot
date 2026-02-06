"""Docker Compose subprocess execution for the POC runner."""

from __future__ import annotations

import logging
import signal
import subprocess
from dataclasses import dataclass

from orchestrator_host.state import job_logs_dir

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    exit_code: int
    was_cancelled: bool = False


def container_name(job_id: str, step_id: str) -> str:
    """Return the Docker container name for a job step."""
    return f"poc-job-{job_id}-{step_id}"


def build_docker_command(job_id: str, step_id: str, command: str) -> list[str]:
    """Build the docker compose run command list."""
    name = container_name(job_id, step_id)
    log_path = f"/runner/jobs/{job_id}/logs/{step_id}.log"
    shell_cmd = f"{command} |& tee {log_path}"
    return [
        "docker",
        "compose",
        "run",
        "--rm",
        "--name",
        name,
        "runner",
        "bash",
        "-lc",
        shell_cmd,
    ]


def run_in_runner(
    job_id: str,
    step_id: str,
    command: str,
    *,
    timeout: int | None = 600,
) -> RunResult:
    """Execute a command inside the Docker runner service.

    Returns a RunResult with exit_code and cancellation flag.
    """
    cmd = build_docker_command(job_id, step_id, command)
    log_dir = job_logs_dir(job_id)
    log_dir.mkdir(parents=True, exist_ok=True)

    log.info("Running step %s for job %s: %s", step_id, job_id, " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            log.debug("stdout [%s/%s]: %s", job_id, step_id, proc.stdout[-500:])
        if proc.stderr:
            log.debug("stderr [%s/%s]: %s", job_id, step_id, proc.stderr[-500:])
        return RunResult(exit_code=proc.returncode)
    except subprocess.TimeoutExpired:
        log.warning("Step %s for job %s timed out after %ds", step_id, job_id, timeout)
        cancel_job_container(job_id, step_id)
        return RunResult(exit_code=-1, was_cancelled=True)


def cancel_job_container(job_id: str, step_id: str) -> None:
    """Stop a running container for a job step (best-effort)."""
    name = container_name(job_id, step_id)
    log.info("Stopping container %s", name)
    try:
        subprocess.run(
            ["docker", "stop", "-t", "5", name],
            timeout=15,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to stop container %s gracefully, trying kill", name)
        try:
            subprocess.run(
                ["docker", "kill", name],
                timeout=10,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            log.error("Failed to kill container %s", name)


def cancel_subprocess(proc: subprocess.Popen, job_id: str, step_id: str) -> None:
    """Terminate a subprocess and its container."""
    log.info("Cancelling subprocess for %s/%s (pid=%s)", job_id, step_id, proc.pid)
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass
    cancel_job_container(job_id, step_id)
