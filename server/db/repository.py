"""Accounts, profiles, and player-uploaded records.

The engine comes from a URL. A filesystem path is SQLite. Accounts and
profiles survive a restart, so a friend code still names the same console.
Records are the shared deposit any game can write: a loan unit, a shop
listing, or whatever that game uploads. Static download files stay on disk.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from server.db.models import Account, Buddy, ProfileRow, RecordRow
from server.db.session import open_engine

LOGGER = logging.getLogger(__name__)

_MAX_PAYLOAD = 256 * 1024
_MAX_LABEL = 128
_MAX_KEY = 32
_PROFILE_MAX = 0x7FFFFFFF
_PROFILE_FIELDS = ("firstname", "lastname", "nick", "uniquenick", "email", "partnerid")
_T = TypeVar("_T")


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
    """One database. A lock covers every transaction."""

    def __init__(self, database: Path | str) -> None:
        self._lock = threading.Lock()
        self._engine = open_engine(database)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        try:
            from server.seed import apply_seeds

            apply_seeds(self)
        except Exception:
            self._engine.dispose()
            raise

    def close(self) -> None:
        with self._lock:
            self._engine.dispose()

    def apply_script(self, script: str) -> None:
        """Run a seed script and commit it."""
        statements = _statements(script)

        def run(session: Session) -> None:
            for statement in statements:
                session.execute(_text(statement))

        self._commit(run)

    def create_account(self) -> str:
        """The next userid. Reopening the database continues the sequence."""

        def run(session: Session) -> str:
            current = session.scalar(
                select(Account.userid).order_by(Account.userid.desc()).limit(1)
            )
            userid = int(current or 0) + 1
            session.add(Account(userid=userid, created_at=int(time.time())))
            return str(userid)

        return self._commit(run)

    def profile_for(self, userid: str, gamecd: str) -> Profile | None:
        if not userid.isdigit():
            return None

        def run(session: Session) -> Profile | None:
            row = session.scalar(
                select(ProfileRow).where(
                    ProfileRow.userid == int(userid),
                    ProfileRow.gamecd == _key(gamecd),
                )
            )
            return _profile(row) if row is not None else None

        return self._commit(run)

    def profile_by_id(self, profileid: str) -> Profile | None:
        if not profileid.isdigit():
            return None

        def run(session: Session) -> Profile | None:
            row = session.get(ProfileRow, int(profileid))
            return _profile(row) if row is not None else None

        return self._commit(run)

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

        def run(session: Session) -> str:
            chosen = pid
            _ensure_account(session, uid, now)
            current = session.scalar(
                select(ProfileRow).where(ProfileRow.userid == uid, ProfileRow.gamecd == game)
            )
            owner = session.get(ProfileRow, chosen)
            if owner is not None and (owner.userid != uid or owner.gamecd != game):
                LOGGER.warning(
                    "Profile %s belongs to userid %s; keeping a different id for userid %s",
                    chosen,
                    owner.userid,
                    uid,
                )
                if current is not None:
                    chosen = int(current.profileid)
                else:
                    chosen = _unused_profile_id(session)
            if current is None:
                session.add(
                    ProfileRow(
                        profileid=chosen,
                        userid=uid,
                        gamecd=game,
                        uniquenick=_key(f"fe{chosen}", limit=32),
                        lastname="",
                        firstname="",
                        nick="",
                        email="",
                        partnerid="",
                        updated_at=now,
                    )
                )
            else:
                session.execute(
                    update(ProfileRow)
                    .where(ProfileRow.userid == uid, ProfileRow.gamecd == game)
                    .values(profileid=chosen, updated_at=now)
                )
            return str(chosen)

        return self._commit(run)

    def update_profile(self, profileid: str, fields: dict[str, str]) -> None:
        chosen = {
            key: value for key, value in fields.items() if key in _PROFILE_FIELDS and value
        }
        if not chosen or not profileid.isdigit():
            return

        def run(session: Session) -> None:
            row = session.get(ProfileRow, int(profileid))
            if row is None:
                return
            for key, value in chosen.items():
                setattr(row, key, value)
            row.updated_at = int(time.time())

        self._commit(run)

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

        def run(session: Session) -> int:
            insert_row = _dialect_insert(session)
            statement = insert_row(RecordRow).values(
                gamecd=game,
                kind=record_kind,
                owner_profileid=int(owner_profileid),
                slot=_key(slot, limit=64),
                label=_key(label, limit=_MAX_LABEL),
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            statement = statement.on_conflict_do_update(
                index_elements=["gamecd", "kind", "owner_profileid", "slot"],
                set_={
                    "label": statement.excluded.label,
                    "payload": statement.excluded.payload,
                    "updated_at": statement.excluded.updated_at,
                },
            )
            try:
                session.execute(statement)
            except IntegrityError as exc:
                raise ValueError(f"profile {owner_profileid} is not stored") from exc
            return now

        return self._commit(run)

    def list_records(self, gamecd: str, kind: str) -> list[Record]:
        def run(session: Session) -> list[Record]:
            rows = session.scalars(
                select(RecordRow)
                .where(RecordRow.gamecd == _key(gamecd), RecordRow.kind == _key(kind))
                .order_by(RecordRow.id)
            )
            return [_record(row) for row in rows]

        return self._commit(run)

    def get_record(self, gamecd: str, kind: str, owner_profileid: str, slot: str) -> Record | None:
        if not owner_profileid.isdigit():
            return None

        def run(session: Session) -> Record | None:
            row = session.scalar(
                select(RecordRow).where(
                    RecordRow.gamecd == _key(gamecd),
                    RecordRow.kind == _key(kind),
                    RecordRow.owner_profileid == int(owner_profileid),
                    RecordRow.slot == _key(slot, limit=64),
                )
            )
            return _record(row) if row is not None else None

        return self._commit(run)

    def delete_record(self, gamecd: str, kind: str, owner_profileid: str, slot: str) -> bool:
        if not owner_profileid.isdigit():
            return False

        def run(session: Session) -> bool:
            row = session.scalar(
                select(RecordRow).where(
                    RecordRow.gamecd == _key(gamecd),
                    RecordRow.kind == _key(kind),
                    RecordRow.owner_profileid == int(owner_profileid),
                    RecordRow.slot == _key(slot, limit=64),
                )
            )
            if row is None:
                return False
            session.delete(row)
            return True

        return self._commit(run)

    def add_buddy(self, owner: str, friend: str) -> None:
        """Remember that one profile lists another as a friend."""
        if not owner.isdigit() or not friend.isdigit() or owner == friend:
            return

        def run(session: Session) -> None:
            statement = _dialect_insert(session)(Buddy).values(
                owner_profileid=int(owner),
                friend_profileid=int(friend),
            )
            session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["owner_profileid", "friend_profileid"]
                )
            )

        self._commit(run)

    def remove_buddy(self, owner: str, friend: str) -> None:
        if not owner.isdigit() or not friend.isdigit():
            return

        def run(session: Session) -> None:
            row = session.get(Buddy, (int(owner), int(friend)))
            if row is not None:
                session.delete(row)

        self._commit(run)

    def list_buddies(self, owner: str) -> list[str]:
        if not owner.isdigit():
            return []

        def run(session: Session) -> list[str]:
            rows = session.scalars(
                select(Buddy.friend_profileid)
                .where(Buddy.owner_profileid == int(owner))
                .order_by(Buddy.friend_profileid)
            )
            return [str(friend) for friend in rows]

        return self._commit(run)

    def _commit(self, work: Callable[[Session], _T]) -> _T:
        with self._lock:
            with self._sessions.begin() as session:
                return work(session)


def _ensure_account(session: Session, userid: int, now: int) -> None:
    statement = _dialect_insert(session)(Account).values(userid=userid, created_at=now)
    session.execute(statement.on_conflict_do_nothing(index_elements=["userid"]))


def _unused_profile_id(session: Session) -> int:
    """A profile id in the range the console can store, not used yet."""
    seed = int.from_bytes(hashlib.md5(str(time.time_ns()).encode()).digest()[:4], "big")
    candidate = seed & _PROFILE_MAX or 1
    for _ in range(1024):
        taken = session.get(ProfileRow, candidate)
        if taken is None:
            return candidate
        candidate = candidate + 1 if candidate < _PROFILE_MAX else 1
    raise ValueError("no free profile id")


def _dialect_insert(session: Session) -> Any:
    if session.get_bind().dialect.name == "postgresql":
        return postgres_insert
    return sqlite_insert


def _text(statement: str):
    from sqlalchemy import text

    return text(statement)


def _statements(script: str) -> list[str]:
    lines: list[str] = []
    for line in script.splitlines():
        comment = line.find("--")
        if comment >= 0:
            line = line[:comment]
        lines.append(line)
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def _key(value: str, limit: int = _MAX_KEY) -> str:
    cleaned = "".join(char for char in value if 32 <= ord(char) < 127)
    return cleaned[:limit]


def _profile(row: ProfileRow) -> Profile:
    return Profile(
        profileid=str(row.profileid),
        userid=str(row.userid),
        gamecd=row.gamecd,
        uniquenick=row.uniquenick,
        lastname=row.lastname,
        firstname=row.firstname,
        nick=row.nick,
        email=row.email,
        partnerid=row.partnerid,
    )


def _record(row: RecordRow) -> Record:
    return Record(
        gamecd=row.gamecd,
        kind=row.kind,
        owner_profileid=str(row.owner_profileid),
        slot=row.slot,
        label=row.label,
        payload=bytes(row.payload),
        updated_at=int(row.updated_at),
    )
