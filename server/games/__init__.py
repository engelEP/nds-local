"""One package per game.

A package is included when it defines ``load_catalog()`` or ``load_stats()``.
DNS, HTTP, and the stats port never import a game by name. Adding a game is
a new folder here, with its files beside its catalog.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from server.services.downloads import GameCatalog

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StatsGame:
    """Which record kind a GameSpy ``gamename`` stores."""

    gamename: str
    kind: str


def load_catalogs() -> list[GameCatalog]:
    """Load every game package that publishes a catalog."""
    catalogs: list[GameCatalog] = []
    for module, path in _packages():
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


def load_stats_games() -> dict[str, str]:
    """Map each registered ``gamename`` to the record kind it stores."""
    kinds: dict[str, str] = {}
    for module, path in _packages():
        load = getattr(module, "load_stats", None)
        if not callable(load):
            continue
        game = load()
        kinds[game.gamename] = game.kind
        LOGGER.info("Loaded stats %s for %s", game.kind, path.name)
    return kinds


def _packages() -> Iterator[tuple[ModuleType, Path]]:
    here = Path(__file__).resolve().parent
    for path in sorted(here.iterdir()):
        if not _is_game_package(path):
            continue
        yield importlib.import_module(f"{__name__}.{path.name}"), path


def _is_game_package(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith("_") and (path / "__init__.py").is_file()
