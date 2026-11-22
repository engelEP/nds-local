"""SQLite store for accounts, profiles, and player-uploaded records.

Accounts and profiles survive a restart, so a friend code still names the
same console. Records are the shared deposit any game can write: a loan
unit, a shop listing, or whatever that game uploads. Static download files
stay on disk.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_MAX_PAYLOAD = 256 * 1024
_MAX_LABEL = 128
_MAX_KEY = 32
_PROFILE_MAX = 0x7FFFFFFF
_PROFILE_FIELDS = ("firstname", "lastname", "nick", "uniquenick", "email", "partnerid")


@dataclass(frozen=True)
class Profile:
    profileid: str
    userid: str
    gamecd: str
    uniquenick: str
    lastname: str
    firstname: str
    nick: str
    email: str
    partnerid: str


@dataclass(frozen=True)
class Record:
    gamecd: str
    kind: str
    owner_profileid: str
    slot: str
    label: str
    payload: bytes
    updated_at: int = 0


class PlayerStore:
    """One SQLite file. A lock covers every statement."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._create_tables()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def apply_script(self, script: str) -> None:
        """Run a seed script and commit it."""
        with self._lock:
            self._conn.executescript(script)
            self._conn.commit()

    def create_account(self) -> str:
        """The next userid. Reopening the file continues the sequence."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(userid), 0) + 1 AS next_id FROM accounts"
            ).fetchone()
            userid = int(row["next_id"])
            self._conn.execute(
                "INSERT INTO accounts (userid, created_at) VALUES (?, ?)",
                (userid, int(time.time())),
            )
            self._conn.commit()
            return str(userid)

    def profile_for(self, userid: str, gamecd: str) -> Profile | None:
        if not userid.isdigit():
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM profiles WHERE userid = ? AND gamecd = ?",
                (int(userid), _key(gamecd)),
            ).fetchone()
        return _profile(row) if row is not None else None

    def profile_by_id(self, profileid: str) -> Profile | None:
        if not profileid.isdigit():
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM profiles WHERE profileid = ?",
                (int(profileid),),
            ).fetchone()
        return _profile(row) if row is not None else None

    def remember_profile(self, userid: str, gamecd: str, profileid: str) -> str:
        """Store the console's profile id and return the id that was kept.

        A profile id that already belongs to another account is left alone.
        The previous id for this account is returned instead.
        """
        if not userid.isdigit() or not profileid.isdigit():
            return profileid
        uid = int(userid)
        pid = int(profileid)
        game = _key(gamecd)
        now = int(time.time())
        with self._lock:
            self._ensure_account(uid, now)
            current = self._conn.execute(
                "SELECT profileid FROM profiles WHERE userid = ? AND gamecd = ?",
                (uid, game),
            ).fetchone()
            owner = self._conn.execute(
                "SELECT userid, gamecd FROM profiles WHERE profileid = ?",
                (pid,),
            ).fetchone()
            if owner is not None and (int(owner["userid"]) != uid or owner["gamecd"] != game):
                LOGGER.warning(
                    "Profile %s belongs to userid %s; keeping a different id for userid %s",
                    pid,
                    owner["userid"],
                    uid,
                )
                if current is not None:
                    pid = int(current["profileid"])
                else:
                    pid = self._unused_profile_id()
            if current is None:
                self._conn.execute(
                    """
                    INSERT INTO profiles (
                        profileid, userid, gamecd, uniquenick, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (pid, uid, game, _key(f"fe{pid}", limit=32), now),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE profiles
                    SET profileid = ?, updated_at = ?
                    WHERE userid = ? AND gamecd = ?
                    """,
                    (pid, now, uid, game),
                )
            self._conn.commit()
            return str(pid)

    def update_profile(self, profileid: str, fields: dict[str, str]) -> None:
        chosen = {
            key: value
            for key, value in fields.items()
            if key in _PROFILE_FIELDS and value
        }
        if not chosen or not profileid.isdigit():
            return
        assignments = ", ".join(f"{key} = ?" for key in chosen)
        values: list[str | int] = [
            *chosen.values(),
            int(time.time()),
            int(profileid),
        ]
        with self._lock:
            self._conn.execute(
                f"UPDATE profiles SET {assignments}, updated_at = ? WHERE profileid = ?",
                values,
            )
            self._conn.commit()

    def put_record(
        self,
        gamecd: str,
        kind: str,
        owner_profileid: str,
        slot: str,
        label: str,
        payload: bytes,
    ) -> int:
        if len(payload) > _MAX_PAYLOAD:
            raise ValueError(f"record is larger than {_MAX_PAYLOAD} bytes")
        if not owner_profileid.isdigit():
            raise ValueError("owner profile id is not a number")
        game = _key(gamecd)
        record_kind = _key(kind)
        if not game or not record_kind:
            raise ValueError("game code and kind are required")
        now = int(time.time())
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO records (
                        gamecd, kind, owner_profileid, slot, label, payload, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (gamecd, kind, owner_profileid, slot) DO UPDATE SET
                        label = excluded.label,
                        payload = excluded.payload,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game,
                        record_kind,
                        int(owner_profileid),
                        _key(slot, limit=64),
                        _key(label, limit=_MAX_LABEL),
                        payload,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise ValueError(f"profile {owner_profileid} is not stored") from exc
            self._conn.commit()
            return now

    def list_records(self, gamecd: str, kind: str) -> list[Record]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM records
                WHERE gamecd = ? AND kind = ?
                ORDER BY id
                """,
                (_key(gamecd), _key(kind)),
            ).fetchall()
        return [_record(row) for row in rows]

    def get_record(self, gamecd: str, kind: str, owner_profileid: str, slot: str) -> Record | None:
        if not owner_profileid.isdigit():
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM records
                WHERE gamecd = ? AND kind = ? AND owner_profileid = ? AND slot = ?
                """,
                (_key(gamecd), _key(kind), int(owner_profileid), _key(slot, limit=64)),
            ).fetchone()
        return _record(row) if row is not None else None

    def delete_record(self, gamecd: str, kind: str, owner_profileid: str, slot: str) -> bool:
        if not owner_profileid.isdigit():
            return False
        with self._lock:
            cursor = self._conn.execute(
                """
                DELETE FROM records
                WHERE gamecd = ? AND kind = ? AND owner_profileid = ? AND slot = ?
                """,
                (_key(gamecd), _key(kind), int(owner_profileid), _key(slot, limit=64)),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def add_buddy(self, owner: str, friend: str) -> None:
        """Remember that one profile lists another as a friend."""
        if not owner.isdigit() or not friend.isdigit() or owner == friend:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO buddies (owner_profileid, friend_profileid)
                VALUES (?, ?)
                """,
                (int(owner), int(friend)),
            )
            self._conn.commit()

    def remove_buddy(self, owner: str, friend: str) -> None:
        if not owner.isdigit() or not friend.isdigit():
            return
        with self._lock:
            self._conn.execute(
                "DELETE FROM buddies WHERE owner_profileid = ? AND friend_profileid = ?",
                (int(owner), int(friend)),
            )
            self._conn.commit()

    def list_buddies(self, owner: str) -> list[str]:
        if not owner.isdigit():
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT friend_profileid FROM buddies
                WHERE owner_profileid = ?
                ORDER BY friend_profileid
                """,
                (int(owner),),
            ).fetchall()
        return [str(row["friend_profileid"]) for row in rows]

    def _unused_profile_id(self) -> int:
        """A profile id in the range the console can store, not used yet."""
        seed = int.from_bytes(hashlib.md5(str(time.time_ns()).encode()).digest()[:4], "big")
        candidate = seed & _PROFILE_MAX or 1
        for _ in range(1024):
            taken = self._conn.execute(
                "SELECT 1 FROM profiles WHERE profileid = ?",
                (candidate,),
            ).fetchone()
            if taken is None:
                return candidate
            candidate = candidate + 1 if candidate < _PROFILE_MAX else 1
        raise ValueError("no free profile id")

    def _ensure_account(self, userid: int, now: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO accounts (userid, created_at) VALUES (?, ?)",
            (userid, now),
        )

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                userid INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
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
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                gamecd TEXT NOT NULL,
                kind TEXT NOT NULL,
                owner_profileid INTEGER NOT NULL REFERENCES profiles(profileid) ON UPDATE CASCADE,
                slot TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                payload BLOB NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE (gamecd, kind, owner_profileid, slot)
            );
            CREATE TABLE IF NOT EXISTS buddies (
                owner_profileid INTEGER NOT NULL,
                friend_profileid INTEGER NOT NULL,
                PRIMARY KEY (owner_profileid, friend_profileid)
            );
            """
        )
        self._conn.commit()


def _key(value: str, limit: int = _MAX_KEY) -> str:
    cleaned = "".join(char for char in value if 32 <= ord(char) < 127)
    return cleaned[:limit]


def _profile(row: sqlite3.Row) -> Profile:
    return Profile(
        profileid=str(row["profileid"]),
        userid=str(row["userid"]),
        gamecd=row["gamecd"],
        uniquenick=row["uniquenick"],
        lastname=row["lastname"],
        firstname=row["firstname"],
        nick=row["nick"],
        email=row["email"],
        partnerid=row["partnerid"],
    )


def _record(row: sqlite3.Row) -> Record:
    payload = row["payload"]
    return Record(
        gamecd=row["gamecd"],
        kind=row["kind"],
        owner_profileid=str(row["owner_profileid"]),
        slot=row["slot"],
        label=row["label"],
        payload=bytes(payload),
        updated_at=int(row["updated_at"]),
    )
