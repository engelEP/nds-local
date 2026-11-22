"""Profile nicks returned on the GameSpy search port."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from server.db.repository import PlayerStore
from server.services.account.presence import parse_messages
from server.services.account.search import SearchServer, others_reply


class OthersReplyTests(unittest.TestCase):
    def test_nicks_follow_the_profile_id_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            players = PlayerStore(Path(tmp) / "nds.sqlite")
            try:
                players.remember_profile("4", "YFEP", "42")
                reply = others_reply(players, "900000001|42|0")
            finally:
                players.close()
        text = reply.decode("ascii")
        self.assertLess(text.index("\\o\\42\\"), text.index("\\o\\900000001\\"))
        self.assertIn("\\uniquenick\\fe42\\", text)
        self.assertIn("\\uniquenick\\fe900000001\\", text)
        self.assertNotIn("\\o\\0\\", text)
        self.assertTrue(text.endswith("\\oldone\\\\final\\"))

    def test_a_live_query_is_answered_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            players = PlayerStore(Path(tmp) / "nds.sqlite")
            server = SearchServer("127.0.0.1", 0, players)
            server.start()
            try:
                with socket.create_connection(server.address, timeout=2) as sock:
                    sock.settimeout(2)
                    sock.sendall(
                        b"\\otherslist\\\\sesskey\\1\\profileid\\42\\numopids\\1"
                        b"\\opids\\900000001\\namespaceid\\16\\gamename\\fireemblemds\\final\\"
                    )
                    data = sock.recv(4096)
            finally:
                server.stop()
                server.join(timeout=2)
                players.close()
        message, _rest = parse_messages(data.decode("ascii"))
        self.assertEqual(message[0]["o"], "900000001")
        self.assertEqual(message[0]["uniquenick"], "fe900000001")
        self.assertIn("oldone", message[0])


if __name__ == "__main__":
    unittest.main()
