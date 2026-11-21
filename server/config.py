"""Process settings. Defaults target melonDS on the same phone."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator


DEFAULT_SUFFIXES = (
    "nintendowifi.net",
    "nintendo.net",
    "gamespy.com",
)


class ServerConfig(BaseModel):
    """Where the process listens, and which address the console should use.

    ``bind_host`` is the local interface. ``answer_ip`` is the address written
    into DNS replies. On a phone, melonDS reaches 10.64.0.1 and libslirp
    delivers that traffic to 127.0.0.1, so those two values differ.
    """

    model_config = ConfigDict(frozen=True)

    bind_host: str = "127.0.0.1"
    dns_port: int = 53
    http_port: int = 80
    ssl_port: int = 443
    presence_port: int = 29900
    available_port: int = 27900
    answer_ip: str = "10.64.0.1"
    cert_file: Path = Path("certs/server.chain.crt")
    key_file: Path = Path("certs/server.key")
    ssl_proxy: Path = Path("bin/ds_ssl_proxy")
    dns_suffixes: tuple[str, ...] = DEFAULT_SUFFIXES

    @field_validator("bind_host", "answer_ip")
    @classmethod
    def _check_ipv4(cls, value: str) -> str:
        try:
            return str(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError as exc:
            raise ValueError(f"{value} is not an IPv4 address") from exc

    @field_validator("dns_port", "http_port", "ssl_port", "presence_port", "available_port")
    @classmethod
    def _check_port(cls, value: int) -> int:
        if not 0 <= value <= 65535:
            raise ValueError(f"port {value} is outside 0-65535")
        return value

    @property
    def service_host(self) -> str:
        return "dls1.nintendowifi.net"


def parse_args(argv: list[str] | None = None) -> ServerConfig:
    parser = argparse.ArgumentParser(
        prog="nds-local",
        description=(
            "Answer a melonDS Nintendo DS session from this machine. "
            "DNS replies send Nintendo hostnames to --answer-ip."
        ),
    )
    parser.add_argument(
        "--bind",
        type=_ipv4,
        default="127.0.0.1",
        help="Local IPv4 address to listen on. Use 0.0.0.0 to accept other machines.",
    )
    parser.add_argument("--dns-port", type=_port, default=53)
    parser.add_argument("--http-port", type=_port, default=80)
    parser.add_argument("--ssl-port", type=_port, default=443)
    parser.add_argument(
        "--answer-ip",
        type=_ipv4,
        default="10.64.0.1",
        help="A-record returned to the console. Use 10.64.0.1 when this process runs on the phone.",
    )
    parser.add_argument(
        "--cert",
        type=Path,
        default=Path("certs/server.chain.crt"),
        help="PEM chain presented to the console on the SSL port.",
    )
    parser.add_argument(
        "--key",
        type=Path,
        default=Path("certs/server.key"),
        help="Private key for --cert.",
    )
    parser.add_argument(
        "--ssl-proxy",
        type=Path,
        default=Path("bin/ds_ssl_proxy"),
        help="SSLv3 proxy built by ssl/build-proxy.sh.",
    )
    args = parser.parse_args(argv)
    return ServerConfig(
        bind_host=args.bind,
        dns_port=args.dns_port,
        http_port=args.http_port,
        ssl_port=args.ssl_port,
        answer_ip=args.answer_ip,
        cert_file=args.cert,
        key_file=args.key,
        ssl_proxy=args.ssl_proxy,
    )


def _port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port {port} is outside 0-65535")
    return port


def _ipv4(raw: str) -> str:
    try:
        return str(ipaddress.IPv4Address(raw))
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw} is not an IPv4 address") from exc
