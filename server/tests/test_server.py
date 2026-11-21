"""The process stops on SIGTERM and takes the listeners with it."""

from __future__ import annotations

import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


class ServerStopTests(unittest.TestCase):
    def test_sigterm_stops_the_listeners(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "nds.sqlite")
            self._stop_server(root, database)

    def _stop_server(self, root: Path, database: str) -> None:
        script = (
            "import logging\n"
            "from pathlib import Path\n"
            "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
            "from server.config import ServerConfig\n"
            "from server.app import serve\n"
            "serve(ServerConfig(bind_host='127.0.0.1', dns_port=0, http_port=0, "
            "ssl_port=9, presence_port=0, available_port=0, "
            "ssl_proxy=Path('missing/ds_ssl_proxy'), "
            f"database=Path({database!r})))\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=root,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines: queue.Queue[str] = queue.Queue()

        def _read() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                lines.put(line)
            lines.put("")

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        try:
            deadline = time.monotonic() + 5
            ready = False
            chunks: list[str] = []
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=deadline - time.monotonic())
                except queue.Empty:
                    break
                if line == "":
                    break
                chunks.append(line)
                if "Listening" in line:
                    ready = True
                    break
            self.assertTrue(ready, "".join(chunks) or f"exit {proc.poll()}")
            proc.send_signal(signal.SIGTERM)
            code = proc.wait(timeout=5)
            self.assertEqual(code, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
            reader.join(timeout=1)
            if proc.stderr is not None:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
