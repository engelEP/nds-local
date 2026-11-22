"""SQL applied to every database, independent of any game package."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def load_seeds() -> list[str]:
    """Scripts in this folder, in filename order."""
    scripts: list[str] = []
    for path in sorted(Path(__file__).resolve().parent.glob("*.sql")):
        scripts.append(path.read_text(encoding="utf-8"))
        LOGGER.info("Loaded seed %s", path.name)
    return scripts
