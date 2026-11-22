"""Engine URL and schema migrations.

A path becomes a SQLite URL. A URL with a driver is used as given, so the
same store can open Postgres when that service exists.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.pool import NullPool

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_LEGACY_TABLES = frozenset({"accounts", "profiles", "records", "buddies"})
_LEGACY_REVISION = "0001_initial"
# Catalog rows used to live in a revision. Those rows are seeds now, so a
# database stamped with the old revision moves back to the schema revision.
_RETIRED_REVISIONS = {"0002_fe11_loan_units": "0001_initial"}


def to_database_url(value: Path | str) -> str:
    """A filesystem path becomes ``sqlite:///...``. A URL stays a URL."""
    text_value = str(value)
    if "://" in text_value:
        return make_url(text_value).render_as_string(hide_password=False)
    return URL.create("sqlite", database=Path(text_value).expanduser().as_posix()).render_as_string(
        hide_password=False
    )


class NoRevision(RuntimeError):
    """Downgrade was asked for and the database has no Alembic revision."""


def open_engine(database: Path | str) -> Engine:
    """Open the engine and apply migrations."""
    engine = _engine(database)
    try:
        migrate(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def downgrade_database(database: Path | str, step: str = "-1") -> None:
    """Undo one revision. ``step`` is an Alembic target such as ``-1``."""
    if _sqlite_file_missing(to_database_url(database)):
        raise NoRevision("the database has no revision to undo")
    engine = _engine(database)
    try:
        config = _alembic_config(engine)
        with engine.connect() as connection:
            if not _revision_present(connection):
                raise NoRevision("the database has no revision to undo")
            if _remap_retired(connection):
                connection.commit()
                raise NoRevision(
                    "the database was on a removed catalog revision and is now at "
                    "0001_initial. Run --down again to drop the tables."
                )
            connection.commit()
            config.attributes["connection"] = connection
            command.downgrade(config, step)
            if connection.in_transaction():
                connection.commit()
    finally:
        engine.dispose()


def _engine(database: Path | str) -> Engine:
    url = _prepare_sqlite(to_database_url(database))
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        _enable_sqlite(engine)
        return engine
    return create_engine(url, pool_pre_ping=True)


def migrate(engine: Engine) -> None:
    """Move the schema to the latest revision.

    A database created before Alembic already has the four tables. It is
    stamped at that initial revision so those rows stay, then later revisions
    run.
    """
    config = _alembic_config(engine)
    with engine.connect() as connection:
        legacy = _legacy_without_revision(connection)
        _remap_retired(connection)
        connection.commit()
        config.attributes["connection"] = connection
        if legacy:
            command.stamp(config, _LEGACY_REVISION)
            if connection.in_transaction():
                connection.commit()
        command.upgrade(config, "head")
        if connection.in_transaction():
            connection.commit()


def _alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return config


def _revision_present(connection: Connection) -> bool:
    names = set(inspect(connection).get_table_names())
    if "alembic_version" not in names:
        return False
    row = connection.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    return row is not None and bool(row[0])


def _sqlite_file_missing(url: str) -> bool:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return False
    if not parsed.database or parsed.database == ":memory:":
        return False
    path = Path(parsed.database)
    if not path.is_absolute():
        path = Path.cwd() / path
    return not path.is_file()


def _prepare_sqlite(url: str) -> str:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return url
    if not parsed.database or parsed.database == ":memory:":
        return url
    path = Path(parsed.database)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return parsed.set(database=path.resolve().as_posix()).render_as_string(hide_password=False)


def _enable_sqlite(engine: Engine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _remap_retired(connection: Connection) -> bool:
    """Point a removed catalog revision at the schema revision. Rows stay."""
    names = set(inspect(connection).get_table_names())
    if "alembic_version" not in names:
        return False
    row = connection.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    if row is None or row[0] not in _RETIRED_REVISIONS:
        return False
    connection.execute(
        text("UPDATE alembic_version SET version_num = :version"),
        {"version": _RETIRED_REVISIONS[row[0]]},
    )
    return True


def _legacy_without_revision(connection: Connection) -> bool:
    names = set(inspect(connection).get_table_names())
    if "alembic_version" in names:
        return False
    return _LEGACY_TABLES <= names
