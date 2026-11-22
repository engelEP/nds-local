"""HTTP wire behavior on an ephemeral local port."""

from __future__ import annotations

import socket
import threading
import unittest
import urllib.error
import urllib.request

from server.net.http import build_http_server, content_length, safe_host
from server.services.account.nas import NasService
from server.services.account.sessions import (
    SessionStore,
    decode_form,
    decode_nintendo,
    encode_nintendo,
)
from server.services.downloads import DownloadService


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        nas = NasService(SessionStore(), "dls1.nintendowifi.net")
        self.httpd = build_http_server("127.0.0.1", 0, nas, DownloadService([]))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        address = self.httpd.server_address
        host = address[0].decode("ascii") if isinstance(address[0], bytes) else str(address[0])
        self.base = f"http://{host}:{address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)
        self.httpd.server_close()

    def test_connection_check(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"ok")
            self.assertEqual(response.headers["X-Organization"], "Nintendo")

    def test_login_post(self) -> None:
        payload = b"action=acctcreate"
        request = urllib.request.Request(self.base + "/ac", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode("ascii")
        self.assertIn("returncd=002", body)
        self.assertEqual(response.headers["NODE"], "wifiappe1")

    def test_nintendo_base64_login(self) -> None:
        payload = b"action=bG9naW4*&userid=MQ**"
        request = urllib.request.Request(self.base + "/ac", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode("ascii")
        self.assertIn("returncd=" + encode_nintendo("001"), body)
        self.assertIn("locator=" + encode_nintendo("gamespy.com"), body)
        self.assertNotIn("returncd=001", body)

    def test_nintendo_base64_download_stays_plain(self) -> None:
        action = encode_nintendo("count").encode()
        game = encode_nintendo("YDQE").encode()
        payload = b"action=" + action + b"&gamecd=" + game
        request = urllib.request.Request(self.base + "/download", data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read()
        self.assertEqual(body, b"0")

    def test_post_without_content_length_is_rejected(self) -> None:
        address = self.httpd.server_address
        host = address[0].decode("ascii") if isinstance(address[0], bytes) else str(address[0])
        with socket.create_connection((host, int(address[1])), timeout=2) as sock:
            sock.sendall(b"POST /ac HTTP/1.0\r\nHost: nas.nintendowifi.net\r\n\r\n")
            data = sock.recv(256)
        self.assertTrue(data.startswith(b"HTTP/1.0 400"), data)

    def test_unknown_post_is_404(self) -> None:
        request = urllib.request.Request(self.base + "/missing", data=b"a=b", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)


class NintendoTests(unittest.TestCase):
    def test_login_token_decodes(self) -> None:
        self.assertEqual(decode_nintendo("bG9naW4*"), "login")
        self.assertEqual(decode_nintendo("c3ZjbG9j"), "svcloc")
        self.assertEqual(encode_nintendo("001"), "MDAx")

    def test_download_padding_is_percent_encoded(self) -> None:
        decoded, encoded = decode_form({"action": "Y291bnQ%2A", "gamecd": "WURRRQ%2A%2A"})
        self.assertEqual(decoded["action"], "count")
        self.assertEqual(decoded["gamecd"], "YDQE")
        self.assertTrue(encoded)

    def test_plain_words_stay_plain(self) -> None:
        self.assertEqual(decode_nintendo("acctcreate"), "acctcreate")
        self.assertEqual(decode_nintendo("YDQE"), "YDQE")
        self.assertEqual(decode_nintendo(""), "")


class HeaderTests(unittest.TestCase):
    def test_broken_content_length_is_rejected(self) -> None:
        self.assertIsNone(content_length(None))
        self.assertIsNone(content_length(""))
        self.assertIsNone(content_length("nope"))
        self.assertIsNone(content_length("-1"))
        self.assertEqual(content_length("0"), 0)
        self.assertEqual(content_length("12"), 12)

    def test_host_header_cannot_break_the_response(self) -> None:
        self.assertEqual(safe_host("dls1.nintendowifi.net"), "dls1.nintendowifi.net")
        self.assertEqual(safe_host("evil\r\nX-Injected: 1"), "dls1.nintendowifi.net")
        self.assertEqual(safe_host("nas.nintendowifi.net@evil.example"), "dls1.nintendowifi.net")
        self.assertEqual(safe_host(None), "dls1.nintendowifi.net")


if __name__ == "__main__":
    unittest.main()
