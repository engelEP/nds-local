"""Download catalog shared by every game module.

A module reports the files it can hand out. This module turns that report
into the count, list, and contents responses the console posts to ``/download``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileEntry:
    name: str
    size: int
    attr1: str = ""
    attr2: str = ""
    attr3: str = ""

    def line(self) -> str:
        attrs = (_list_field(self.attr1), _list_field(self.attr2), _list_field(self.attr3))
        return f"{self.name}\t\t{attrs[0]}\t{attrs[1]}\t{attrs[2]}\t{self.size}"


@dataclass(frozen=True)
class DownloadRequest:
    game_code: str
    action: str
    contents: str
    attr1: str | None
    attr2: str | None
    attr3: str | None
    limit: int | None
    offset: int | None


class GameCatalog:
    """Files one game is willing to serve."""

    game_codes: frozenset[str]

    def entries(self, game_code: str) -> list[FileEntry]:
        raise NotImplementedError

    def read(self, game_code: str, name: str) -> bytes | None:
        raise NotImplementedError


def select_entries(entries: list[FileEntry], request: DownloadRequest) -> list[FileEntry]:
    chosen = [
        entry
        for entry in entries
        if _matches(request.attr1, entry.attr1)
        and _matches(request.attr2, entry.attr2)
        and _matches(request.attr3, entry.attr3)
    ]
    start = max(request.offset or 0, 0)
    chosen = chosen[start:]
    if request.limit is not None:
        chosen = chosen[: max(request.limit, 0)]
    return chosen


def list_body(entries: list[FileEntry]) -> bytes:
    lines: list[str] = []
    for entry in entries:
        if not servable_name(entry.name):
            LOGGER.warning("Skipping download name %r", entry.name)
            continue
        lines.append(entry.line())
    if not lines:
        return b"\r\n"
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def _list_field(value: str) -> str:
    return "".join(char for char in value if 32 <= ord(char) < 127 and char != "\t")


def servable_name(name: str) -> bool:
    """A name that can sit in one list field and in a Content-Disposition header."""
    if not name or not name.isascii():
        return False
    if any(char in name for char in '/\\"\t\r\n\x00'):
        return False
    return True


class DownloadService:
    def __init__(self, catalogs: list[GameCatalog]) -> None:
        self._by_code: dict[str, GameCatalog] = {}
        for catalog in catalogs:
            for code in catalog.game_codes:
                key = code.upper()
                if key in self._by_code:
                    LOGGER.warning("Game code %s is registered twice; using the later catalog", key)
                self._by_code[key] = catalog

    def handle(self, form: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        request = _request_from(form)
        catalog = self._by_code.get(request.game_code.upper())
        if catalog is None:
            LOGGER.warning("Download for unknown game %s", request.game_code or "<missing>")
            return _empty(request.action)

        if request.action == "count":
            chosen = select_entries(catalog.entries(request.game_code), request)
            LOGGER.info("Download count %s -> %d", request.game_code, len(chosen))
            return 200, {"Content-Type": "text/plain"}, str(len(chosen)).encode("ascii")

        if request.action == "list":
            chosen = select_entries(catalog.entries(request.game_code), request)
            LOGGER.info("Download list %s -> %d files", request.game_code, len(chosen))
            return 200, {"Content-Type": "text/plain"}, list_body(chosen)

        if request.action == "contents":
            payload = catalog.read(request.game_code, request.contents)
            if payload is None:
                LOGGER.warning(
                    "Download missing %s for %s", request.contents or "<missing>", request.game_code
                )
                return 404, {"Content-Type": "text/plain"}, b""
            LOGGER.info(
                "Download contents %s %s (%d bytes)",
                request.game_code,
                request.contents,
                len(payload),
            )
            headers = {
                "Content-Type": "application/x-dsdl",
                "Content-Disposition": f'attachment; filename="{request.contents}"',
            }
            return 200, headers, payload

        LOGGER.warning("Download unknown action %s", request.action or "<missing>")
        return 400, {"Content-Type": "text/plain"}, b""


def _matches(expected: str | None, actual: str) -> bool:
    return expected is None or expected == actual


def _empty(action: str) -> tuple[int, dict[str, str], bytes]:
    if action == "contents":
        return 404, {"Content-Type": "text/plain"}, b""
    if action == "count":
        return 200, {"Content-Type": "text/plain"}, b"0"
    if action == "list":
        return 200, {"Content-Type": "text/plain"}, b"\r\n"
    return 400, {"Content-Type": "text/plain"}, b""


def _request_from(form: dict[str, str]) -> DownloadRequest:
    return DownloadRequest(
        game_code=form.get("gamecd", ""),
        action=form.get("action", "").lower(),
        contents=_safe_name(form.get("contents", "")),
        attr1=form.get("attr1"),
        attr2=form.get("attr2"),
        attr3=form.get("attr3"),
        limit=_optional_int(form.get("num")),
        offset=_optional_int(form.get("offset")),
    )


def _safe_name(raw: str) -> str:
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.replace('"', "").replace("\r", "").replace("\n", "")
    if name in {"", ".", ".."}:
        return ""
    return name


def _optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None
