"""Tests for poc.handler — HTTP API endpoints."""

from __future__ import annotations

import json
from http.server import HTTPServer
from threading import Thread
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen

from poc.handler import RunnerHandler


class TestHealthEndpoint:
    def test_get_health(self):
        RunnerHandler.sessions = {}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with urlopen(f"http://127.0.0.1:{port}/health") as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert data["service"] == "poc-runner"
        server.server_close()

    def test_get_root_backward_compat(self):
        RunnerHandler.sessions = {}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with urlopen(f"http://127.0.0.1:{port}/") as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        server.server_close()


class TestStartEndpoint:
    @patch("poc.handler.AgentSession")
    def test_start_job(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session.model = "claude-sonnet-4-5-20250929"
        mock_session_cls.return_value = mock_session

        RunnerHandler.sessions = {}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        body = json.dumps({"goal": "Fix the tests", "callback_url": "http://cb:8001/events"}).encode()
        req = Request(
            f"http://127.0.0.1:{port}/jobs/test-1/start",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            data = json.loads(resp.read())

        assert data["status"] == "started"
        assert data["job_id"] == "test-1"
        mock_session.start.assert_called_once()
        server.server_close()

    def test_start_missing_goal(self):
        RunnerHandler.sessions = {}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        body = json.dumps({}).encode()
        req = Request(
            f"http://127.0.0.1:{port}/jobs/test-1/start",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(req)
            assert False, "Should have raised"
        except Exception as e:
            assert "400" in str(e)
        server.server_close()


class TestStatusEndpoint:
    def test_status_existing_session(self):
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session.iteration = 5
        mock_session.max_turns = 200
        mock_session.model = "claude-sonnet-4-5-20250929"
        mock_session.result_text = ""
        mock_session.pending_approval = None

        RunnerHandler.sessions = {"test-1": mock_session}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        with urlopen(f"http://127.0.0.1:{port}/jobs/test-1/status") as resp:
            data = json.loads(resp.read())

        assert data["status"] == "running"
        assert data["iteration"] == 5
        server.server_close()

    def test_status_not_found(self):
        RunnerHandler.sessions = {}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            urlopen(f"http://127.0.0.1:{port}/jobs/nope/status")
            assert False, "Should have raised"
        except Exception as e:
            assert "404" in str(e)
        server.server_close()


class TestApproveEndpoint:
    def test_approve(self):
        mock_session = MagicMock()
        mock_session.approve.return_value = True

        RunnerHandler.sessions = {"test-1": mock_session}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        body = json.dumps({"tool_use_id": "tu-1", "approved": True}).encode()
        req = Request(
            f"http://127.0.0.1:{port}/jobs/test-1/approve",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            data = json.loads(resp.read())

        assert data["status"] == "ok"
        mock_session.approve.assert_called_once()
        server.server_close()


class TestCancelEndpoint:
    def test_cancel(self):
        mock_session = MagicMock()

        RunnerHandler.sessions = {"test-1": mock_session}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        body = json.dumps({}).encode()
        req = Request(
            f"http://127.0.0.1:{port}/jobs/test-1/cancel",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            data = json.loads(resp.read())

        assert data["status"] == "cancel_requested"
        mock_session.cancel.assert_called_once()
        server.server_close()


class TestMessageEndpoint:
    def test_send_message(self):
        mock_session = MagicMock()

        RunnerHandler.sessions = {"test-1": mock_session}
        server = HTTPServer(("127.0.0.1", 0), RunnerHandler)
        port = server.server_address[1]
        thread = Thread(target=server.handle_request, daemon=True)
        thread.start()

        body = json.dumps({"message": "do something else"}).encode()
        req = Request(
            f"http://127.0.0.1:{port}/jobs/test-1/message",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            data = json.loads(resp.read())

        assert data["status"] == "message_added"
        mock_session.add_message.assert_called_once_with("do something else")
        server.server_close()
