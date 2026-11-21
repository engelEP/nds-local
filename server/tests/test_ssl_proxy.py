"""The port-443 proxy starts only when this machine can actually speak SSLv3."""

from __future__ import annotations

import contextlib
import io
import stat
import tempfile
import unittest
from pathlib import Path

from server.net.ssl_proxy import backend_host, proxy_command, start_ssl_proxy, unavailable_reason
from server.config import ServerConfig, parse_args


class SslProxyTests(unittest.TestCase):
    def test_bind_must_be_an_ipv4_address(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args(["--bind", "localhost"])

    def test_defaults_point_at_the_proxy_and_the_chain(self) -> None:
        config = parse_args([])
        self.assertEqual(config.ssl_port, 443)
        self.assertEqual(config.cert_file, Path("certs/server.chain.crt"))
        self.assertEqual(config.key_file, Path("certs/server.key"))
        self.assertEqual(config.ssl_proxy, Path("bin/ds_ssl_proxy"))

    def test_missing_binary_names_the_gap(self) -> None:
        config = ServerConfig(ssl_proxy=Path("missing/ds_ssl_proxy"))
        reason = unavailable_reason(config)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("no-ssl3", reason)
        self.assertIn("ssl/build-proxy.sh", reason)

    def test_missing_certificate_is_reported_after_the_binary_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "ds_ssl_proxy"
            binary.write_text("", encoding="ascii")
            config = ServerConfig(
                ssl_proxy=binary,
                cert_file=root / "server.chain.crt",
                key_file=root / "server.key",
            )
            reason = unavailable_reason(config)
            self.assertIsNotNone(reason)
            assert reason is not None
            self.assertIn("server.chain.crt", reason)
            self.assertIn("server.key", reason)

    def test_command_forwards_to_the_local_http_port(self) -> None:
        config = ServerConfig(bind_host="0.0.0.0", http_port=8080, ssl_port=4443)
        command = proxy_command(config)
        self.assertEqual(backend_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(backend_host("192.168.4.153"), "192.168.4.153")
        self.assertIn("127.0.0.1", command)
        self.assertIn("8080", command)
        self.assertIn("4443", command)

    def test_start_tracks_a_proxy_that_reports_listening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "ds_ssl_proxy"
            binary.write_text(
                "#!/bin/sh\necho 'SSL proxy listening on 127.0.0.1:9' >&2\nexec sleep 30\n",
                encoding="ascii",
            )
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            cert = root / "server.chain.crt"
            key = root / "server.key"
            cert.write_text("cert", encoding="ascii")
            key.write_text("key", encoding="ascii")
            config = ServerConfig(ssl_proxy=binary, cert_file=cert, key_file=key, ssl_port=9)
            proxy = start_ssl_proxy(config)
            self.assertIsNotNone(proxy)
            assert proxy is not None
            proxy.stop()
            self.assertIsNotNone(proxy._process.poll())

    def test_unexecutable_proxy_does_not_take_down_the_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "ds_ssl_proxy"
            binary.write_text("not a program", encoding="ascii")
            cert = root / "server.chain.crt"
            key = root / "server.key"
            cert.write_text("cert", encoding="ascii")
            key.write_text("key", encoding="ascii")
            config = ServerConfig(ssl_proxy=binary, cert_file=cert, key_file=key, ssl_port=9)
            self.assertIsNone(start_ssl_proxy(config))


if __name__ == "__main__":
    unittest.main()
