"""SSLv3 proxy in front of the clear HTTP server.

The console finishes its connection test with SSLv3 and RC4 on port 443, and
its ClientHello still uses the old SSLv2 record format. OpenSSL 1.1 and 3
removed that, and the Ubuntu build used by WSL is configured with ``no-ssl3``
and ``no-ssl3-method``. Neither WSL nor macOS ships a certificate the console
accepts. ``ssl/build-proxy.sh`` compiles OpenSSL 1.0.2 and ``bin/ds_ssl_proxy``
for this one listener. The proxy decrypts the request and hands the same HTTP
bytes to the port this process already serves.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from dataclasses import dataclass

from server.config import ServerConfig

LOGGER = logging.getLogger(__name__)

_LISTENING = "SSL proxy listening"
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


@dataclass
class SslProxy:
    """A running proxy process. ``stop`` returns once the process has exited."""

    _process: subprocess.Popen[str]

    def stop(self) -> None:
        process = self._process
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if process.stderr is not None:
            process.stderr.close()


def backend_host(bind_host: str) -> str:
    """Address the proxy uses to reach the HTTP port on this machine."""
    if bind_host == "0.0.0.0":
        return "127.0.0.1"
    return bind_host


def unavailable_reason(config: ServerConfig) -> str | None:
    """Why port 443 cannot open, or ``None`` when the proxy can start."""
    if not config.ssl_proxy.is_file():
        return (
            "SSL port is not open. The console needs SSLv3 and RC4, "
            "including the old SSLv2-shaped hello. "
            f"The OpenSSL linked into Python ({_openssl_version()}) does not provide that, "
            "and Ubuntu on WSL is built with no-ssl3 and no-ssl3-method. "
            "WSL does not contain an old certificate either. "
            "The image build runs ssl/build-proxy.sh."
        )
    missing = [
        str(path)
        for path in (config.cert_file, config.key_file)
        if not path.is_file()
    ]
    if missing:
        return (
            "SSL proxy is built, but the certificate files are missing: "
            + ", ".join(missing)
            + ". WSL does not include a certificate the console accepts. "
            "Place the nds-constraint chain and its private key at those paths."
        )
    return None


def proxy_command(config: ServerConfig) -> list[str]:
    return [
        str(config.ssl_proxy),
        "--bind",
        config.bind_host,
        "--port",
        str(config.ssl_port),
        "--backend-host",
        backend_host(config.bind_host),
        "--backend-port",
        str(config.http_port),
        "--cert",
        str(config.cert_file),
        "--key",
        str(config.key_file),
    ]


def start_ssl_proxy(config: ServerConfig) -> SslProxy | None:
    """Start the proxy when the binary and certificate files are present."""
    reason = unavailable_reason(config)
    if reason is not None:
        LOGGER.warning("%s", reason)
        return None
    try:
        process = subprocess.Popen(
            proxy_command(config),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        LOGGER.warning("SSL proxy could not start: %s", exc)
        return None
    ready = threading.Event()
    relay = threading.Thread(target=_relay_stderr, args=(process, ready), daemon=True)
    relay.start()
    if not ready.wait(timeout=3) or process.poll() is not None:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        LOGGER.warning("SSL proxy exited with status %s", process.returncode)
        relay.join(timeout=1)
        if process.stderr is not None:
            process.stderr.close()
        return None
    return SslProxy(process)


def _relay_stderr(process: subprocess.Popen[str], ready: threading.Event) -> None:
    stream = process.stderr
    if stream is None:
        ready.set()
        return
    for line in stream:
        text = line.rstrip("\n")
        if text.startswith(_LISTENING):
            LOGGER.info(_LISTENING)
            LOGGER.debug("%s", text)
            ready.set()
            continue
        shown = _IPV4.sub("***", text)
        LOGGER.info("%s", shown)
        if shown != text:
            LOGGER.debug("%s", text)
    ready.set()


def _openssl_version() -> str:
    import ssl

    return ssl.OPENSSL_VERSION
