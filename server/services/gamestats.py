"""GameSpy player-data storage on TCP 29920.

A DS game resolves ``<gamename>.gamestats.gs.nintendowifi.net``. It XOR-decodes
the first 38 bytes with ``GameSpy3D`` and looks for ``challenge``. Later
messages keep that XOR on the body and leave ``\\final\\`` in the clear. After
``\\auth\\`` and ``\\authp\\`` the game reads and writes a record with
``getpd`` / ``setpd``. The record kind comes from the game package.
"""

from __future__ import annotations

import logging
import secrets
import socket
import threading
import time

from server.services.account.presence import profile_id
from server.services.account.sessions import SessionStore
from server.services.store import PlayerStore

LOGGER = logging.getLogger(__name__)

_ENC = b"GameSpy3D"
_FINAL = b"\\final\\"
_DATA = b"\\data\\"
_MAX_BUFFER = 512 * 1024
_UNREGISTERED = "pd"
_CHALLENGE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class GamestatsServer(threading.Thread):
    """TCP listener. One thread per console. ``stop()`` closes the socket."""

    def __init__(
        self,
        host: str,
        port: int,
        players: PlayerStore,
        sessions: SessionStore,
        kinds: dict[str, str],
    ) -> None:
        super().__init__(name="gamestats", daemon=True)
        self._players = players
        self._sessions = sessions
        self._kinds = kinds
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
                args=(conn, peer[0], self._players, self._sessions, self._kinds),
                name="gamestats-client",
                daemon=True,
            )
            worker.start()


def _serve_client(
    conn: socket.socket,
    peer: str,
    players: PlayerStore,
    sessions: SessionStore,
    kinds: dict[str, str],
) -> None:
    gamename = ""
    account_userid = ""
    account_gamecd = ""
    LOGGER.info("Gamestats connected from %s", peer)
    try:
        conn.settimeout(180)
        conn.sendall(challenge_packet())
        pending = bytearray()
        while True:
            body = _next_body(conn, pending)
            if body is None:
                return
            command, fields, payload = _split(body)
            message_id = fields.get("id", "1")
            if command == "auth":
                gamename = fields.get("gamename", "")
                LOGGER.info("Gamestats auth from %s game=%s", peer, gamename or "?")
                sesskey = str(secrets.randbelow(900000000) + 100000000)
                conn.sendall(_text(f"\\sesskey\\{sesskey}\\id\\{message_id}"))
                continue
            if command == "authp":
                account_userid, account_gamecd, profile = _authenticated_profile(
                    players, sessions, fields, gamename
                )
                LOGGER.info("Gamestats authp from %s profile=%s", peer, profile)
                lid = fields.get("lid", "0")
                conn.sendall(_text(f"\\pauthr\\{profile}\\lid\\{lid}"))
                continue
            if command == "setpd":
                conn.sendall(
                    _save(
                        players,
                        kinds,
                        gamename,
                        account_userid,
                        account_gamecd,
                        fields,
                        payload,
                    )
                )
                continue
            if command == "getpd":
                conn.sendall(_load(players, kinds, gamename, fields))
                continue
            LOGGER.info("Gamestats %s from %s %s", command or "<missing>", peer, _preview(body))
    except TimeoutError:
        LOGGER.info("Gamestats closed %s: idle", peer)
    except OSError as exc:
        LOGGER.info("Gamestats closed %s: %s", peer, exc.strerror or exc)
    except Exception:
        LOGGER.exception("Gamestats failed for %s", peer)
    finally:
        conn.close()
        LOGGER.info("Gamestats disconnected %s", peer)


def xor_gamespy(data: bytes) -> bytes:
    """The repeating XOR the stats SDK applies before ``\\final\\``."""
    size = len(_ENC)
    return bytes(byte ^ _ENC[index % size] for index, byte in enumerate(data))


def encode_message(body: bytes) -> bytes:
    """XOR the body and leave the terminator in the clear."""
    return xor_gamespy(body) + _FINAL


def challenge_packet() -> bytes:
    """38 bytes the console XOR-decodes before it looks for ``challenge``."""
    token = "".join(secrets.choice(_CHALLENGE_ALPHABET) for _ in range(20))
    plain = f"\\challenge\\{token}\\final\\".encode("ascii")
    return xor_gamespy(plain)


def _text(body: str) -> bytes:
    return encode_message(body.encode("ascii"))


def _next_body(conn: socket.socket, pending: bytearray) -> bytes | None:
    """One message. ``\\final\\`` is clear; everything before it is XOR-encoded.

    A counted ``\\data\\`` block is taken by its ``length``, so the cipher
    text may contain the clear terminator without ending the unit.
    """
    while True:
        decoded = xor_gamespy(bytes(pending))
        data_at = decoded.find(_DATA)
        if data_at >= 0:
            length = _declared_length(decoded[:data_at])
            if length is not None:
                end = data_at + len(_DATA) + length
                if len(pending) < end and not _fill(conn, pending, end):
                    return None
                if len(pending) < end:
                    return None
                if pending[end : end + len(_FINAL)] != _FINAL and len(pending) < end + len(_FINAL):
                    _fill(conn, pending, end + len(_FINAL))
                consumed = end
                if pending[end : end + len(_FINAL)] == _FINAL:
                    consumed += len(_FINAL)
                body = xor_gamespy(bytes(pending[:end]))
                del pending[:consumed]
                return body
        final_at = pending.find(_FINAL)
        if final_at >= 0 and (data_at < 0 or final_at < data_at):
            body = xor_gamespy(bytes(pending[:final_at]))
            del pending[: final_at + len(_FINAL)]
            return body
        if not _fill(conn, pending):
            return None


def _fill(conn: socket.socket, pending: bytearray, minimum: int | None = None) -> bool:
    target = len(pending) + 1 if minimum is None else minimum
    while len(pending) < target:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        pending.extend(chunk)
        if len(pending) > _MAX_BUFFER:
            raise ValueError("gamestats message is too large")
    return True


def _declared_length(header: bytes) -> int | None:
    text = header.decode("latin1")
    marker = "\\length\\"
    at = text.rfind(marker)
    if at < 0:
        return None
    digits = []
    for char in text[at + len(marker) :]:
        if not char.isdigit():
            break
        digits.append(char)
    if not digits:
        return None
    length = int("".join(digits))
    if length > _MAX_BUFFER:
        raise ValueError("gamestats message is too large")
    return length


# Names that end the ``keys`` list. The list itself is ``\keys\a\b\c\``, not pairs.
_FIELD_NAMES = frozenset({"pid", "ptype", "dindex", "keys", "lid", "length", "id", "mod", "kv"})


def _split(body: bytes) -> tuple[str, dict[str, str], bytes]:
    marker = body.find(_DATA)
    head = body if marker < 0 else body[:marker]
    payload = b"" if marker < 0 else body[marker + len(_DATA) :]
    parts = head.decode("latin1").split("\\")
    if parts and parts[0] == "":
        parts = parts[1:]
    if not parts:
        return "", {}, payload
    command = parts[0]
    rest = parts[1:]
    if rest:
        rest = rest[1:]
    fields = _fields(rest)
    if fields.get("length", "").isdigit():
        payload = payload[: int(fields["length"])]
    return command, fields, payload


def _fields(parts: list[str]) -> dict[str, str]:
    """Pair names with values. ``keys`` keeps every name up to the next field."""
    fields: dict[str, str] = {}
    index = 0
    while index < len(parts):
        name = parts[index]
        if name == "keys":
            index += 1
            names: list[str] = []
            while index < len(parts) and parts[index] not in _FIELD_NAMES:
                if parts[index]:
                    names.append(parts[index])
                index += 1
            fields["keys"] = "\\".join(names)
            continue
        if index + 1 >= len(parts):
            break
        fields[name] = parts[index + 1]
        index += 2
    return fields


def _save(
    players: PlayerStore,
    kinds: dict[str, str],
    gamename: str,
    account_userid: str,
    account_gamecd: str,
    fields: dict[str, str],
    payload: bytes,
) -> bytes:
    pid = fields.get("pid", "")
    lid = fields.get("lid", "0")
    modified = int(time.time())
    if not pid.isdigit():
        LOGGER.warning("Gamestats setpd without a profile id")
        return _set_reply(False, lid, pid, modified)
    gamecd = _gamecd(players, pid, gamename, account_userid, account_gamecd)
    payload = _without_trailing_nul(payload)
    if fields.get("kv", "1") != "0":
        previous = players.get_record(gamecd, _kind(kinds, gamename), pid, _slot(fields))
        if previous is not None:
            payload = _merge_pairs(_without_trailing_nul(previous.payload), payload)
    try:
        modified = players.put_record(
            gamecd,
            _kind(kinds, gamename),
            pid,
            _slot(fields),
            "",
            payload,
        )
    except ValueError as exc:
        LOGGER.warning("Gamestats setpd rejected: %s", exc)
        return _set_reply(False, lid, pid, modified)
    LOGGER.info(
        "Gamestats setpd game=%s profile=%s slot=%s (%d bytes)",
        gamecd,
        pid,
        _slot(fields),
        len(payload),
    )
    return _set_reply(True, lid, pid, modified)


def _load(
    players: PlayerStore,
    kinds: dict[str, str],
    gamename: str,
    fields: dict[str, str],
) -> bytes:
    pid = fields.get("pid", "")
    lid = fields.get("lid", "0")
    payload = b""
    modified = 0
    if pid.isdigit():
        profile = players.profile_by_id(pid)
        gamecd = profile.gamecd if profile is not None else gamename
        found = players.get_record(gamecd, _kind(kinds, gamename), pid, _slot(fields))
        if found is not None:
            payload = _without_trailing_nul(found.payload)
            modified = found.updated_at
    payload = _filter_pairs(payload, fields.get("keys", ""))
    LOGGER.info(
        "Gamestats getpd profile=%s slot=%s keys=%s (%d bytes)",
        pid or "?",
        _slot(fields),
        fields.get("keys", "") or "*",
        len(payload),
    )
    head = (
        f"\\getpdr\\1\\lid\\{lid}\\pid\\{pid}\\mod\\{modified}\\length\\{len(payload)}\\data\\"
    ).encode("ascii")
    return encode_message(head + payload)


def _gamecd(
    players: PlayerStore,
    pid: str,
    gamename: str,
    account_userid: str,
    account_gamecd: str,
) -> str:
    profile = players.profile_by_id(pid)
    if profile is not None:
        return profile.gamecd
    userid = account_userid or pid
    name = account_gamecd or gamename or "gamestats"
    players.remember_profile(userid, name, pid)
    return name


def _authenticated_profile(
    players: PlayerStore,
    sessions: SessionStore,
    fields: dict[str, str],
    gamename: str,
) -> tuple[str, str, str]:
    """The account behind the token, and the profile id to send back."""
    record = sessions.recall(fields.get("authtoken", ""))
    if record is None:
        requested = fields.get("pid", "")
        if requested.isdigit():
            return "", "", requested
        LOGGER.warning("Gamestats authp without a known account token")
        return "", "", "0"
    profile = players.profile_for(record.userid, record.gamecd)
    if profile is not None:
        return record.userid, record.gamecd, profile.profileid
    chosen = profile_id(record.userid, fields.get("pid", ""))
    players.remember_profile(record.userid, record.gamecd or gamename, chosen)
    return record.userid, record.gamecd, chosen


def _kind(kinds: dict[str, str], gamename: str) -> str:
    return kinds.get(gamename, _UNREGISTERED)


def _slot(fields: dict[str, str]) -> str:
    return f"{fields.get('ptype', '0')}:{fields.get('dindex', '0')}"


def _without_trailing_nul(payload: bytes) -> bytes:
    """The cartridge appends a NUL after the record. Returning it blanks the list."""
    if payload.endswith(b"\x00"):
        return payload[:-1]
    return payload


def _record_pairs(payload: bytes) -> list[tuple[str, str]]:
    text = payload.decode("latin1")
    if text.startswith("\\"):
        text = text[1:]
    if not text:
        return []
    parts = text.split("\\")
    if len(parts) % 2:
        parts = parts[:-1]
    return [(parts[index], parts[index + 1]) for index in range(0, len(parts), 2) if parts[index]]


def _join_pairs(pairs: list[tuple[str, str]]) -> bytes:
    return "".join(f"\\{key}\\{value}" for key, value in pairs).encode("latin1")


def _merge_pairs(existing: bytes, update: bytes) -> bytes:
    """``kv`` 1 writes the keys in this upload and leaves the others in the slot."""
    incoming = _record_pairs(update)
    current = _record_pairs(existing)
    if not incoming or not current:
        return update
    merged = dict(current)
    order = [key for key, _ in current]
    for key, value in incoming:
        if key not in merged:
            order.append(key)
        merged[key] = value
    return _join_pairs([(key, merged[key]) for key in order])


def _asked_keys(keys_field: str) -> list[str]:
    """Key names from one ``getpd``. The SDK separates them with byte 0x01."""
    names: list[str] = []
    for piece in keys_field.replace("\x01", "\\").split("\\"):
        if piece and piece not in names:
            names.append(piece)
    return names


def _filter_pairs(payload: bytes, keys_field: str) -> bytes:
    """Return the asked keys that have a value. An empty list means the whole record.

    A missing key is left out, so a request for ``rental01`` is an empty reply
    and cannot repeat the unit stored under ``rental00``.
    """
    pairs = [(key, value) for key, value in _record_pairs(payload) if value]
    if not pairs:
        return b"" if keys_field else payload
    if not keys_field:
        return payload
    wanted = set(_asked_keys(keys_field))
    selected = [(key, value) for key, value in pairs if key in wanted]
    return _join_pairs(selected)


def _set_reply(ok: bool, lid: str, pid: str, modified: int) -> bytes:
    flag = "1" if ok else "0"
    body = f"\\setpdr\\{flag}\\lid\\{lid}\\pid\\{pid}\\mod\\{modified}"
    return _text(body)


def _preview(body: bytes) -> str:
    text = body.decode("latin1", errors="replace")
    text = "".join(char if 32 <= ord(char) < 127 else "." for char in text)
    if len(text) > 180:
        return text[:180] + "..."
    return text
