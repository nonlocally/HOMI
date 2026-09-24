#!/usr/bin/env python3
"""Loopback broker startup must not depend on reverse-DNS availability."""
import http.client
import http.server
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from bus_broker import BusHTTPServer


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ready"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class BindTests(unittest.TestCase):
    def check_listener(self, host):
        with patch("socket.getfqdn", side_effect=AssertionError("reverse DNS unavailable")) as lookup:
            with BusHTTPServer((host, 0), Handler) as server:
                self.assertEqual(server.server_name, host)
                self.assertEqual(server.server_port, server.socket.getsockname()[1])
                self.assertGreater(server.server_port, 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    connection = http.client.HTTPConnection(host, server.server_port, timeout=3)
                    try:
                        connection.request("GET", "/")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), b"ready")
                    finally:
                        connection.close()
                finally:
                    server.shutdown()
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())
            lookup.assert_not_called()

    def test_ipv4_without_reverse_dns(self):
        self.check_listener("127.0.0.1")

    def test_ipv6_without_reverse_dns(self):
        try:
            with socket.socket(socket.AF_INET6) as probe:
                probe.bind(("::1", 0))
        except OSError:
            self.skipTest("IPv6 loopback unavailable")
        self.check_listener("::1")

    def test_non_loopback_still_refused(self):
        with self.assertRaisesRegex(ValueError, "must bind to loopback"):
            BusHTTPServer(("0.0.0.0", 0), Handler)


if __name__ == "__main__":
    unittest.main()
