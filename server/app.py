"""Start the DNS and HTTP listeners and block until interrupted."""

from __future__ import annotations

import errno
import logging
import signal
import threading

from server.services.account.available import AvailableServer
from server.services.account.nas import NasService
from server.services.account.presence import PresenceServer
from server.services.account.sessions import SessionStore
from server.services.downloads import DownloadService
from server.games import load_catalogs
from server.net.dns import DnsServer, answer_ip_for
from server.net.http import build_http_server
from server.net.ssl_proxy import SslProxy, start_ssl_proxy
from server.config import ServerConfig

LOGGER = logging.getLogger(__name__)


class BindError(RuntimeError):
    """The operating system refused one of the listening ports."""


def serve(config: ServerConfig) -> None:
    sessions = SessionStore()
    nas = NasService(sessions, config.service_host)
    downloads = DownloadService(load_catalogs())
    httpd = _bind_http(config, nas, downloads)
    dns = _bind_dns(config, httpd)
    dns.start()
    presence = _bind_presence(config, sessions, httpd, dns)
    presence.start()
    available = _bind_available(config, httpd, dns, presence)
    available.start()
    proxy = start_ssl_proxy(config)
    # SIGINT already raises KeyboardInterrupt. SIGTERM (kill, WSL, a service
    # manager) would otherwise skip this finally and leave the port-443 process.
    # Installed before the listening line so a stop cannot land in between.
    previous_term = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        LOGGER.info(
            "Listening. DNS %s:%d, HTTP %s:%d, SSL %s, presence %s:%d, available %s:%d, answers %s",
            config.bind_host,
            dns.address[1],
            config.bind_host,
            httpd.server_address[1],
            _ssl_where(config, proxy),
            config.bind_host,
            presence.address[1],
            config.bind_host,
            available.address[1],
            config.answer_ip,
        )
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if proxy is not None:
            proxy.stop()
        _shutdown(httpd, dns, presence, available)


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _ssl_where(config: ServerConfig, proxy: SslProxy | None) -> str:
    if proxy is None:
        return "off"
    return f"{config.bind_host}:{config.ssl_port}"


def _bind_http(config: ServerConfig, nas: NasService, downloads: DownloadService):
    try:
        return build_http_server(config.bind_host, config.http_port, nas, downloads)
    except OSError as exc:
        raise BindError(_explain("HTTP", config.bind_host, config.http_port, exc)) from exc


def _bind_dns(config: ServerConfig, httpd) -> DnsServer:
    suffixes = config.dns_suffixes
    answer = config.answer_ip

    def resolve(name: str) -> str | None:
        return answer_ip_for(name, suffixes, answer)

    try:
        return DnsServer(config.bind_host, config.dns_port, resolve)
    except OSError as exc:
        httpd.server_close()
        raise BindError(_explain("DNS", config.bind_host, config.dns_port, exc)) from exc


def _bind_presence(config: ServerConfig, sessions: SessionStore, httpd, dns: DnsServer) -> PresenceServer:
    try:
        return PresenceServer(config.bind_host, config.presence_port, sessions)
    except OSError as exc:
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(_explain("presence", config.bind_host, config.presence_port, exc)) from exc


def _bind_available(
    config: ServerConfig,
    httpd,
    dns: DnsServer,
    presence: PresenceServer,
) -> AvailableServer:
    try:
        return AvailableServer(config.bind_host, config.available_port)
    except OSError as exc:
        presence.stop()
        presence.join(timeout=2)
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(_explain("available", config.bind_host, config.available_port, exc)) from exc


def _shutdown(httpd, dns: DnsServer, presence: PresenceServer, available: AvailableServer) -> None:
    stopper = threading.Thread(target=httpd.shutdown, daemon=True)
    stopper.start()
    available.stop()
    presence.stop()
    dns.stop()
    available.join(timeout=2)
    presence.join(timeout=2)
    dns.join(timeout=2)
    stopper.join(timeout=2)
    httpd.server_close()


def _explain(kind: str, host: str, port: int, exc: OSError) -> str:
    if isinstance(exc, PermissionError) or exc.errno == errno.EACCES:
        return (
            f"Cannot listen on {host}:{port} for {kind}. "
            "Ports below 1024 need extra privilege. Termux on an unrooted phone "
            "usually cannot open 53 or 80. Run this program on a computer in the "
            "same network instead: --bind 0.0.0.0 --answer-ip <that computer's LAN address>."
        )
    if exc.errno == errno.EADDRINUSE:
        return f"{kind} port {port} on {host} is already in use."
    return f"Cannot listen on {host}:{port} for {kind}: {exc.strerror or exc}"
