"""HTTP API handler for the POC runner.

Provides endpoints for managing agent sessions:

    GET  /health                  Health check
    POST /jobs/{job_id}/start     Start an agent session
    POST /jobs/{job_id}/approve   Approve/deny a pending tool call
    POST /jobs/{job_id}/message   Send a follow-up message to the agent
    POST /jobs/{job_id}/cancel    Cancel a running session
    GET  /jobs/{job_id}/status    Get session status
"""

from __future__ import annotations

import json
import logging
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

from poc.agent import AgentSession

log = logging.getLogger(__name__)

PORT = 8000

# Route patterns
_ROUTE_HEALTH = re.compile(r"^/health$")
_ROUTE_JOB_START = re.compile(r"^/jobs/(?P<job_id>[^/]+)/start$")
_ROUTE_JOB_APPROVE = re.compile(r"^/jobs/(?P<job_id>[^/]+)/approve$")
_ROUTE_JOB_MESSAGE = re.compile(r"^/jobs/(?P<job_id>[^/]+)/message$")
_ROUTE_JOB_CANCEL = re.compile(r"^/jobs/(?P<job_id>[^/]+)/cancel$")
_ROUTE_JOB_STATUS = re.compile(r"^/jobs/(?P<job_id>[^/]+)/status$")


class RunnerHandler(BaseHTTPRequestHandler):
    """HTTP request handler with path-based routing for agent management."""

    # Class-level session registry (shared across requests)
    sessions: dict[str, AgentSession] = {}

    def do_GET(self):
        path = self.path.split("?")[0]

        # GET /health (also accept GET / for backward compat)
        if _ROUTE_HEALTH.match(path) or path == "/":
            self._respond(200, {"status": "ok", "service": "poc-runner"})
            return

        # GET /jobs/{job_id}/status
        m = _ROUTE_JOB_STATUS.match(path)
        if m:
            self._handle_status(m.group("job_id"))
            return

        self._respond(404, {"error": "Not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        # POST /jobs/{job_id}/start
        m = _ROUTE_JOB_START.match(path)
        if m:
            self._handle_start(m.group("job_id"), body)
            return

        # POST /jobs/{job_id}/approve
        m = _ROUTE_JOB_APPROVE.match(path)
        if m:
            self._handle_approve(m.group("job_id"), body)
            return

        # POST /jobs/{job_id}/message
        m = _ROUTE_JOB_MESSAGE.match(path)
        if m:
            self._handle_message(m.group("job_id"), body)
            return

        # POST /jobs/{job_id}/cancel
        m = _ROUTE_JOB_CANCEL.match(path)
        if m:
            self._handle_cancel(m.group("job_id"), body)
            return

        self._respond(404, {"error": "Not found"})

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    def _handle_start(self, job_id: str, body: dict) -> None:
        if job_id in self.sessions:
            session = self.sessions[job_id]
            if session.status in ("running", "waiting_approval"):
                self._respond(409, {"error": f"Job {job_id} is already running"})
                return

        goal = body.get("goal", "")
        if not goal:
            self._respond(400, {"error": "Missing 'goal' in request body"})
            return

        session = AgentSession(
            job_id=job_id,
            goal=goal,
            callback_url=body.get("callback_url", ""),
            model=body.get("model", ""),
            max_turns=body.get("max_turns", 200),
            approval_timeout=body.get("approval_timeout", 600),
        )
        self.sessions[job_id] = session
        session.start()

        log.info("Started agent session for job %s: %s", job_id, goal[:100])
        self._respond(200, {
            "job_id": job_id,
            "status": "started",
            "model": session.model or "claude-sonnet-4-5-20250929",
        })

    def _handle_approve(self, job_id: str, body: dict) -> None:
        session = self.sessions.get(job_id)
        if not session:
            self._respond(404, {"error": f"Job {job_id} not found"})
            return

        tool_use_id = body.get("tool_use_id", "")
        approved = body.get("approved", True)
        auto_approve_tool = body.get("auto_approve_tool", False)

        if approved:
            ok = session.approve(tool_use_id, auto_approve_tool=auto_approve_tool)
        else:
            ok = session.deny(tool_use_id)

        if ok:
            self._respond(200, {"status": "ok", "approved": approved})
        else:
            self._respond(400, {"error": "No matching pending approval"})

    def _handle_message(self, job_id: str, body: dict) -> None:
        session = self.sessions.get(job_id)
        if not session:
            self._respond(404, {"error": f"Job {job_id} not found"})
            return

        message = body.get("message", "")
        if not message:
            self._respond(400, {"error": "Missing 'message' in request body"})
            return

        session.add_message(message)
        self._respond(200, {"status": "message_added"})

    def _handle_cancel(self, job_id: str, body: dict) -> None:
        session = self.sessions.get(job_id)
        if not session:
            self._respond(404, {"error": f"Job {job_id} not found"})
            return

        session.cancel()
        self._respond(200, {"status": "cancel_requested"})

    def _handle_status(self, job_id: str) -> None:
        session = self.sessions.get(job_id)
        if not session:
            self._respond(404, {"error": f"Job {job_id} not found"})
            return

        data = {
            "job_id": job_id,
            "status": session.status,
            "iteration": session.iteration,
            "max_turns": session.max_turns,
            "model": session.model,
            "result_text": session.result_text[:2000] if session.result_text else "",
        }
        if session.pending_approval:
            data["pending_approval"] = {
                "tool_use_id": session.pending_approval["tool_use_id"],
                "tool_name": session.pending_approval["tool_name"],
            }
        self._respond(200, data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _respond(self, code: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        log.debug("HTTP: %s", format % args)


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    log.info("POC Runner handler starting on port %d", PORT)
    server = HTTPServer(("0.0.0.0", PORT), RunnerHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
