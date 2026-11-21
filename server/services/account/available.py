"""UDP answer for the GameSpy availability probe on port 27900.

Before NAS login the console asks ``<game>.available.gs.nintendowifi.net``
whether the service is up. The probe starts with ``0x09``. The reply is
``FE FD 09`` and a zero bitfield. A missing reply costs the console about
four seconds, then it continues anyway.
"""

from __future__ import annotations

import logging
import socket
import threading

LOGGER = logging.getLogger(__name__)

_PROBE = 0x09
_REPLY = bytes((0xFE, 0xFD, 0x09, 0, 0, 0, 0))


def availability_reply(packet: bytes) -> tuple[bytes, str] | None:
    """Return the reply and game name for a probe, or None if it is not one."""
    if not packet or packet[0] != _PROBE or len(packet) < 6:
        return None
    raw = packet[5:].split(b"\x00", 1)[0]
    try:
        name = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not name:
        return None
    return _REPLY, name


class AvailableServer(threading.Thread):
    """UDP listener. ``stop()`` closes the socket."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(name="available", daemon=True)
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
                packet, peer = self._socket.recvfrom(256)
            except TimeoutError:
                continue
            except OSError:
                return
            parsed = availability_reply(packet)
            if parsed is None:
                LOGGER.info("Available ignored %d bytes from %s", len(packet), peer[0])
                continue
            reply, name = parsed
            LOGGER.info("Available %s from %s", name, peer[0])
            try:
                self._socket.sendto(reply, peer)
            except OSError:
                return
