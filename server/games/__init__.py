"""One package per game.

A package is included when it defines ``load_catalog()``. DNS, HTTP, and the
download route never import a game by name. Adding a game is a new folder
here, with its files beside its catalog.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

from server.services.downloads import GameCatalog

LOGGER = logging.getLogger(__name__)


def load_catalogs() -> list[GameCatalog]:
    """Load every game package that publishes a catalog."""
    catalogs: list[GameCatalog] = []
    here = Path(__file__).resolve().parent
    for path in sorted(here.iterdir()):
        if not _is_game_package(path):
            continue
        module = importlib.import_module(f"{__name__}.{path.name}")
        load = getattr(module, "load_catalog", None)
        if not callable(load):
            continue
        catalog = load()
        log_inventory = getattr(catalog, "log_inventory", None)
        if callable(log_inventory):
            log_inventory()
        catalogs.append(catalog)
        LOGGER.info("Loaded game package %s", path.name)
    return catalogs


def _is_game_package(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith("_") and (path / "__init__.py").is_file()
