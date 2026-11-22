"""Rows applied after the schema migration.

``server/migrations`` changes tables. This package inserts the rows every
database needs: the stand-in profile (``*.sql``, in filename order) and
catalog modules that expose ``apply(store)``. Both are idempotent.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.db.repository import PlayerStore

LOGGER = logging.getLogger(__name__)


def load_seeds() -> list[str]:
    """SQL scripts in this folder, in filename order."""
    scripts: list[str] = []
    for path in sorted(Path(__file__).resolve().parent.glob("*.sql")):
        scripts.append(path.read_text(encoding="utf-8"))
        LOGGER.info("Loaded seed %s", path.name)
    return scripts


def apply_seeds(store: PlayerStore) -> None:
    """Insert seed rows. Safe to run again on a database that already has them."""
    for script in load_seeds():
        store.apply_script(script)
    for module in pkgutil.iter_modules([str(Path(__file__).resolve().parent)]):
        if module.name.startswith("_"):
            continue
        loaded = importlib.import_module(f"{__name__}.{module.name}")
        apply = getattr(loaded, "apply", None)
        if apply is None:
            continue
        LOGGER.info("Loaded seed %s", module.name)
        apply(store)
