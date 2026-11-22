"""Start the DNS and HTTP listeners and block until interrupted."""

from __future__ import annotations

import errno
import logging
import signal
import threading

from server.config import ServerConfig
from server.db.repository import PlayerStore
from server.games import load_catalogs, load_stats_games
from server.net.dns import DnsServer, answer_ip_for
from server.net.http import build_http_server
from server.net.ssl_proxy import start_ssl_proxy
from server.services.account.available import AvailableServer
from server.services.account.nas import NasService
from server.services.account.presence import PresenceServer
from server.services.account.search import SearchServer
from server.services.account.sessions import SessionStore
from server.services.downloads import DownloadService
from server.services.gamestats import GamestatsServer

LOGGER = logging.getLogger(__name__)


class BindError(RuntimeError):
    """The operating system refused one of the listening ports."""


def serve(config: ServerConfig) -> None:
    players = PlayerStore(config.database)
    try:
        _serve(config, players)
    finally:
        players.close()


def _serve(config: ServerConfig, players: PlayerStore) -> None:
    sessions = SessionStore(players)
    nas = NasService(sessions, config.service_host)
    downloads = DownloadService(load_catalogs())
    httpd = _bind_http(config, nas, downloads)
    dns = _bind_dns(config, httpd)
    dns.start()
    presence = _bind_presence(config, sessions, players, httpd, dns)
    presence.start()
    search = _bind_search(config, players, httpd, dns, presence)
    search.start()
    available = _bind_available(config, httpd, dns, presence, search)
    available.start()
    gamestats = _bind_gamestats(
        config, sessions, players, httpd, dns, presence, search, available
    )
    gamestats.start()
    proxy = start_ssl_proxy(config)
    # SIGINT already raises KeyboardInterrupt. SIGTERM (kill, WSL, a service
    # manager) would otherwise skip this finally and leave the port-443 process.
    # Installed before the listening line so a stop cannot land in between.
    previous_term = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        LOGGER.info(
            "Listening. DNS %d, HTTP %d, SSL %s, presence %d, search %d, "
            "available %d, gamestats %d",
            dns.address[1],
            httpd.server_address[1],
            "off" if proxy is None else str(config.ssl_port),
            presence.address[1],
            search.address[1],
            available.address[1],
            gamestats.address[1],
        )
        LOGGER.debug("Listening on %s, answers %s", config.bind_host, config.answer_ip)
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if proxy is not None:
            proxy.stop()
        _shutdown(httpd, dns, presence, search, available, gamestats)


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


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


def _bind_presence(
    config: ServerConfig,
    sessions: SessionStore,
    players: PlayerStore,
    httpd,
    dns: DnsServer,
) -> PresenceServer:
    try:
        return PresenceServer(config.bind_host, config.presence_port, sessions, players)
    except OSError as exc:
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(_explain("presence", config.bind_host, config.presence_port, exc)) from exc


def _bind_search(
    config: ServerConfig,
    players: PlayerStore,
    httpd,
    dns: DnsServer,
    presence: PresenceServer,
) -> SearchServer:
    try:
        return SearchServer(config.bind_host, config.search_port, players)
    except OSError as exc:
        presence.stop()
        presence.join(timeout=2)
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(_explain("search", config.bind_host, config.search_port, exc)) from exc


def _bind_available(
    config: ServerConfig,
    httpd,
    dns: DnsServer,
    presence: PresenceServer,
    search: SearchServer,
) -> AvailableServer:
    try:
        return AvailableServer(config.bind_host, config.available_port)
    except OSError as exc:
        search.stop()
        search.join(timeout=2)
        presence.stop()
        presence.join(timeout=2)
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(
            _explain("available", config.bind_host, config.available_port, exc)
        ) from exc


def _bind_gamestats(
    config: ServerConfig,
    sessions: SessionStore,
    players: PlayerStore,
    httpd,
    dns: DnsServer,
    presence: PresenceServer,
    search: SearchServer,
    available: AvailableServer,
) -> GamestatsServer:
    try:
        return GamestatsServer(
            config.bind_host,
            config.gamestats_port,
            players,
            sessions,
            load_stats_games(),
        )
    except OSError as exc:
        available.stop()
        available.join(timeout=2)
        search.stop()
        search.join(timeout=2)
        presence.stop()
        presence.join(timeout=2)
        dns.stop()
        dns.join(timeout=2)
        httpd.server_close()
        raise BindError(
            _explain("gamestats", config.bind_host, config.gamestats_port, exc)
        ) from exc


def _shutdown(
    httpd,
    dns: DnsServer,
    presence: PresenceServer,
    search: SearchServer,
    available: AvailableServer,
    gamestats: GamestatsServer,
) -> None:
    stopper = threading.Thread(target=httpd.shutdown, daemon=True)
    stopper.start()
    gamestats.stop()
    available.stop()
    search.stop()
    presence.stop()
    dns.stop()
    gamestats.join(timeout=2)
    available.join(timeout=2)
    search.join(timeout=2)
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
