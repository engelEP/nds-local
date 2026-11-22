"""Alembic environment. The process passes an open connection."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from server.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if isinstance(connection, Connection):
        _run(connection)
        return
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not set")
    engine = create_engine(url)
    with engine.connect() as opened:
        _run(opened)


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
