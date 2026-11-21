"""GameSpy presence login on TCP 29900.

After NAS returns ``returncd`` 001, the console resolves
``gpcm.gs.nintendowifi.net`` and opens this port. Nothing else in the
session speaks until this handshake finishes. A refused connection is the
disconnect error on the console.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import socket
import threading

from server.services.account.sessions import SessionStore

LOGGER = logging.getLogger(__name__)

_CHALLENGE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MAX_BUFFER = 64 * 1024
_PROFILE_MAX = 0x7FFFFFFF


def generate_response(
    server_challenge: str,
    nas_challenge: str,
    client_challenge: str,
    authtoken: str,
) -> str:
    """The ``response`` field the console sends with its login."""
    return _digest(server_challenge, nas_challenge, client_challenge, authtoken, proof=False)


def generate_proof(
    server_challenge: str,
    nas_challenge: str,
    client_challenge: str,
    authtoken: str,
) -> str:
    """The ``proof`` the console checks in the login reply.

    Same material as the response, with the two challenges swapped.
    """
    return _digest(server_challenge, nas_challenge, client_challenge, authtoken, proof=True)


def _digest(
    server_challenge: str,
    nas_challenge: str,
    client_challenge: str,
    authtoken: str,
    *,
    proof: bool,
) -> str:
    nas_hash = hashlib.md5(nas_challenge.encode("ascii")).hexdigest()
    left, right = (server_challenge, client_challenge) if proof else (client_challenge, server_challenge)
    material = f"{nas_hash}{' ' * 0x30}{authtoken}{left}{right}{nas_hash}"
    return hashlib.md5(material.encode("ascii")).hexdigest()


def parse_messages(data: str) -> tuple[list[dict[str, str]], str]:
    """Split complete ``\\final\\`` messages. The tail is an unfinished one."""
    messages: list[dict[str, str]] = []
    while True:
        end = data.find("\\final\\")
        if end < 0:
            return messages, data
        chunk = data[:end]
        data = data[end + len("\\final\\") :]
        if not chunk.startswith("\\"):
            continue
        parts = chunk.split("\\")[1:]
        if len(parts) < 2:
            continue
        fields = {"__cmd__": parts[0], "__cmd_val__": parts[1]}
        rest = parts[2:]
        if len(rest) % 2:
            rest = rest[:-1]
        for index in range(0, len(rest), 2):
            fields[rest[index]] = rest[index + 1]
        messages.append(fields)


def profile_id(userid: str, requested: str) -> str:
    """A profile id the console can store in 32 bits.

    The console sends the id it saved on an earlier login. That value has to
    come back unchanged. A NAS userid is longer than 32 bits, so it cannot
    be used when the console did not send one.
    """
    if requested.isdigit():
        value = int(requested)
        if 1 <= value <= _PROFILE_MAX:
            return str(value)
    digest = hashlib.md5(userid.encode("ascii")).digest()
    value = int.from_bytes(digest[:4], "big") & _PROFILE_MAX
    return str(value or 1)


def _wire(value: str) -> str:
    """Keep a field on one line. A backslash would split the message."""
    return "".join(char for char in value if 32 <= ord(char) < 127 and char != "\\")


def gamespy_message(command: str, value: str, fields: list[tuple[str, str]]) -> bytes:
    body = "".join(f"\\{_wire(key)}\\{_wire(item)}" for key, item in fields)
    return f"\\{_wire(command)}\\{_wire(value)}{body}\\final\\".encode("ascii")


class PresenceServer(threading.Thread):
    """TCP listener. One thread per console. ``stop()`` closes the socket."""

    def __init__(self, host: str, port: int, sessions: SessionStore) -> None:
        super().__init__(name="presence", daemon=True)
        self._sessions = sessions
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
                args=(conn, peer[0], self._sessions),
                name="presence-client",
                daemon=True,
            )
            worker.start()


def _serve_client(conn: socket.socket, peer: str, sessions: SessionStore) -> None:
    challenge = "".join(secrets.choice(_CHALLENGE_ALPHABET) for _ in range(10))
    profile: dict[str, str] = {}
    LOGGER.info("Presence connected from %s", peer)
    try:
        conn.settimeout(180)
        conn.sendall(
            gamespy_message("lc", "1", [("challenge", challenge), ("id", "1")])
        )
        pending = ""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            pending += chunk.decode("latin1")
            if len(pending) > _MAX_BUFFER:
                LOGGER.warning("Presence closed %s: message too large", peer)
                return
            messages, pending = parse_messages(pending)
            for message in messages:
                if not _handle(conn, peer, sessions, challenge, profile, message):
                    return
    except TimeoutError:
        LOGGER.info("Presence closed %s: idle", peer)
    except OSError as exc:
        LOGGER.info("Presence closed %s: %s", peer, exc.strerror or exc)
    except Exception:
        LOGGER.exception("Presence failed for %s", peer)
    finally:
        conn.close()
        LOGGER.info("Presence disconnected %s", peer)


def _handle(
    conn: socket.socket,
    peer: str,
    sessions: SessionStore,
    challenge: str,
    profile: dict[str, str],
    message: dict[str, str],
) -> bool:
    command = message.get("__cmd__", "")
    if command == "login":
        return _login(conn, peer, sessions, challenge, profile, message)
    if command == "ka":
        conn.sendall(gamespy_message("ka", "", []))
        return True
    if command == "logout":
        LOGGER.info("Presence logout %s", peer)
        return False
    if command == "updatepro":
        _remember_profile(profile, message)
        details = " ".join(
            f"{key}={value}" for key, value in message.items() if not key.startswith("_")
        )
        LOGGER.info("Presence updatepro from %s %s", peer, details)
        return True
    if command == "getprofile":
        LOGGER.info(
            "Presence getprofile from %s profile=%s lastname=%s",
            peer,
            message.get("profileid", profile.get("profileid", "")),
            profile.get("lastname", ""),
        )
        _profile(conn, profile, message)
        return True
    if command == "status":
        LOGGER.info(
            "Presence status %s stat=%s loc=%s",
            peer,
            message.get("statstring", ""),
            message.get("locstring", ""),
        )
        return True
    details = " ".join(
        f"{key}={value}" for key, value in message.items() if not key.startswith("_")
    )
    LOGGER.info("Presence %s from %s %s", command or "<missing>", peer, details)
    return True


def _login(
    conn: socket.socket,
    peer: str,
    sessions: SessionStore,
    challenge: str,
    profile: dict[str, str],
    message: dict[str, str],
) -> bool:
    token = message.get("authtoken", "")
    record = sessions.recall(token)
    if record is None:
        LOGGER.warning("Presence login from %s rejected: unknown token", peer)
        conn.sendall(
            gamespy_message(
                "error",
                "",
                [
                    ("err", "266"),
                    ("fatal", ""),
                    ("errmsg", "There was an error validating the pre-authentication."),
                    ("id", message.get("id", "1")),
                ],
            )
        )
        return True
    client_challenge = message.get("challenge", "")
    expected = generate_response(challenge, record.challenge, client_challenge, token)
    if message.get("response") != expected:
        LOGGER.warning(
            "Presence login from %s: response %s, expected %s",
            peer,
            message.get("response", ""),
            expected,
        )
    proof = generate_proof(challenge, record.challenge, client_challenge, token)
    profileid = profile_id(record.userid, message.get("profileid", ""))
    uniquenick = f"fe{profileid}"
    profile.clear()
    profile.update(
        {
            "userid": record.userid,
            "profileid": profileid,
            "uniquenick": uniquenick,
            "gamecd": record.gamecd,
        }
    )
    sesskey = str(secrets.randbelow(900000000) + 100000000)
    # SDK 11 reads the buddy lists before it accepts the login reply.
    if message.get("sdkrevision") == "11":
        conn.sendall(gamespy_message("blk", "0", [("list", "")]))
        conn.sendall(gamespy_message("bdy", "0", [("list", "")]))
    conn.sendall(
        gamespy_message(
            "lc",
            "2",
            [
                ("sesskey", sesskey),
                ("proof", proof),
                ("userid", record.userid),
                ("profileid", profileid),
                ("uniquenick", uniquenick),
                ("lt", secrets.token_hex(8)),
                ("id", message.get("id", "1")),
            ],
        )
    )
    LOGGER.info(
        "Presence login %s userid=%s profile=%s game=%s sdk=%s",
        peer,
        record.userid,
        profileid,
        message.get("gamename", record.gamecd or "?"),
        message.get("sdkrevision", "?"),
    )
    return True


def _remember_profile(profile: dict[str, str], message: dict[str, str]) -> None:
    for key in ("firstname", "lastname", "nick", "uniquenick", "email"):
        value = message.get(key, "")
        if value:
            profile[key] = value
    partner = message.get("partnerid", "")
    if partner:
        profile["pid"] = partner


def _profile(conn: socket.socket, profile: dict[str, str], message: dict[str, str]) -> None:
    uniquenick = profile.get("uniquenick", "fe1")
    fields = [
        ("profileid", message.get("profileid", profile.get("profileid", "1"))),
        ("nick", profile.get("nick", uniquenick)),
        ("userid", profile.get("userid", "1")),
        ("email", profile.get("email", f"{uniquenick}@nds")),
        ("sig", secrets.token_hex(16)),
        ("uniquenick", uniquenick),
        ("pid", profile.get("pid", "11")),
    ]
    for key in ("firstname", "lastname"):
        value = profile.get(key, "")
        if value:
            fields.append((key, value))
    fields.extend(
        [
            ("lon", "0.000000"),
            ("lat", "0.000000"),
            ("loc", ""),
            ("id", message.get("id", "1")),
        ]
    )
    conn.sendall(gamespy_message("pi", "", fields))
