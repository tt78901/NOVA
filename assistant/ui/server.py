"""A local HUD, served to the browser.

Deliberately dependency-free: a threaded `http.server` hands out one static
page, and state reaches it over Server-Sent Events. SSE is one-directional,
which is all a display needs, and it avoids pulling in a websocket stack.

The bus never blocks the assistant. If a browser tab stops reading, its queue
fills and events are dropped for that client only — a slow HUD must never
stall speech recognition.
"""

from __future__ import annotations

import json
import queue
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
INDEX = HERE / "index.html"


class EventBus:
    def __init__(self, maxsize: int = 200):
        self.maxsize = maxsize
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._last_state = "idle"

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.maxsize)
        with self._lock:
            self._subscribers.add(q)
        # Bring a newly-opened tab up to date immediately.
        q.put({"type": "state", "value": self._last_state})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, type: str, **data: Any) -> None:
        if type == "state":
            self._last_state = data.get("value", "idle")
        event = {"type": type, **data}
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # this client is behind; skip rather than block


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, bus: EventBus, **kwargs):
        self.bus = bus
        super().__init__(*args, **kwargs)

    def log_message(self, *_args) -> None:
        pass  # the terminal belongs to the assistant

    def do_GET(self) -> None:  # noqa: N802  (http.server's naming)
        if self.path.startswith("/events"):
            self._serve_events()
        elif self.path in ("/", "/index.html"):
            self._serve_index()
        else:
            self.send_error(404)

    def _serve_index(self) -> None:
        try:
            body = INDEX.read_bytes()
        except OSError:
            self.send_error(500, "HUD missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = self.bus.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    payload = f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    payload = ": keepalive\n\n"  # stop proxies timing us out
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # tab closed
        finally:
            self.bus.unsubscribe(q)


class UIServer:
    def __init__(self, bus: EventBus, port: int = 7788, host: str = "127.0.0.1"):
        self.bus = bus
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self) -> str:
        handler = partial(_Handler, bus=self.bus)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        # Bound after construction: port 0 would have been reassigned.
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
