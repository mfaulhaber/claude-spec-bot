"""Tests for orchestrator_host.callback_server."""

from __future__ import annotations

import json
import time
import urllib.request

from orchestrator_host.callback_server import start_callback_server


class TestCallbackServer:
    def test_receives_event(self):
        received = []

        def handler(event):
            received.append(event)

        server = start_callback_server(handler, port=0)
        port = server.server_address[1]
        time.sleep(0.1)  # Let server start

        payload = json.dumps({
            "job_id": "test-1",
            "event_type": "thinking",
            "data": {"iteration": 1},
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/events",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        assert data["status"] == "ok"
        assert len(received) == 1
        assert received[0]["job_id"] == "test-1"
        assert received[0]["event_type"] == "thinking"
        server.shutdown()

    def test_health_endpoint(self):
        server = start_callback_server(lambda e: None, port=0)
        port = server.server_address[1]
        time.sleep(0.1)

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        server.shutdown()

    def test_rejects_non_events_path(self):
        server = start_callback_server(lambda e: None, port=0)
        port = server.server_address[1]
        time.sleep(0.1)

        payload = json.dumps({"test": True}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/wrong",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            assert False, "Should have raised"
        except Exception as e:
            assert "404" in str(e)
        server.shutdown()

    def test_handler_error_doesnt_crash(self):
        def bad_handler(event):
            raise ValueError("boom")

        server = start_callback_server(bad_handler, port=0)
        port = server.server_address[1]
        time.sleep(0.1)

        payload = json.dumps({"job_id": "test-1", "event_type": "test"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/events",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Should still return 200 despite handler error
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        server.shutdown()
