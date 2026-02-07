"""HTTP callback server that receives events from the runner agent.

Runs on port 8001 in a daemon thread. The runner POSTs events to
``POST /events`` and this server dispatches them to a registered handler.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

log = logging.getLogger(__name__)

CALLBACK_PORT = 8001

EventHandler = Callable[[dict], None]


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles incoming event POSTs from the runner."""

    # Set by the factory — shared across all requests
    event_handler: EventHandler = lambda event: None

    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/events":
            self._respond(404, {"error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._respond(400, {"error": "Empty body"})
            return

        try:
            body = json.loads(self.rfile.read(content_length).decode())
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON"})
            return

        try:
            self.event_handler(body)
        except Exception:
            log.exception("Event handler error")

        self._respond(200, {"status": "ok"})

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._respond(200, {"status": "ok", "service": "callback-server"})
        else:
            self._respond(404, {"error": "Not found"})

    def _respond(self, code: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        log.debug("Callback HTTP: %s", fmt % args)


def start_callback_server(
    handler: EventHandler, port: int = CALLBACK_PORT
) -> HTTPServer:
    """Create and start the callback server in a daemon thread.

    Returns the HTTPServer instance (for shutdown).
    """
    # Create a handler class with the event_handler bound
    handler_class = type(
        "BoundCallbackHandler",
        (_CallbackHandler,),
        {"event_handler": staticmethod(handler)},
    )

    server = HTTPServer(("0.0.0.0", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="callback-server")
    thread.start()
    log.info("Callback server started on port %d", port)
    return server
