"""DNS replies for the names a DS session looks up."""

from __future__ import annotations

import socket
import unittest

from server.net.dns import (
    DnsError,
    DnsServer,
    answer_ip_for,
    build_response,
    decode_name,
    encode_name,
)


def _query(name: str) -> bytes:
    question = encode_name(name) + (1).to_bytes(2, "big") + (1).to_bytes(2, "big")
    header = b"\x12\x34" + b"\x01\x00" + b"\x00\x01" + b"\x00\x00" + b"\x00\x00" + b"\x00\x00"
    return header + question


class DnsMessageTests(unittest.TestCase):
    def test_nintendo_name_receives_the_virtual_host(self) -> None:
        reply = build_response(
            _query("nas.nintendowifi.net"),
            lambda name: answer_ip_for(name, ("nintendowifi.net",), "10.64.0.1"),
        )
        self.assertEqual(reply[:2], b"\x12\x34")
        self.assertEqual(reply[2:4], b"\x81\x80")
        self.assertEqual(socket.inet_ntoa(reply[-4:]), "10.64.0.1")

    def test_other_query_type_for_a_known_name_is_not_nxdomain(self) -> None:
        query = encode_name("nas.nintendowifi.net")
        query += (28).to_bytes(2, "big") + (1).to_bytes(2, "big")
        packet = b"\x12\x34" + b"\x01\x00" + b"\x00\x01" + b"\x00\x00\x00\x00\x00\x00" + query
        reply = build_response(
            packet,
            lambda name: answer_ip_for(name, ("nintendowifi.net",), "10.64.0.1"),
        )
        self.assertEqual(reply[2:4], b"\x81\x80")
        self.assertEqual(reply[6:8], b"\x00\x00")

    def test_non_ascii_label_is_rejected(self) -> None:
        with self.assertRaises(DnsError):
            decode_name(b"\x01\xff\x00", 0)

    def test_other_names_are_nxdomain(self) -> None:
        reply = build_response(
            _query("example.com"),
            lambda name: answer_ip_for(name, ("nintendowifi.net",), "10.64.0.1"),
        )
        self.assertEqual(reply[2:4], b"\x81\x83")
        self.assertEqual(reply[6:8], b"\x00\x00")

    def test_name_round_trip(self) -> None:
        packet = encode_name("dls1.nintendowifi.net")
        name, end = decode_name(packet, 0)
        self.assertEqual(name, "dls1.nintendowifi.net")
        self.assertEqual(end, len(packet))


class DnsServerTests(unittest.TestCase):
    def test_live_socket_answers(self) -> None:
        server = DnsServer(
            "127.0.0.1",
            0,
            lambda name: answer_ip_for(name, ("nintendowifi.net",), "10.64.0.1"),
        )
        server.start()
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.settimeout(2)
            try:
                client.sendto(_query("conntest.nintendowifi.net"), server.address)
                payload, _peer = client.recvfrom(512)
            finally:
                client.close()
            self.assertEqual(socket.inet_ntoa(payload[-4:]), "10.64.0.1")
        finally:
            server.stop()
            server.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
