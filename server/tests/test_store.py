"""Accounts, profiles, and player records in the SQLite file."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.services.account.sessions import SessionStore
from server.services.store import PlayerStore


class AccountTests(unittest.TestCase):
    def test_userids_continue_after_the_file_is_reopened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nds.sqlite"
            first = PlayerStore(path)
            opened = SessionStore(first).create_account().userid
            first.close()
            second = PlayerStore(path)
            try:
                continued = SessionStore(second).create_account().userid
            finally:
                second.close()
        self.assertEqual(opened, "1")
        self.assertEqual(continued, "2")


class ProfileTests(unittest.TestCase):
    def test_profile_survives_reopening_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nds.sqlite"
            store = PlayerStore(path)
            kept = store.remember_profile("4", "YFEE", "42")
            store.update_profile("42", {"lastname": "000000000YFEE2ml3d1m", "partnerid": "11"})
            store.close()
            reopened = PlayerStore(path)
            try:
                profile = reopened.profile_for("4", "YFEE")
            finally:
                reopened.close()
        self.assertEqual(kept, "42")
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.profileid, "42")
        self.assertEqual(profile.lastname, "000000000YFEE2ml3d1m")
        self.assertEqual(profile.partnerid, "11")


class RecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = PlayerStore(Path(self._tmp.name) / "nds.sqlite")
        self.store.remember_profile("4", "YFEE", "42")
        self.store.remember_profile("5", "YCOE", "7")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_records_are_listed_read_and_deleted_per_game(self) -> None:
        self.store.put_record("YFEE", "loan_unit", "42", "0", "Abel", b"unit-a")
        self.store.put_record("YFEE", "loan_unit", "42", "1", "Cain", b"unit-b")
        self.store.put_record("YCOE", "shop_listing", "7", "0", "potion", b"shop")
        listed = self.store.list_records("YFEE", "loan_unit")
        self.assertEqual([item.label for item in listed], ["Abel", "Cain"])
        self.assertEqual(listed[0].payload, b"unit-a")
        self.assertEqual(self.store.list_records("YCOE", "loan_unit"), [])
        found = self.store.get_record("YFEE", "loan_unit", "42", "0")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.payload, b"unit-a")
        self.store.put_record("YFEE", "loan_unit", "42", "0", "Abel", b"unit-a2")
        replaced = self.store.get_record("YFEE", "loan_unit", "42", "0")
        assert replaced is not None
        self.assertEqual(replaced.payload, b"unit-a2")
        self.assertTrue(self.store.delete_record("YFEE", "loan_unit", "42", "0"))
        self.assertIsNone(self.store.get_record("YFEE", "loan_unit", "42", "0"))
        self.assertFalse(self.store.delete_record("YFEE", "loan_unit", "42", "0"))

    def test_oversized_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put_record("YFEE", "loan_unit", "42", "0", "Abel", b"x" * (256 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
