"""Tables shared by every game. The migration freezes this shape."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata Alembic compares against the database."""


class Account(Base):
    __tablename__ = "accounts"

    userid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger)


class ProfileRow(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("userid", "gamecd", name="uq_profiles_userid_gamecd"),)

    profileid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    userid: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounts.userid"))
    gamecd: Mapped[str] = mapped_column(Text)
    uniquenick: Mapped[str] = mapped_column(Text, default="", server_default="")
    lastname: Mapped[str] = mapped_column(Text, default="", server_default="")
    firstname: Mapped[str] = mapped_column(Text, default="", server_default="")
    nick: Mapped[str] = mapped_column(Text, default="", server_default="")
    email: Mapped[str] = mapped_column(Text, default="", server_default="")
    partnerid: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[int] = mapped_column(BigInteger)


class RecordRow(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint(
            "gamecd",
            "kind",
            "owner_profileid",
            "slot",
            name="uq_records_slot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gamecd: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    owner_profileid: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("profiles.profileid", onupdate="CASCADE"),
    )
    slot: Mapped[str] = mapped_column(Text, default="", server_default="")
    label: Mapped[str] = mapped_column(Text, default="", server_default="")
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class Buddy(Base):
    __tablename__ = "buddies"

    owner_profileid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    friend_profileid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
