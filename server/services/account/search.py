"""GameSpy profile search on TCP 29901.

After presence login the console resolves ``gpsp.gs.nintendowifi.net`` and
asks for the nick of each friend. A refused connection ends the whole
session, including the shop.
"""

from __future__ import annotations

import logging
import socket
import threading

from server.db.repository import PlayerStore
from server.services.account.presence import gamespy_message, parse_messages

LOGGER = logging.getLogger(__name__)

_MAX_BUFFER = 64 * 1024


class SearchServer(threading.Thread):
    """TCP listener. One thread per console. ``stop()`` closes the socket."""

    def __init__(self, host: str, port: int, players: PlayerStore | None = None) -> None:
        super().__init__(name="search", daemon=True)
        self._players = players
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        self._socket.listen(5)
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
                conn, peer = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            worker = threading.Thread(
                target=_serve_client,
                args=(conn, peer[0], self._players),
                name="search-client",
                daemon=True,
            )
            worker.start()


def others_reply(players: PlayerStore | None, requested: str) -> bytes:
    """One nick for each profile id, in numeric order."""
    fields: list[tuple[str, str]] = []
    for profile_id in _profile_ids(requested):
        fields.append(("o", profile_id))
        fields.append(("uniquenick", _nick(players, profile_id)))
    fields.append(("oldone", ""))
    return gamespy_message("otherslist", "", fields)


def _serve_client(conn: socket.socket, peer: str, players: PlayerStore | None) -> None:
    LOGGER.debug("Search connected from %s", peer)
    try:
        conn.settimeout(30)
        pending = ""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            pending += chunk.decode("latin1")
            if len(pending) > _MAX_BUFFER:
                LOGGER.warning("Search closed: message too large")
                LOGGER.debug("Search closed %s: message too large", peer)
                return
            messages, pending = parse_messages(pending)
            for message in messages:
                if not _handle(conn, peer, players, message):
                    return
    except TimeoutError:
        LOGGER.debug("Search closed %s: idle", peer)
    except OSError as exc:
        LOGGER.debug("Search closed %s: %s", peer, exc.strerror or exc)
    except Exception:
        LOGGER.debug("Search failed for %s", peer)
        LOGGER.exception("Search failed")
    finally:
        conn.close()
        LOGGER.debug("Search disconnected %s", peer)


def _handle(
    conn: socket.socket,
    peer: str,
    players: PlayerStore | None,
    message: dict[str, str],
) -> bool:
    command = message.get("__cmd__", "")
    if command == "otherslist":
        requested = message.get("opids", "")
        LOGGER.info("Search otherslist")
        LOGGER.debug("Search otherslist from %s opids=%s", peer, requested or "?")
        conn.sendall(others_reply(players, requested))
        return False
    LOGGER.info("Search %s", command or "<missing>")
    LOGGER.debug("Search %s from %s", command or "<missing>", peer)
    return True


def _profile_ids(raw: str) -> list[str]:
    found: list[str] = []
    for piece in raw.split("|"):
        if piece.isdigit() and int(piece) > 0:
            found.append(str(int(piece)))
    return sorted(set(found), key=int)


def _nick(players: PlayerStore | None, profile_id: str) -> str:
    if players is None:
        return ""
    profile = players.profile_by_id(profile_id)
    if profile is None:
        return ""
    return profile.uniquenick or f"fe{profile_id}"
