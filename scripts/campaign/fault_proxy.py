"""Bounded HTTP fault proxy restricted to explicitly disposable loopback ports.

Does not implement bandwidth shaping or real packet loss. Delays and before/after
forwarding disconnects are deterministic. No request/authorization logs retained.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


def server(target, *, mode="none", delay_ms=0):
    parsed = urlsplit(target)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or not parsed.port or parsed.port < 1024
            or parsed.port in (5432, 8000, 8025, 1025)
            or parsed.username or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Only a disposable loopback origin on a random port is allowed")
    if mode not in ("none", "delay", "drop_before", "drop_after") or not 0 <= delay_ms <= 500:
        raise ValueError("Unsupported or excessive fault configuration")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("Campaign proxy forbids redirects")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def handle_request(self):
            if self.headers.get("Transfer-Encoding"):
                self.send_error(400)
                return
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= 16 * 1024 * 1024 or not self.path.startswith("/"):
                self.send_error(413)
                return
            body = self.rfile.read(length)
            if mode == "delay":
                time.sleep(delay_ms / 1000)
            if mode != "drop_before":
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "connection", "content-length")}
                request = Request(target.rstrip("/") + self.path, data=body if length else None,
                                  headers=headers, method=self.command)
                opener = build_opener(ProxyHandler({}), NoRedirect())
                try:
                    upstream = opener.open(request, timeout=5)
                except HTTPError as response:
                    upstream = response
                with upstream:
                    payload = upstream.read(16 * 1024 * 1024 + 1)
                    if len(payload) > 16 * 1024 * 1024:
                        self.send_error(502)
                        return
                    if mode != "drop_after":
                        self.send_response(upstream.status)
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
