"""Loan units stored through the GameSpy stats port."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from server.games.fe11 import load_stats
from server.services.account.sessions import SessionStore
from server.services.gamestats import GamestatsServer, encode_message, xor_gamespy
from server.services.store import PlayerStore

_FINAL = b"\\final\\"


class HandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "nds.sqlite"
        self.players = PlayerStore(self.path)
        self.players.remember_profile("4", "YFEE", "42")
        self.sessions = SessionStore(self.players)
        game = load_stats()
        kinds = {game.gamename: game.kind}
        self.server = GamestatsServer("127.0.0.1", 0, self.players, self.sessions, kinds)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.server.join(timeout=2)
        self.players.close()
        self._tmp.cleanup()

    def test_login_then_player_auth_returns_the_profile(self) -> None:
        token = self.sessions.login("4", "YFEE").token
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            challenge = xor_gamespy(_recv(sock, 38))
            self.assertEqual(len(challenge), 38)
            self.assertIn(b"\\challenge\\", challenge)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            logged = _plain(_read(sock))
            self.assertIn(b"\\sesskey\\", logged)
            claimed = b"\\authp\\\\authtoken\\" + token.encode("ascii")
            claimed += b"\\pid\\99\\resp\\abcd\\lid\\2"
            sock.sendall(encode_message(claimed))
            authed = _plain(_read(sock))
        self.assertIn(b"\\pauthr\\42\\", authed)
        self.assertIn(b"\\lid\\2", authed)

    def test_a_unit_round_trips_and_survives_reopening_the_file(self) -> None:
        unit = b"\\record\\hero\\final\\still-here"
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"0", b"1", b"7", unit)))
            saved = _plain(_read(sock))
            self.assertTrue(saved.startswith(b"\\setpdr\\1\\"))
            self.assertIn(b"\\lid\\7\\", saved)
            self.assertIn(b"\\pid\\42\\", saved)
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\0\\dindex\\1\\keys\\\\lid\\8")
            )
            loaded = _plain(_read(sock))
        self.assertIn(b"\\length\\" + str(len(unit)).encode("ascii") + b"\\data\\", loaded)
        self.assertTrue(loaded.endswith(unit))
        reopened = PlayerStore(self.path)
        try:
            record = reopened.get_record("YFEE", "loan_unit", "42", "0:1")
        finally:
            reopened.close()
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.payload, unit)

    def test_a_missing_unit_has_length_zero(self) -> None:
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(b"\\auth\\\\gamename\\fireemblemds\\response\\x\\port\\0\\id\\1")
            )
            _read(sock)
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\1\\dindex\\0\\keys\\\\lid\\3")
            )
            loaded = _plain(_read(sock))
        self.assertIn(b"\\getpdr\\1\\", loaded)
        self.assertIn(b"\\length\\0\\data\\", loaded)

    def test_an_unknown_command_keeps_the_connection(self) -> None:
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(b"\\auth\\\\gamename\\fireemblemds\\response\\x\\port\\0\\id\\1")
            )
            _read(sock)
            sock.sendall(encode_message(b"\\something\\\\lid\\1"))
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\9\\dindex\\9\\keys\\\\lid\\4")
            )
            loaded = _plain(_read(sock))
        self.assertIn(b"\\length\\0\\", loaded)

    def test_cipher_text_may_contain_the_clear_terminator(self) -> None:
        prefix = b"\\setpd\\\\pid\\42\\ptype\\0\\dindex\\1\\kv\\1\\lid\\7\\length\\7\\data\\"
        unit = bytes(_FINAL[index] ^ _ENC[(len(prefix) + index) % len(_ENC)] for index in range(7))
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1")
            )
            _read(sock)
            sock.sendall(encode_message(prefix + unit))
            saved = _plain(_read(sock))
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\0\\dindex\\1\\keys\\\\lid\\8")
            )
            loaded = _plain(_read(sock))
        self.assertTrue(saved.startswith(b"\\setpdr\\1\\"))
        self.assertTrue(loaded.endswith(unit))

    def test_a_trailing_nul_is_left_out_of_the_reply(self) -> None:
        unit = b"\\rental00\\AQQA**\x00"
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", unit)))
            _read(sock)
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\\\lid\\8")
            )
            loaded = _plain(_read(sock))
        kept = unit[:-1]
        self.assertIn(b"\\length\\" + str(len(kept)).encode("ascii") + b"\\data\\", loaded)
        self.assertTrue(loaded.endswith(kept))
        self.assertFalse(loaded.endswith(b"\x00"))
        reopened = PlayerStore(self.path)
        try:
            record = reopened.get_record("YFEE", "loan_unit", "42", "3:0")
        finally:
            reopened.close()
        assert record is not None
        self.assertEqual(record.payload, kept)

    def test_a_request_for_another_key_does_not_repeat_the_unit(self) -> None:
        unit = b"\\rental00\\AQQA**"
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", unit)))
            _read(sock)
            sock.sendall(
                encode_message(
                    b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\rental00\\rental01\\lid\\8"
                )
            )
            both = _plain(_read(sock))
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\rental01\\lid\\9")
            )
            other = _plain(_read(sock))
            sock.sendall(
                encode_message(
                    b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\rental00\x01rental01\\lid\\10"
                )
            )
            packed = _plain(_read(sock))
        self.assertIn(b"\\rental00\\AQQA**", both)
        self.assertNotIn(b"rental01", both)
        self.assertIn(b"\\length\\0\\data\\", other)
        self.assertNotIn(b"AQQA", other)
        self.assertIn(b"\\rental00\\AQQA**", packed)
        self.assertNotIn(b"rental01", packed)

    def test_a_second_key_in_the_same_slot_keeps_the_first(self) -> None:
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", b"\\rental00\\ONE**")))
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", b"\\rental01\\TWO**")))
            _read(sock)
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\\\lid\\8")
            )
            loaded = _plain(_read(sock))
        self.assertIn(b"\\rental00\\ONE**", loaded)
        self.assertIn(b"\\rental01\\TWO**", loaded)

    def test_a_plain_upload_replaces_the_slot(self) -> None:
        with socket.create_connection(self.server.address, timeout=2) as sock:
            sock.settimeout(2)
            _recv(sock, 38)
            sock.sendall(
                encode_message(
                    b"\\auth\\\\gamename\\fireemblemds\\response\\abcd\\port\\0\\id\\1"
                )
            )
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", b"\\rental00\\ONE**")))
            _read(sock)
            sock.sendall(encode_message(_setpd(b"42", b"3", b"0", b"7", b"raw-bytes")))
            _read(sock)
            sock.sendall(
                encode_message(b"\\getpd\\\\pid\\42\\ptype\\3\\dindex\\0\\keys\\\\lid\\8")
            )
            loaded = _plain(_read(sock))
        self.assertTrue(loaded.endswith(b"raw-bytes"))
        self.assertNotIn(b"ONE", loaded)


_ENC = b"GameSpy3D"


def _setpd(pid: bytes, ptype: bytes, dindex: bytes, lid: bytes, unit: bytes) -> bytes:
    return (
        b"\\setpd\\\\pid\\"
        + pid
        + b"\\ptype\\"
        + ptype
        + b"\\dindex\\"
        + dindex
        + b"\\kv\\1\\lid\\"
        + lid
        + b"\\length\\"
        + str(len(unit)).encode("ascii")
        + b"\\data\\"
        + unit
    )


def _recv(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _plain(packet: bytes) -> bytes:
    end = packet.find(_FINAL)
    return xor_gamespy(packet[:end])


def _read(sock: socket.socket) -> bytes:
    data = b""
    while _FINAL not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


if __name__ == "__main__":
    unittest.main()
