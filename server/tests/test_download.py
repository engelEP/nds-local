"""Download catalog for Dragon Quest IX files on disk."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server.games.dq9 import DragonQuest9Catalog, shop_week
from server.services.downloads import DownloadService


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        folder = root / "YDQE"
        folder.mkdir()
        (folder / "quest.bin").write_bytes(b"quests")
        (folder / "auction.bin").write_bytes(b"shop")
        (folder / "_secret.bin").write_bytes(b"hidden")
        self.service = DownloadService([DragonQuest9Catalog(root)])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_count_and_list_skip_hidden_files(self) -> None:
        status, _headers, body = self.service.handle({"action": "count", "gamecd": "YDQE"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"2")

        status, _headers, body = self.service.handle({"action": "list", "gamecd": "YDQE"})
        text = body.decode("ascii")
        self.assertIn("auction.bin", text)
        self.assertIn("quest.bin", text)
        self.assertNotIn("_secret.bin", text)

    def test_contents_and_path_are_confined_to_the_folder(self) -> None:
        status, headers, body = self.service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "quest.bin"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"quests")
        self.assertEqual(headers["Content-Type"], "application/x-dsdl")

        status, _headers, body = self.service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "../secret"}
        )
        self.assertEqual(status, 404)

    def test_explicit_list_file_is_used_when_present(self) -> None:
        folder = Path(self._tmp.name) / "YDQP"
        folder.mkdir()
        (folder / "mes.bin").write_bytes(b"hello")
        (folder / "_list.txt").write_text("mes.bin\t\tnews\t\t\t5\r\n")
        service = DownloadService([DragonQuest9Catalog(Path(self._tmp.name))])
        _status, _headers, body = service.handle(
            {"action": "list", "gamecd": "YDQP", "attr1": "news"}
        )
        self.assertIn(b"mes.bin", body)
        _status, _headers, body = service.handle(
            {"action": "list", "gamecd": "YDQP", "attr1": "other"}
        )
        self.assertEqual(body, b"\r\n")

    def test_shop_follows_the_calendar_week(self) -> None:
        root = Path(self._tmp.name)
        for index, shop in ((0, b"opening"), (1, b"alchaid-shop")):
            folder = root / "weeks" / f"{index:02d}_week"
            folder.mkdir(parents=True)
            (folder / "auction.bin").write_bytes(shop)
            (folder / "mes.bin").write_bytes(b"news-" + shop)
        epoch = 1279267200
        week = 7 * 24 * 60 * 60
        def clock() -> float:
            return float(epoch + week + 10)

        service = DownloadService([DragonQuest9Catalog(root, clock=clock)])

        status, _headers, body = service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "auction.bin"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"alchaid-shop")
        self.assertNotEqual(body, b"shop")

        _status, _headers, listed = service.handle(
            {"action": "list", "gamecd": "YDQE", "attr1": "auction"}
        )
        self.assertIn(b"auction.bin", listed)
        self.assertNotIn(b"quest.bin", listed)

        _status, _headers, quests = service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "quest.bin"}
        )
        self.assertEqual(quests, b"quests")
        self.assertEqual(shop_week(float(epoch)), 0)
        self.assertEqual(shop_week(float(epoch + 52 * week)), 0)

    def test_packaged_catalog_is_discovered_with_its_files(self) -> None:
        from server.games import load_catalogs

        catalogs = load_catalogs()
        self.assertTrue(any("YDQE" in catalog.game_codes for catalog in catalogs))
        status, _headers, body = DownloadService(catalogs).handle(
            {"action": "count", "gamecd": "YDQE"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"3")

    def test_missing_shop_week_does_not_serve_the_region_shop(self) -> None:
        root = Path(self._tmp.name)
        (root / "weeks").mkdir()
        service = DownloadService([DragonQuest9Catalog(root, clock=lambda: 1279267200.0)])

        status, _headers, body = service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "auction.bin"}
        )
        self.assertEqual(status, 404)
        self.assertEqual(body, b"")

        status, _headers, listed = service.handle({"action": "list", "gamecd": "YDQE"})
        self.assertEqual(status, 200)
        self.assertIn(b"quest.bin", listed)
        self.assertNotIn(b"auction.bin", listed)

        status, _headers, quests = service.handle(
            {"action": "contents", "gamecd": "YDQE", "contents": "quest.bin"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(quests, b"quests")

    def test_unknown_game_returns_an_empty_catalog(self) -> None:
        status, _headers, body = self.service.handle({"action": "count", "gamecd": "ZZZZ"})
        self.assertEqual(status, 200)
        self.assertEqual(body, b"0")


if __name__ == "__main__":
    unittest.main()
