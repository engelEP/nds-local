"""Dragon Quest IX catalog and the files it serves."""

from __future__ import annotations

from pathlib import Path

from server.games.dq9.catalog import DragonQuest9Catalog, shop_week

FILES = Path(__file__).resolve().parent / "files"


def load_catalog() -> DragonQuest9Catalog:
    """Catalog bound to this package's ``files`` directory."""
    return DragonQuest9Catalog(FILES)


__all__ = ["FILES", "DragonQuest9Catalog", "load_catalog", "shop_week"]
