"""Authentication responses, without opening a port."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from server.services.account.nas import NasService
from server.services.account.sessions import SessionStore, now_stamp, parse_form


class NasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nas = NasService(SessionStore(), "dls1.nintendowifi.net")

    def test_account_then_login(self) -> None:
        status, body = self.nas.handle("/ac", {"action": "acctcreate"})
        fields = parse_form(body)
        self.assertEqual(status, 200)
        self.assertEqual(fields["returncd"], "002")
        self.assertEqual(fields["userid"], "1")

        status, body = self.nas.handle("/ac", {"action": "login", "userid": fields["userid"]})
        logged = parse_form(body)
        self.assertEqual(status, 200)
        self.assertEqual(logged["returncd"], "001")
        self.assertEqual(len(logged["challenge"]), 8)
        self.assertTrue(logged["token"])

    def test_download_service_points_at_the_configured_host(self) -> None:
        _status, body = self.nas.handle("/ac", {"action": "svcloc", "svc": "9000", "userid": "4"})
        fields = parse_form(body)
        self.assertEqual(fields["returncd"], "007")
        self.assertEqual(fields["svchost"], "dls1.nintendowifi.net")
        self.assertIn("token", fields)
        self.assertNotIn("servicetoken", fields)

    def test_unknown_action_is_rejected(self) -> None:
        status, _body = self.nas.handle("/ac", {"action": "nope"})
        self.assertEqual(status, 400)


class StampTests(unittest.TestCase):
    def test_stamp_is_utc(self) -> None:
        with patch("server.services.account.sessions.time.gmtime", return_value=time.gmtime(0)):
            self.assertEqual(now_stamp(), "19700101000000")


if __name__ == "__main__":
    unittest.main()
