"""Tests for orchestrator_host.docker_exec (mocked subprocess)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from orchestrator_host.docker_exec import (
    build_docker_command,
    cancel_job_container,
    container_name,
    run_in_runner,
)


class TestContainerName:
    def test_format(self):
        assert container_name("abc-123", "bootstrap") == "poc-job-abc-123-bootstrap"


class TestBuildDockerCommand:
    def test_includes_name_and_tee(self):
        cmd = build_docker_command("job-1", "test", "scripts/test.sh")
        assert cmd[:4] == ["docker", "compose", "run", "--rm"]
        assert "--name" in cmd
        assert "poc-job-job-1-test" in cmd
        assert "runner" in cmd
        assert "bash" in cmd
        # Check shell command includes tee
        shell_cmd = cmd[-1]
        assert "scripts/test.sh" in shell_cmd
        assert "tee" in shell_cmd
        assert "/runner/jobs/job-1/logs/test.log" in shell_cmd


class TestRunInRunner:
    @patch("orchestrator_host.docker_exec.subprocess.run")
    def test_success(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = run_in_runner("job-1", "bootstrap", "scripts/bootstrap.sh")
        assert result.exit_code == 0
        assert not result.was_cancelled
        mock_run.assert_called_once()

    @patch("orchestrator_host.docker_exec.subprocess.run")
    def test_failure(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = run_in_runner("job-1", "bootstrap", "scripts/bootstrap.sh")
        assert result.exit_code == 1
        assert not result.was_cancelled

    @patch("orchestrator_host.docker_exec.cancel_job_container")
    @patch("orchestrator_host.docker_exec.subprocess.run")
    def test_timeout(self, mock_run, mock_cancel, tmp_path, monkeypatch):
        import subprocess

        monkeypatch.setattr("orchestrator_host.state.JOBS_DIR", tmp_path / "jobs")
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=600)
        result = run_in_runner("job-1", "test", "scripts/test.sh")
        assert result.exit_code == -1
        assert result.was_cancelled
        mock_cancel.assert_called_once_with("job-1", "test")


class TestCancelJobContainer:
    @patch("orchestrator_host.docker_exec.subprocess.run")
    def test_stop_called(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        cancel_job_container("job-1", "test")
        args = mock_run.call_args[0][0]
        assert args[:2] == ["docker", "stop"]
        assert "poc-job-job-1-test" in args

    @patch("orchestrator_host.docker_exec.subprocess.run")
    def test_fallback_to_kill(self, mock_run):
        import subprocess

        # First call (stop) times out, second call (kill) succeeds
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="docker", timeout=15),
            MagicMock(returncode=0),
        ]
        cancel_job_container("job-1", "test")
        assert mock_run.call_count == 2
        # Second call should be docker kill
        kill_args = mock_run.call_args_list[1][0][0]
        assert kill_args[:2] == ["docker", "kill"]
