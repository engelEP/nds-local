"""HTTP front for the connection test, authentication, and downloads."""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.services.account.nas import NasService
from server.services.account.sessions import decode_form, encode_form_body, parse_form
from server.services.downloads import DownloadService

LOGGER = logging.getLogger(__name__)

_MAX_BODY = 64 * 1024
_DEFAULT_HOST = "dls1.nintendowifi.net"


def build_http_server(
    host: str,
    port: int,
    nas: NasService,
    downloads: DownloadService,
) -> ThreadingHTTPServer:
    handler = _handler_class(nas, downloads)
    server = ThreadingHTTPServer((host, port), handler)
    # Request threads must not keep the process alive after the listeners stop.
    server.daemon_threads = True
    return server


def _handler_class(nas: NasService, downloads: DownloadService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        # A client that connects and never finishes the body must not keep the process up.
        timeout = 30

        def handle(self) -> None:
            try:
                super().handle()
            except TimeoutError:
                LOGGER.warning("HTTP request timed out")

        def do_GET(self) -> None:  # noqa: N802
            LOGGER.info("HTTP GET %s", _request_path(self.path))
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Organization", "Nintendo")
            self.send_header("Server", "BigIP")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = content_length(self.headers.get("Content-Length"))
            if length is None:
                self._reply(400, {"Content-Type": "text/plain"}, b"")
                return
            if length > _MAX_BODY:
                self._reply(413, {"Content-Type": "text/plain"}, b"")
                return
            raw = self.rfile.read(length)
            form, encoded = decode_form(parse_form(raw))
            path = _request_path(self.path)
            LOGGER.info("HTTP POST %s action=%s", path, form.get("action", ""))
            if path == "/download":
                status, headers, body = downloads.handle(form)
                host = safe_host(self.headers.get("Host"))
                headers = {**headers, "X-DLS-Host": f"http://{host}/"}
            elif path in {"/ac", "/pr"}:
                status, body = nas.handle(path, form)
                if encoded and body:
                    body = encode_form_body(body)
                headers = {"Content-Type": "text/plain", "NODE": "wifiappe1"}
            else:
                LOGGER.warning("HTTP unknown path %s", path)
                status, headers, body = 404, {"Content-Type": "text/plain"}, b""
            self._reply(status, headers, body)

        def _reply(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            LOGGER.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def content_length(raw: str | None) -> int | None:
    """Byte count from a Content-Length header.

    ``None`` means the header is missing, empty, or not a usable length.
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        length = int(raw)
    except ValueError:
        return None
    if length < 0:
        return None
    return length


def safe_host(raw: str | None) -> str:
    """A Host header that can be written back into one response header."""
    if not raw:
        return _DEFAULT_HOST
    host = raw.split(",")[0].strip()
    if not host or not host.isascii():
        return _DEFAULT_HOST
    if any(char in host for char in "\r\n\x00 /\\@?#"):
        return _DEFAULT_HOST
    return host


def _request_path(raw: str) -> str:
    path = raw.split("?", 1)[0]
    return path.replace("\r", "").replace("\n", "")
