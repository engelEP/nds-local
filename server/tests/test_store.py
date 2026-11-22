"""Accounts, profiles, and player records in the SQLite file."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.games import load_seeds as load_game_seeds
from server.seed import load_seeds as load_common_seeds
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

    def test_a_taken_profile_id_is_not_given_to_another_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PlayerStore(Path(tmp) / "nds.sqlite")
            try:
                store.remember_profile("4", "YFEE", "42")
                store.update_profile("42", {"uniquenick": "marth"})
                other = store.remember_profile("5", "YFEE", "42")
                store.remember_profile("4", "YFEE", "42")
                owner = store.profile_by_id("42")
                moved = store.profile_for("5", "YFEE")
            finally:
                store.close()
        self.assertNotEqual(other, "42")
        assert owner is not None
        self.assertEqual(owner.userid, "4")
        self.assertEqual(owner.uniquenick, "marth")
        assert moved is not None
        self.assertEqual(moved.profileid, other)


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

    def test_seeds_install_a_clean_profile_and_the_registered_unit(self) -> None:
        registered = bytes.fromhex(
            "5c72656e74616c30305c41515141426741444177414642514942414959423841414141"
            "4377424c414757414867414141414858686341416a414941414141414141414141414141"
            "51414174414d41414141414151424952614d7759686a454d4f55306146474a5761516778"
            "53544e52535a4a523032444847684e426a6d7157536339374632434a4d51733554434a55"
            "515931716c556d5155644636324743494f51306f7951464f63516f4c57626b4d4f556f77"
            "796a6b4c4f70563569426d5161684a535330464c514931777943475155553169546e6b48"
            "47494d7079314a4a655551307a5948456563557a6a47655237592e456935334d74733632"
            "5559314c6c512e6d5459395534554d435230324d6e67362d55615a50727043566a6f6454"
            "39704b4e447237546f347032553471495a5a4b537956744a5777706d6a37635274744b50"
            "6c66534c5251327555726154706c47305448365668492e5a41796d46412a2a00"
        )
        fresh = PlayerStore(Path(self._tmp.name) / "empty.sqlite")
        try:
            for script in load_common_seeds():
                fresh.apply_script(script)
            self.assertIsNone(fresh.get_record("YFEP", "loan_unit", "900000001", "3:0"))
            fresh.remember_profile("4", "YFEP", "42")
            fresh.put_record("YFEP", "loan_unit", "42", "0", "Abel", b"unit-a")
            fresh.put_record("YFEP", "loan_unit", "900000001", "3:0", "mock", b"edited")
            for script in (*load_common_seeds(), *load_game_seeds()):
                fresh.apply_script(script)
                fresh.apply_script(script)
            profile = fresh.profile_for("900000001", "YFEP")
            unit = fresh.get_record("YFEP", "loan_unit", "900000001", "3:0")
            kept = fresh.get_record("YFEP", "loan_unit", "42", "0")
            buddies = fresh.list_buddies("900000001")
        finally:
            fresh.close()
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.profileid, "900000001")
        self.assertEqual(profile.nick, "")
        self.assertIsNotNone(unit)
        assert unit is not None
        self.assertEqual(unit.payload, registered)
        assert kept is not None
        self.assertEqual(kept.payload, b"unit-a")
        self.assertEqual(buddies, [])

    def test_oversized_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put_record("YFEE", "loan_unit", "42", "0", "Abel", b"x" * (256 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
