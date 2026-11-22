"""The GameSpy presence handshake the console opens after NAS login."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from server.db.repository import PlayerStore
from server.services.account.available import AvailableServer, availability_reply
from server.services.account.nas import NasService
from server.services.account.presence import (
    PresenceServer,
    gamespy_message,
    generate_proof,
    generate_response,
    parse_messages,
    profile_id,
)
from server.services.account.sessions import SessionStore, parse_form


class ProofTests(unittest.TestCase):
    def test_proof_swaps_the_challenges(self) -> None:
        response = generate_response("SERVERCHAL", "naschall", "clientchal", "tok")
        proof = generate_proof("SERVERCHAL", "naschall", "clientchal", "tok")
        self.assertEqual(response, "2695026d81cad8318b7a41d34aab6131")
        self.assertEqual(proof, "faff64e1cf1759d6058fa5013f1d73e0")


class ProfileIdTests(unittest.TestCase):
    def test_saved_profile_is_echoed(self) -> None:
        self.assertEqual(profile_id("3213702132725", "4821"), "4821")

    def test_nas_userid_does_not_become_the_profile(self) -> None:
        chosen = profile_id("3213702132725", "")
        self.assertNotEqual(chosen, "3213702132725")
        self.assertLessEqual(int(chosen), 0x7FFFFFFF)


class HandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = SessionStore()
        self.nas = NasService(self.sessions, "dls1.nintendowifi.net")
        self.server = PresenceServer("127.0.0.1", 0, self.sessions)
        self.server.start()
        self.address = self.server.address

    def tearDown(self) -> None:
        self.server.stop()
        self.server.join(timeout=2)

    def test_login_returns_the_proof_for_that_nas_token(self) -> None:
        _status, body = self.nas.handle(
            "/ac",
            {"action": "login", "userid": "4", "gamecd": "YFEP"},
        )
        fields = parse_form(body)
        with socket.create_connection(self.address, timeout=2) as sock:
            sock.settimeout(2)
            challenge = _read_message(sock)["challenge"]
            response = generate_response(
                challenge,
                fields["challenge"],
                "CLIENTCHAL",
                fields["token"],
            )
            login = (
                "\\login\\\\challenge\\CLIENTCHAL\\authtoken\\"
                + fields["token"]
                + "\\response\\"
                + response
                + "\\profileid\\42\\gamename\\fireemblemds\\id\\1\\final\\"
            )
            sock.sendall(login.encode("ascii"))
            replies = _read_until(sock, "lc")
        reply = replies[-1]
        self.assertEqual(reply["__cmd__"], "lc")
        self.assertEqual(reply["__cmd_val__"], "2")
        self.assertEqual(
            reply["proof"],
            generate_proof(challenge, fields["challenge"], "CLIENTCHAL", fields["token"]),
        )
        self.assertEqual(reply["userid"], "4")
        self.assertEqual(reply["profileid"], "42")
        self.assertEqual(reply["uniquenick"], "fe42")

    def test_profile_reply_returns_the_lastname(self) -> None:
        _status, body = self.nas.handle("/ac", {"action": "login", "userid": "4"})
        fields = parse_form(body)
        with socket.create_connection(self.address, timeout=2) as sock:
            sock.settimeout(2)
            challenge = _read_message(sock)["challenge"]
            response = generate_response(
                challenge, fields["challenge"], "CLIENTCHAL", fields["token"]
            )
            sock.sendall(
                (
                    "\\login\\\\challenge\\CLIENTCHAL\\authtoken\\"
                    + fields["token"]
                    + "\\response\\"
                    + response
                    + "\\sdkrevision\\3\\id\\1\\final\\"
                    "\\updatepro\\\\sesskey\\1\\lastname\\000000000YFEE2ml3d1m"
                    "\\partnerid\\11\\final\\"
                    "\\getprofile\\\\sesskey\\1\\profileid\\42\\id\\2\\final\\"
                ).encode("ascii")
            )
            replies = _read_until(sock, "pi")
        profile = replies[-1]
        self.assertEqual(profile["__cmd__"], "pi")
        self.assertEqual(profile["lastname"], "000000000YFEE2ml3d1m")
        self.assertEqual(profile["pid"], "11")
        self.assertEqual(profile["id"], "2")
        self.assertIn("sig", profile)

    def test_unknown_token_is_rejected(self) -> None:
        with socket.create_connection(self.address, timeout=2) as sock:
            sock.settimeout(2)
            _read_message(sock)
            sock.sendall(b"\\login\\\\authtoken\\nope\\challenge\\x\\response\\y\\id\\1\\final\\")
            reply = _read_message(sock)
        self.assertEqual(reply["__cmd__"], "error")
        self.assertEqual(reply["err"], "266")

    def test_stored_profile_is_reused_after_the_file_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nds.sqlite"
            first_id = _profile_from_fresh_file(path, "4", "YFEP")
            second_id = _profile_from_fresh_file(path, "4", "YFEP")
        self.assertEqual(second_id, first_id)
        self.assertNotEqual(first_id, "")

    def test_getprofile_reads_the_other_console(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            players = PlayerStore(Path(tmp) / "nds.sqlite")
            sessions = SessionStore(players)
            nas = NasService(sessions, "dls1.nintendowifi.net")
            server = PresenceServer("127.0.0.1", 0, sessions, players)
            server.start()
            try:
                _exchange(
                    nas,
                    server.address,
                    "4",
                    "YFEE",
                    "42",
                    b"\\updatepro\\\\sesskey\\1\\lastname\\abel\\partnerid\\11\\id\\1\\final\\"
                    b"\\ka\\\\final\\",
                    "ka",
                )
                profile = _exchange(
                    nas,
                    server.address,
                    "5",
                    "YFEE",
                    "99",
                    b"\\getprofile\\\\sesskey\\1\\profileid\\42\\id\\2\\final\\",
                    "pi",
                )
            finally:
                server.stop()
                server.join(timeout=2)
                players.close()
        self.assertEqual(profile["profileid"], "42")
        self.assertEqual(profile["userid"], "4")
        self.assertEqual(profile["lastname"], "abel")
        self.assertEqual(profile["pid"], "11")

    def test_addbuddy_is_listed_on_the_next_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            players = PlayerStore(Path(tmp) / "nds.sqlite")
            players.remember_profile("4", "YFEP", "42")
            sessions = SessionStore(players)
            nas = NasService(sessions, "dls1.nintendowifi.net")
            server = PresenceServer("127.0.0.1", 0, sessions, players)
            server.start()
            try:
                added = _exchange_all(
                    nas,
                    server.address,
                    "4",
                    "YFEP",
                    "42",
                    b"\\addbuddy\\\\sesskey\\1\\newprofileid\\900000001\\reason\\\\final\\",
                    "bm",
                )
                authorized = [
                    item
                    for item in added
                    if item["__cmd__"] == "bm" and item["__cmd_val__"] == "1"
                ]
                self.assertEqual(len(authorized), 1)
                self.assertIn("authorized", authorized[0]["msg"])
                replies = _login_replies(nas, server.address, "4", "YFEP", "42", until="bm")
            finally:
                server.stop()
                server.join(timeout=2)
                players.close()
        listed = [item for item in replies if item["__cmd__"] == "bdy"]
        self.assertEqual(listed[-1]["__cmd_val__"], "1")
        self.assertEqual(listed[-1]["list"], "900000001")
        login_at = next(
            index
            for index, item in enumerate(replies)
            if item["__cmd__"] == "lc" and item["__cmd_val__"] == "2"
        )
        authorized_at = next(
            index
            for index, item in enumerate(replies)
            if item["__cmd__"] == "bm" and item["__cmd_val__"] == "1"
        )
        self.assertLess(login_at, authorized_at)

    def test_keepalive_is_answered(self) -> None:
        with socket.create_connection(self.address, timeout=2) as sock:
            sock.settimeout(2)
            _read_message(sock)
            sock.sendall(b"\\ka\\\\final\\")
            reply = _read_message(sock)
        self.assertEqual(reply["__cmd__"], "ka")


def _profile_from_fresh_file(path: Path, userid: str, gamecd: str) -> str:
    players = PlayerStore(path)
    sessions = SessionStore(players)
    nas = NasService(sessions, "dls1.nintendowifi.net")
    server = PresenceServer("127.0.0.1", 0, sessions, players)
    server.start()
    try:
        reply = _login(nas, server.address, userid, gamecd, "")
        return reply["profileid"]
    finally:
        server.stop()
        server.join(timeout=2)
        players.close()


def _login_replies(
    nas: NasService,
    address: tuple[str, int],
    userid: str,
    gamecd: str,
    profileid: str,
    until: str = "lc",
) -> list[dict[str, str]]:
    _status, body = nas.handle("/ac", {"action": "login", "userid": userid, "gamecd": gamecd})
    fields = parse_form(body)
    with socket.create_connection(address, timeout=2) as sock:
        sock.settimeout(2)
        challenge = _read_message(sock)["challenge"]
        response = generate_response(challenge, fields["challenge"], "CLIENTCHAL", fields["token"])
        requested = f"\\profileid\\{profileid}" if profileid else ""
        login = (
            "\\login\\\\challenge\\CLIENTCHAL\\authtoken\\"
            + fields["token"]
            + "\\response\\"
            + response
            + requested
            + "\\gamename\\fireemblemds\\sdkrevision\\3\\id\\1\\final\\"
        )
        sock.sendall(login.encode("ascii"))
        return _read_until(sock, until)


def _login(
    nas: NasService,
    address: tuple[str, int],
    userid: str,
    gamecd: str,
    profileid: str,
) -> dict[str, str]:
    return _exchange(nas, address, userid, gamecd, profileid, b"", "lc")


def _exchange(
    nas: NasService,
    address: tuple[str, int],
    userid: str,
    gamecd: str,
    profileid: str,
    follow: bytes,
    command: str,
) -> dict[str, str]:
    return _exchange_all(nas, address, userid, gamecd, profileid, follow, command)[-1]


def _exchange_all(
    nas: NasService,
    address: tuple[str, int],
    userid: str,
    gamecd: str,
    profileid: str,
    follow: bytes,
    command: str,
) -> list[dict[str, str]]:
    _status, body = nas.handle("/ac", {"action": "login", "userid": userid, "gamecd": gamecd})
    fields = parse_form(body)
    with socket.create_connection(address, timeout=2) as sock:
        sock.settimeout(2)
        challenge = _read_message(sock)["challenge"]
        response = generate_response(challenge, fields["challenge"], "CLIENTCHAL", fields["token"])
        requested = f"\\profileid\\{profileid}" if profileid else ""
        login = (
            "\\login\\\\challenge\\CLIENTCHAL\\authtoken\\"
            + fields["token"]
            + "\\response\\"
            + response
            + requested
            + "\\gamename\\fireemblemds\\id\\1\\final\\"
        )
        sock.sendall(login.encode("ascii") + follow)
        return _read_until(sock, command)


def _read_until(sock: socket.socket, command: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    pending = ""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        pending += chunk.decode("ascii")
        messages, pending = parse_messages(pending)
        found.extend(messages)
        if any(item["__cmd__"] == command for item in found):
            return found
    raise AssertionError(found)


class AvailableTests(unittest.TestCase):
    def test_probe_names_the_game(self) -> None:
        packet = b"\x09\x00\x00\x00\x00fireemblemds\x00"
        parsed = availability_reply(packet)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        reply, name = parsed
        self.assertEqual(name, "fireemblemds")
        self.assertEqual(reply, b"\xfe\xfd\x09\x00\x00\x00\x00")

    def test_server_answers_the_probe(self) -> None:
        server = AvailableServer("127.0.0.1", 0)
        server.start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(b"\x09\x00\x00\x00\x00fireemblemds\x00", server.address)
                reply, _peer = sock.recvfrom(32)
            self.assertEqual(reply, b"\xfe\xfd\x09\x00\x00\x00\x00")
        finally:
            server.stop()
            server.join(timeout=2)


def _read_message(sock: socket.socket) -> dict[str, str]:
    data = ""
    while "\\final\\" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk.decode("ascii")
    messages, _rest = parse_messages(data)
    if not messages:
        raise AssertionError(data)
    return messages[0]


class ParseTests(unittest.TestCase):
    def test_incomplete_tail_is_kept(self) -> None:
        messages, rest = parse_messages("\\ka\\\\final\\\\login\\")
        self.assertEqual(messages[0]["__cmd__"], "ka")
        self.assertEqual(rest, "\\login\\")

    def test_backslash_in_a_value_cannot_split_the_reply(self) -> None:
        raw = gamespy_message("pi", "", [("lastname", "ab\\cd\n")])
        self.assertEqual(raw, b"\\pi\\\\lastname\\abcd\\final\\")


if __name__ == "__main__":
    unittest.main()
