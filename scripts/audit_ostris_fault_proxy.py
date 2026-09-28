"""Local AUDIT-only osTRIS HTTP fault injection; no production deployment."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import os
import socket
import threading
import time

UPSTREAM = os.environ.get("OSTRIS_UPSTREAM", "http://ostris:8095")
lock = threading.Lock()
next_mode = "normal"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log Authorization or request bodies.
        print("AUDIT proxy " + fmt % args, flush=True)

    def do_GET(self):
        self.forward()

    def do_POST(self):
        global next_mode
        if self.path.startswith("/__audit/mode/"):
            mode = self.path.rsplit("/", 1)[-1]
            if mode not in {"normal", "unavailable_once", "timeout_once", "drop_after_once"}:
                self.send_error(400)
                return
            with lock:
                next_mode = mode
            self.respond(204, b"")
            return
        mode = "normal"
        if self.path.endswith("/commit"):
            with lock:
                mode, next_mode = next_mode, "normal"
        if mode == "unavailable_once":
            self.respond(503, b'{"code":"AUDIT_UNAVAILABLE","message":"Injected before upstream processing"}', "application/json")
            return
        if mode == "timeout_once":
            time.sleep(25)
            self.respond(503, b'{"code":"AUDIT_TIMEOUT","message":"Injected before upstream processing"}', "application/json")
            return
        self.forward(drop_response=(mode == "drop_after_once"))

    def forward(self, drop_response=False):
        length = int(self.headers.get("Content-Length", "0"))
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            body = b"".join(chunks)
        else:
            body = self.rfile.read(length) if length else None
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() in {"authorization", "x-tenant", "content-type"}}
        request = Request(UPSTREAM + self.path, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=60) as upstream:
                status, payload = upstream.status, upstream.read()
                content_type = upstream.headers.get("Content-Type", "application/json")
        except HTTPError as error:
            status, payload = error.code, error.read()
            content_type = error.headers.get("Content-Type", "application/json")
        if drop_response:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.respond(status, payload, content_type)

    def respond(self, status, payload, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


ThreadingHTTPServer(("0.0.0.0", 8095), Handler).serve_forever()
