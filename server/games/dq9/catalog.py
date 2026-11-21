"""Dragon Quest IX download files.

Quests stay in ``files/<game code>/quest.bin``. The shop and Sellma's message
come from ``files/weeks/``: one of the 52 weeks, advancing every seven days
from 16 July 2010, 08:00 UTC, the original DQVC calendar.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from server.services.downloads import FileEntry, GameCatalog, servable_name

LOGGER = logging.getLogger(__name__)

GAME_CODES = frozenset({"YDQE", "YDQJ", "YDQP"})
_MAX_BYTES = 2 * 1024 * 1024
_SKIP_NAMES = {"readme.md"}
_SHOP_EPOCH = 1279267200  # 2010-07-16 08:00 UTC
_SHOP_WEEK = 7 * 24 * 60 * 60
_SHOP_COUNT = 52
_SHOP_FILES = (("auction.bin", "auction"), ("mes.bin", "mes"))


def shop_week(now: float) -> int:
    """Week index 0–51 on the repeating 52-week DQVC calendar."""
    elapsed = int(now) - _SHOP_EPOCH
    return (elapsed // _SHOP_WEEK) % _SHOP_COUNT


class DragonQuest9Catalog(GameCatalog):
    game_codes = GAME_CODES

    def __init__(self, root: Path, clock: Callable[[], float] | None = None) -> None:
        self._root = root
        self._clock = clock or time.time

    def entries(self, game_code: str) -> list[FileEntry]:
        folder = self._folder(game_code)
        if folder is None:
            return []
        if self._weeks_library() is not None:
            # The calendar is present, so a missing week is an empty shop.
            # Falling through would serve the region's static files instead.
            return _rotating_entries(folder, self._week_folder())
        listed = folder / "_list.txt"
        if listed.is_file():
            return _entries_from_list(listed.read_text(encoding="utf-8", errors="replace"))
        found: list[FileEntry] = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or not _listed(path.name):
                continue
            found.append(FileEntry(name=path.name, size=path.stat().st_size))
        return found

    def read(self, game_code: str, name: str) -> bytes | None:
        if not _listed(name):
            return None
        folder = self._folder(game_code)
        if folder is None:
            return None
        shop_names = {filename for filename, _attr in _SHOP_FILES}
        if name in shop_names and self._weeks_library() is not None:
            week = self._week_folder()
            if week is None:
                return None
            return _read_file(week, name)
        return _read_file(folder, name)

    def log_inventory(self) -> None:
        week = self._week_folder()
        if week is not None:
            LOGGER.info("DQ9 shop week %s", week.name)
        for code in sorted(self.game_codes):
            names = [entry.name for entry in self.entries(code)]
            if names:
                LOGGER.info("DQ9 %s files: %s", code, ", ".join(names))
            else:
                LOGGER.info("DQ9 %s has no files in %s", code, self._root / code)

    def _weeks_library(self) -> Path | None:
        library = (self._root / "weeks").resolve()
        root = self._root.resolve()
        if library.parent != root or not library.is_dir():
            return None
        return library

    def _week_folder(self) -> Path | None:
        library = self._weeks_library()
        if library is None:
            return None
        prefix = f"{shop_week(self._clock()):02d}_"
        matches = sorted(
            path for path in library.iterdir() if path.is_dir() and path.name.startswith(prefix)
        )
        if not matches:
            LOGGER.warning("DQ9 shop week %s is missing under %s", prefix, library)
            return None
        return matches[0]

    def _folder(self, game_code: str) -> Path | None:
        folder = (self._root / game_code.upper()).resolve()
        root = self._root.resolve()
        if folder.parent != root or not folder.is_dir():
            return None
        return folder


def _rotating_entries(region: Path, week: Path | None) -> list[FileEntry]:
    entries: list[FileEntry] = []
    quest = region / "quest.bin"
    if quest.is_file() and quest.stat().st_size <= _MAX_BYTES:
        entries.append(FileEntry(name="quest.bin", size=quest.stat().st_size, attr1="quest"))
    if week is None:
        return entries
    for name, attr in _SHOP_FILES:
        path = week / name
        if path.is_file() and path.stat().st_size <= _MAX_BYTES:
            entries.append(FileEntry(name=name, size=path.stat().st_size, attr1=attr))
    return entries


def _read_file(folder: Path, name: str) -> bytes | None:
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        return None
    if path.stat().st_size > _MAX_BYTES:
        LOGGER.warning("Refusing %s; larger than %d bytes", path.name, _MAX_BYTES)
        return None
    return path.read_bytes()


def _listed(name: str) -> bool:
    if name.startswith(".") or name.startswith("_") or name.lower() in _SKIP_NAMES:
        return False
    return servable_name(name)


def _entries_from_list(text: str) -> list[FileEntry]:
    entries: list[FileEntry] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 6:
            continue
        name = fields[0]
        if not _listed(name):
            continue
        try:
            size = int(fields[5])
        except ValueError:
            size = 0
        entries.append(
            FileEntry(
                name=name,
                size=size,
                attr1=fields[2],
                attr2=fields[3],
                attr3=fields[4],
            )
        )
    return entries
