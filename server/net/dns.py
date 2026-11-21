"""Small DNS responder for the hostnames a DS session looks up.

Only names under the configured suffixes receive an A record. Every other
name is NXDOMAIN, so this process is not an open resolver.
"""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable

LOGGER = logging.getLogger(__name__)

_QTYPE_A = 1
_CLASS_IN = 1
_POINTER_TO_QUESTION = b"\xc0\x0c"
_MAX_PACKET = 512


class DnsError(ValueError):
    """The query packet is too short or malformed to answer."""


def encode_name(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    chunks: list[bytes] = []
    for label in labels:
        raw = label.encode("ascii")
        if not raw or len(raw) > 63:
            raise DnsError(f"invalid label in {name!r}")
        chunks.append(bytes([len(raw)]) + raw)
    return b"".join(chunks) + b"\x00"


def decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    cursor = offset
    jumped = False
    end = offset
    hops = 0
    while True:
        if cursor >= len(packet):
            raise DnsError("name ran past the packet")
        length = packet[cursor]
        if length == 0:
            cursor += 1
            if not jumped:
                end = cursor
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                raise DnsError("truncated name pointer")
            if not jumped:
                end = cursor + 2
            cursor = ((length & 0x3F) << 8) | packet[cursor + 1]
            jumped = True
            hops += 1
            if hops > 8:
                raise DnsError("name pointer loop")
            continue
        cursor += 1
        if cursor + length > len(packet):
            raise DnsError("truncated label")
        try:
            labels.append(packet[cursor : cursor + length].decode("ascii"))
        except UnicodeDecodeError as exc:
            raise DnsError("label is not ASCII") from exc
        cursor += length
        if not jumped:
            end = cursor
    return ".".join(labels), end


def answer_ip_for(name: str, suffixes: tuple[str, ...], answer_ip: str) -> str | None:
    host = name.lower().rstrip(".")
    for suffix in suffixes:
        tail = suffix.lower().rstrip(".")
        if host == tail or host.endswith("." + tail):
            return answer_ip
    return None


def build_response(query: bytes, resolve: Callable[[str], str | None]) -> bytes:
    """Build one response for a single-question query."""
    if len(query) < 12:
        raise DnsError("packet shorter than a DNS header")
    if int.from_bytes(query[4:6], "big") != 1:
        raise DnsError("expected one question")
    name, offset = decode_name(query, 12)
    if offset + 4 > len(query):
        raise DnsError("question is truncated")
    qtype = int.from_bytes(query[offset : offset + 2], "big")
    question = query[12 : offset + 4]
    resolved = resolve(name)
    address = resolved if qtype == _QTYPE_A else None

    flags = 0x8183 if resolved is None else 0x8180
    header = b"".join(
        (
            query[:2],
            flags.to_bytes(2, "big"),
            (1).to_bytes(2, "big"),
            (1 if address else 0).to_bytes(2, "big"),
            b"\x00\x00\x00\x00",
        )
    )
    if not address:
        LOGGER.debug("DNS NXDOMAIN %s", name)
        return header + question

    LOGGER.info("DNS %s -> %s", name, address)
    answer = b"".join(
        (
            _POINTER_TO_QUESTION,
            _QTYPE_A.to_bytes(2, "big"),
            _CLASS_IN.to_bytes(2, "big"),
            (300).to_bytes(4, "big"),
            (4).to_bytes(2, "big"),
            socket.inet_aton(address),
        )
    )
    return header + question + answer


class DnsServer(threading.Thread):
    """UDP listener. Stops when ``stop()`` closes the socket."""

    def __init__(self, host: str, port: int, resolve: Callable[[str], str | None]) -> None:
        super().__init__(name="dns", daemon=True)
        self._resolve = resolve
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        self._socket.settimeout(0.5)
        self._stopped = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._socket.getsockname()
        return host, port

    def stop(self) -> None:
        self._stopped.set()
        self._socket.close()

    def run(self) -> None:
        while not self._stopped.is_set():
            try:
                payload, peer = self._socket.recvfrom(_MAX_PACKET)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                reply = build_response(payload, self._resolve)
            except DnsError as exc:
                LOGGER.warning("DNS ignored a packet from %s: %s", peer[0], exc)
                continue
            except Exception:
                LOGGER.exception("DNS failed to answer %s", peer[0])
                continue
            try:
                self._socket.sendto(reply, peer)
            except OSError:
                return
