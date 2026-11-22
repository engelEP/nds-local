"""Accounts, profiles, and player records."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.config import ServerConfig
from server.db.repository import PlayerStore
from server.db.session import NoRevision, downgrade_database, to_database_url
from server.services.account.sessions import SessionStore


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
        self.assertEqual(opened, "900000002")
        self.assertEqual(continued, "900000003")


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

    def test_the_mock_profile_offers_four_loan_units(self) -> None:
        fresh = PlayerStore(Path(self._tmp.name) / "catalog.sqlite")
        try:
            fresh.remember_profile("4", "YFEP", "42")
            fresh.put_record("YFEP", "loan_unit", "42", "0", "Abel", b"unit-a")
            unit = fresh.get_record("YFEP", "loan_unit", "900000001", "3:0")
            kept = fresh.get_record("YFEP", "loan_unit", "42", "0")
            profile = fresh.profile_for("900000001", "YFEP")
            buddies = fresh.list_buddies("900000001")
        finally:
            fresh.close()
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.profileid, "900000001")
        self.assertEqual(profile.nick, "")
        self.assertIsNotNone(unit)
        assert unit is not None
        text = unit.payload.decode("latin1")
        for key in ("rental00", "rental01", "rental02", "rental03"):
            self.assertIn(f"\\{key}\\", text)
        self.assertNotIn("\\rental04\\", text)
        assert kept is not None
        self.assertEqual(kept.payload, b"unit-a")
        self.assertEqual(buddies, [])

    def test_oversized_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.put_record("YFEE", "loan_unit", "42", "0", "Abel", b"x" * (256 * 1024 + 1))

    def test_an_existing_file_is_stamped_and_keeps_its_rows(self) -> None:
        path = Path(self._tmp.name) / "legacy.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE accounts (
                userid INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE profiles (
                profileid INTEGER PRIMARY KEY,
                userid INTEGER NOT NULL REFERENCES accounts(userid),
                gamecd TEXT NOT NULL,
                uniquenick TEXT NOT NULL DEFAULT '',
                lastname TEXT NOT NULL DEFAULT '',
                firstname TEXT NOT NULL DEFAULT '',
                nick TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                partnerid TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                UNIQUE (userid, gamecd)
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY,
                gamecd TEXT NOT NULL,
                kind TEXT NOT NULL,
                owner_profileid INTEGER NOT NULL REFERENCES profiles(profileid)
                    ON UPDATE CASCADE,
                slot TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                payload BLOB NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE (gamecd, kind, owner_profileid, slot)
            );
            CREATE TABLE buddies (
                owner_profileid INTEGER NOT NULL,
                friend_profileid INTEGER NOT NULL,
                PRIMARY KEY (owner_profileid, friend_profileid)
            );
            INSERT INTO accounts (userid, created_at) VALUES (4, 10);
            INSERT INTO profiles (
                profileid, userid, gamecd, uniquenick, updated_at
            ) VALUES (42, 4, 'YFEP', 'fe42', 10);
            """
        )
        connection.commit()
        connection.close()
        opened = PlayerStore(path)
        try:
            profile = opened.profile_for("4", "YFEP")
        finally:
            opened.close()
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.profileid, "42")
        stamped = sqlite3.connect(path)
        try:
            revision = stamped.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            stamped.close()
        assert revision is not None
        self.assertEqual(revision[0], "0001_initial")

    def test_down_removes_the_tables(self) -> None:
        path = Path(self._tmp.name) / "down.sqlite"
        store = PlayerStore(path)
        store.close()
        downgrade_database(path)
        connection = sqlite3.connect(path)
        try:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            names = {row[0] for row in rows}
        finally:
            connection.close()
        self.assertNotIn("accounts", names)
        self.assertNotIn("profiles", names)
        self.assertNotIn("records", names)

    def test_a_retired_catalog_revision_returns_to_the_schema(self) -> None:
        path = Path(self._tmp.name) / "retired.sqlite"
        store = PlayerStore(path)
        store.close()
        _stamp(path, "0002_fe11_loan_units")
        opened = PlayerStore(path)
        try:
            unit = opened.get_record("YFEP", "loan_unit", "900000001", "3:0")
        finally:
            opened.close()
        self.assertIsNotNone(unit)
        self.assertEqual(_revision(path), "0001_initial")
        held = Path(self._tmp.name) / "held.sqlite"
        held_store = PlayerStore(held)
        held_store.close()
        _stamp(held, "0002_fe11_loan_units")
        with self.assertRaises(NoRevision):
            downgrade_database(held)
        self.assertEqual(_revision(held), "0001_initial")
        connection = sqlite3.connect(held)
        try:
            names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()
        self.assertIn("records", names)

    def test_down_without_a_file_does_not_create_one(self) -> None:
        path = Path(self._tmp.name) / "missing.sqlite"
        with self.assertRaises(NoRevision):
            downgrade_database(path)
        self.assertFalse(path.exists())


def _stamp(path: Path, revision: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("UPDATE alembic_version SET version_num = ?", (revision,))
    connection.commit()
    connection.close()


def _revision(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


class DatabaseUrlTests(unittest.TestCase):
    def test_a_path_or_a_postgres_url_is_accepted(self) -> None:
        self.assertEqual(to_database_url(Path("/tmp/nds.sqlite")), "sqlite:////tmp/nds.sqlite")
        file_config = ServerConfig(database="/tmp/nds.sqlite")
        self.assertEqual(file_config.database, "sqlite:////tmp/nds.sqlite")
        url = "postgresql+psycopg://nds:secret@db:5432/nds"
        remote = ServerConfig(database=url)
        self.assertEqual(remote.database, url)


if __name__ == "__main__":
    unittest.main()
