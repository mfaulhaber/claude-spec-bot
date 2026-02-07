"""Simple HTTP message handler for the POC runner.

Starts with the container and listens for incoming messages on port 8000.
Responds with a confirmation that includes the job ID.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

PORT = 8000


class MessageHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON"})
            return

        job_id = data.get("job_id", "unknown")
        message = data.get("message", "")
        log.info("Received message for job %s: %s", job_id, message)

        response = {
            "job_id": job_id,
            "status": "processed",
            "reply": f"Job {job_id} processed by runner. Message received: {message}",
        }
        log.info("Responding: %s", response["reply"])
        self._respond(200, response)

    def do_GET(self):
        self._respond(200, {"status": "ok", "service": "poc-runner"})

    def _respond(self, code: int, data: dict):
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
    server = HTTPServer(("0.0.0.0", PORT), MessageHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
