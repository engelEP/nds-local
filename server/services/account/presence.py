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
from server.services.store import PlayerStore, Profile

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
    if proof:
        left, right = server_challenge, client_challenge
    else:
        left, right = client_challenge, server_challenge
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

    def __init__(
        self,
        host: str,
        port: int,
        sessions: SessionStore,
        players: PlayerStore | None = None,
    ) -> None:
        super().__init__(name="presence", daemon=True)
        self._sessions = sessions
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
                args=(conn, peer[0], self._sessions, self._players),
                name="presence-client",
                daemon=True,
            )
            worker.start()


def _serve_client(
    conn: socket.socket,
    peer: str,
    sessions: SessionStore,
    players: PlayerStore | None,
) -> None:
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
                if not _handle(conn, peer, sessions, players, challenge, profile, message):
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
    players: PlayerStore | None,
    challenge: str,
    profile: dict[str, str],
    message: dict[str, str],
) -> bool:
    command = message.get("__cmd__", "")
    match command:
        case "login":
            return _login(conn, peer, sessions, players, challenge, profile, message)
        case "ka":
            conn.sendall(gamespy_message("ka", "", []))
            return True
        case "logout":
            LOGGER.info("Presence logout %s", peer)
            return False
        case "updatepro":
            _remember_profile(profile, players, message)
            LOGGER.info("Presence updatepro from %s %s", peer, _fields(message))
            return True
        case "getprofile":
            LOGGER.info(
                "Presence getprofile from %s profile=%s lastname=%s",
                peer,
                message.get("profileid", profile.get("profileid", "")),
                profile.get("lastname", ""),
            )
            _profile(conn, profile, players, message)
            return True
        case "status":
            LOGGER.info(
                "Presence status %s stat=%s loc=%s",
                peer,
                message.get("statstring", ""),
                message.get("locstring", ""),
            )
            return True
        case "addbuddy":
            _add_buddy(conn, players, profile, message)
            return True
        case "delbuddy":
            _remove_buddy(players, profile, message)
            return True
        case _:
            LOGGER.info(
                "Presence %s from %s %s",
                command or "<missing>",
                peer,
                _fields(message),
            )
            return True


# The cart keeps a new code gray until this exact reply arrives. It is the
# other player accepting the request, which a one-console test cannot send.
_AUTHORIZE = "I have authorized your request to add me to your list"


def _send_buddy_list(conn: socket.socket, players: PlayerStore | None, owner: str) -> list[str]:
    """The block and friend lists. Status arrives only after login is finished."""
    friends = players.list_buddies(owner) if players is not None else []
    conn.sendall(gamespy_message("blk", "0", [("list", "")]))
    conn.sendall(gamespy_message("bdy", str(len(friends)), [("list", ",".join(friends))]))
    return friends


def _announce_buddies(conn: socket.socket, friends: list[str]) -> None:
    for friend in friends:
        _buddy_authorized(conn, friend)
        _buddy_status(conn, friend)


def _buddy_authorized(conn: socket.socket, friend: str) -> None:
    conn.sendall(gamespy_message("bm", "1", [("f", friend), ("msg", _AUTHORIZE)]))


def _buddy_status(conn: socket.socket, friend: str) -> None:
    conn.sendall(
        gamespy_message(
            "bm",
            "100",
            [("f", friend), ("msg", "|s|1|ss|Online|ls||ip|0|p|0|qm|0")],
        )
    )


def _buddy_id(message: dict[str, str]) -> str:
    for key in ("newprofileid", "delprofileid", "profileid", "buddy"):
        value = message.get(key, "")
        if value.isdigit():
            return value
    return ""


def _add_buddy(
    conn: socket.socket,
    players: PlayerStore | None,
    profile: dict[str, str],
    message: dict[str, str],
) -> None:
    owner = profile.get("profileid", "")
    friend = _buddy_id(message)
    if players is None or not owner.isdigit() or not friend.isdigit() or friend == owner:
        LOGGER.info("Presence addbuddy ignored owner=%s friend=%s", owner, friend or "?")
        return
    players.add_buddy(owner, friend)
    LOGGER.info("Presence addbuddy %s -> %s", owner, friend)
    friends = _send_buddy_list(conn, players, owner)
    _announce_buddies(conn, friends)


def _remove_buddy(
    players: PlayerStore | None,
    profile: dict[str, str],
    message: dict[str, str],
) -> None:
    owner = profile.get("profileid", "")
    friend = _buddy_id(message)
    if players is None or not owner.isdigit() or not friend.isdigit():
        return
    players.remove_buddy(owner, friend)
    LOGGER.info("Presence delbuddy %s -> %s", owner, friend)


def _fields(message: dict[str, str]) -> str:
    return " ".join(f"{key}={value}" for key, value in message.items() if not key.startswith("_"))


def _login(
    conn: socket.socket,
    peer: str,
    sessions: SessionStore,
    players: PlayerStore | None,
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
    profileid = _assign_profile_id(
        players,
        record.userid,
        record.gamecd,
        message.get("profileid", ""),
    )
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
    # The console reads the friend list before it accepts the login reply.
    # Authorization is a later message: sending it first makes SDK 3 drop the session.
    friends = _send_buddy_list(conn, players, profileid)
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
    _announce_buddies(conn, friends)
    LOGGER.info(
        "Presence login %s userid=%s profile=%s game=%s sdk=%s",
        peer,
        record.userid,
        profileid,
        message.get("gamename", record.gamecd or "?"),
        message.get("sdkrevision", "?"),
    )
    return True


def _assign_profile_id(
    players: PlayerStore | None,
    userid: str,
    gamecd: str,
    requested: str,
) -> str:
    """The id the console already saved, or the one stored for this account."""
    if players is not None and requested == "":
        stored = players.profile_for(userid, gamecd)
        if stored is not None:
            return stored.profileid
    chosen = profile_id(userid, requested)
    if players is None:
        return chosen
    return players.remember_profile(userid, gamecd, chosen)


def _remember_profile(
    profile: dict[str, str],
    players: PlayerStore | None,
    message: dict[str, str],
) -> None:
    fields: dict[str, str] = {}
    for key in ("firstname", "lastname", "nick", "uniquenick", "email"):
        value = message.get(key, "")
        if value:
            profile[key] = value
            fields[key] = value
    partner = message.get("partnerid", "")
    if partner:
        profile["pid"] = partner
        fields["partnerid"] = partner
    profileid = profile.get("profileid", "")
    if players is not None and profileid:
        players.update_profile(profileid, fields)


def _profile(
    conn: socket.socket,
    profile: dict[str, str],
    players: PlayerStore | None,
    message: dict[str, str],
) -> None:
    profile = _profile_source(profile, players, message)
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


def _profile_source(
    profile: dict[str, str],
    players: PlayerStore | None,
    message: dict[str, str],
) -> dict[str, str]:
    requested = message.get("profileid", "")
    local_id = profile.get("profileid", "")
    if requested == "" or requested == local_id or players is None:
        return profile
    stored = players.profile_by_id(requested)
    if stored is None:
        return {"profileid": requested, "userid": "", "uniquenick": f"fe{requested}"}
    return _stored_profile(stored)


def _stored_profile(stored: Profile) -> dict[str, str]:
    fields = {
        "profileid": stored.profileid,
        "userid": stored.userid,
        "nick": stored.nick,
        "uniquenick": stored.uniquenick or f"fe{stored.profileid}",
        "email": stored.email,
        "firstname": stored.firstname,
        "lastname": stored.lastname,
    }
    if stored.partnerid:
        fields["pid"] = stored.partnerid
    return fields
