"""Tests for orchestrator_host.docker_exec (HTTP client)."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

from orchestrator_host.docker_exec import (
    cancel_agent_job,
    check_runner_health,
    get_agent_status,
    send_approval,
    send_message,
    start_agent_job,
)


class _MockRunnerHandler(BaseHTTPRequestHandler):
    """Captures requests for test assertions."""

    requests = []

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        self.requests.append({"method": "POST", "path": self.path, "body": body})
        self._respond(200, {"status": "ok"})

    def do_GET(self):
        self.requests.append({"method": "GET", "path": self.path})
        self._respond(200, {"status": "ok", "service": "poc-runner"})

    def _respond(self, code, data):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class TestStartAgentJob:
    def test_posts_to_runner(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = start_agent_job(
                "job-1", "Fix the bug", "http://callback:8001/events",
                model="claude-sonnet-4-5-20250929",
            )

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/jobs/job-1/start"
        assert req["body"]["goal"] == "Fix the bug"
        assert req["body"]["callback_url"] == "http://callback:8001/events"
        server.server_close()

    def test_handles_unreachable(self):
        with patch("orchestrator_host.docker_exec.RUNNER_URL", "http://127.0.0.1:1"):
            result = start_agent_job("job-1", "goal", "http://callback:8001/events", timeout=1)
        assert result.get("error")


class TestSendApproval:
    def test_posts_approval(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = send_approval("job-1", "tu-123", approved=True, auto_approve_tool=True)

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/jobs/job-1/approve"
        assert req["body"]["approved"] is True
        assert req["body"]["auto_approve_tool"] is True
        server.server_close()


class TestSendMessage:
    def test_posts_message(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = send_message("job-1", "do this instead")

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/jobs/job-1/message"
        assert req["body"]["message"] == "do this instead"
        server.server_close()


class TestCancelAgentJob:
    def test_posts_cancel(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = cancel_agent_job("job-1")

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/jobs/job-1/cancel"
        server.server_close()


class TestCheckRunnerHealth:
    def test_gets_health(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = check_runner_health()

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/health"
        server.server_close()


class TestGetAgentStatus:
    def test_gets_status(self):
        _MockRunnerHandler.requests.clear()
        server = HTTPServer(("127.0.0.1", 0), _MockRunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with patch("orchestrator_host.docker_exec.RUNNER_URL", f"http://127.0.0.1:{port}"):
            result = get_agent_status("job-1")

        assert result["status"] == "ok"
        req = _MockRunnerHandler.requests[0]
        assert req["path"] == "/jobs/job-1/status"
        server.server_close()
