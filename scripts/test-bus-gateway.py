#!/usr/bin/env python3
"""Protected HTTP origin and password-bound browser session integration tests."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from bus_broker import Broker, BusHTTPServer, handler_factory


SECRET = "gateway-test-only-" + "s" * 40
HASH_A = hashlib.sha256(b"reader-a-current-password").hexdigest()
HASH_D = hashlib.sha256(b"reader-d-current-password").hexdigest()


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="communicate-bus-gateway-")
        with mock.patch.dict(os.environ, {"BUS_GATEWAY_SHARED_SECRET": SECRET, "BUS_ADMIN_READERS": "aadarsh"}):
            self.b = Broker(self.tmp.name)
        self.server = BusHTTPServer(("127.0.0.1", 0), handler_factory(self.b))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.b.handle(self.b.admin_token, {"op": "create", "bus": "private-project"})

    def tearDown(self):
        self.server.shutdown()
        self.worker.join()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path="/v1", method="POST", body=None, token=None, reader=None, digest=None, gateway=SECRET):
        headers = {}
        if gateway is not None:
            headers["X-Communicate-Bus-Gateway"] = gateway
        if reader is not None:
            headers["X-Communicate-Bus-Reader"] = reader
        if digest is not None:
            headers["X-Communicate-Bus-Reader-Hash"] = digest
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        result = json.loads(raw) if raw and "application/json" in content_type else raw
        status = response.status
        conn.close()
        return status, result

    def session(self, reader="aadarsh", digest=HASH_A):
        status, result = self.request("/_bus/session", "GET", reader=reader, digest=digest)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["browser_session"])
        self.assertEqual(result["logout_url"], "/_gateway/logout")
        self.assertNotIn(digest, json.dumps(result))
        return result["token"]

    def test_entire_origin_requires_gateway_secret_even_static_health_and_other_methods(self):
        for method, path in (("GET", "/"), ("GET", "/graph?bus=qit-wilde"), ("GET", "/graph/"), ("GET", "/health"), ("GET", "/assets/x.js"),
                             ("GET", "/_bus/session"), ("POST", "/v1"), ("HEAD", "/"),
                             ("OPTIONS", "/v1"), ("DELETE", "/unknown")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(path, method, gateway=None)[0], 401)
        self.assertEqual(self.request("/health", "GET", gateway="wrong")[0], 401)
        self.assertEqual(self.request("/health", "GET")[0], 200)
        self.assertEqual(self.request("/", "GET")[0], 200)
        self.assertEqual(self.request("/graph?bus=qit-wilde", "GET")[0], 200)
        self.assertEqual(self.request("/graph/", "GET")[0], 200)
        self.assertEqual(self.request("/_bus/session", "GET")[0], 401)
        self.assertEqual(self.request("/_bus/session", "GET", reader="aadarsh", digest=HASH_A, gateway=None)[0], 401)
        self.assertEqual(self.request("/_bus/session", "GET", reader="aadarsh", digest="spoofed")[0], 401)

    def test_reader_scope_and_browser_credentials_are_bound_on_every_request(self):
        admin = self.session()
        member = self.session("peer", HASH_D)
        status, snap = self.request(body={"op": "snapshot"}, token=admin, reader="aadarsh", digest=HASH_A)
        self.assertEqual(status, 200, snap)
        self.assertTrue(snap["is_admin"])
        self.assertFalse(snap["read_only"])
        self.assertEqual(len(snap["buses"]), 2)
        self.assertEqual([principal["id"] for principal in snap["principals"]], ["admin"],
                         "browser readers must not appear as enrolled devices")
        status, snap = self.request(body={"op": "snapshot"}, token=member, reader="peer", digest=HASH_D)
        self.assertEqual(status, 200, snap)
        self.assertTrue(snap["read_only"])
        self.assertFalse(snap["is_admin"])
        self.assertEqual([bus["name"] for bus in snap["buses"]], ["general"])
        self.assertNotIn("principals", snap)
        for token, reader, digest in ((admin, None, None), (admin, "peer", HASH_D),
                                      (admin, "aadarsh", HASH_D), (member, "aadarsh", HASH_A)):
            self.assertEqual(self.request(body={"op": "snapshot"}, token=token, reader=reader, digest=digest)[0], 401)
        # Payload fields cannot supply the HTTP handler's trusted context.
        self.assertEqual(self.request(body={"op": "snapshot", "reader": "aadarsh", "reader_hash": HASH_A}, token=admin)[0], 401)
        for op in ("create", "invite", "invite_revoke", "revoke", "register", "send", "redeem", "leave", "poll", "ack", "heartbeat"):
            status, result = self.request(body={"op": op}, token=member, reader="peer", digest=HASH_D)
            self.assertEqual(status, 403, (op, result))
        status, result = self.request(body={"op": "create", "bus": "admin-created"}, token=admin, reader="aadarsh", digest=HASH_A)
        self.assertEqual(status, 200, result)

    def test_password_rotation_stable_identity_and_reader_access_boundary(self):
        old = self.session("peer", HASH_D)
        old_snap = self.b.handle(old, {"op": "snapshot"}, reader="peer", reader_hash=HASH_D)
        new_hash = hashlib.sha256(b"replacement-reader-password").hexdigest()
        self.assertEqual(self.request(body={"op": "snapshot"}, token=old, reader="peer", digest=new_hash)[0], 401)
        new = self.session("peer", new_hash)
        self.assertNotEqual(old, new)
        status, snap = self.request(body={"op": "snapshot"}, token=new, reader="peer", digest=new_hash)
        self.assertEqual(status, 200)
        self.assertEqual(old_snap["principal"], snap["principal"])
        self.assertEqual(self.request(body={"op": "snapshot"}, token=old, reader="peer", digest=HASH_D)[0], 401)
        db_bytes = self.b.db_path.read_bytes()
        self.assertNotIn(new.encode(), db_bytes)
        self.assertNotIn(old.encode(), db_bytes)
        status, revoked = self.request(body={"op": "revoke", "principal": snap["principal"]}, token=self.b.admin_token)
        self.assertEqual(status, 403)
        self.assertEqual(revoked["code"], "forbidden")
        self.assertIn("gateway roster", revoked["error"])
        self.assertEqual(self.request(body={"op": "snapshot"}, token=new, reader="peer", digest=new_hash)[0], 200)
        self.assertEqual(self.session("peer", new_hash), new)

    def test_machine_credentials_remain_independent_of_browser_context(self):
        invite = self.b.handle(self.b.admin_token, {"op": "invite"})["invite"]
        status, enrolled = self.request(body={"op": "redeem", "invite": invite, "device": "external-device"})
        self.assertEqual(status, 200, enrolled)
        status, registered = self.request(body={"op": "register", "session_key": "agent", "name": "agent"}, token=enrolled["token"])
        self.assertEqual(status, 200, registered)
        status, snap = self.request(body={"op": "snapshot"}, token=enrolled["token"])
        self.assertEqual(status, 200)
        self.assertNotIn("browser_session", snap)
        self.assertEqual(len(snap["buses"][0]["agents"]), 1)
        self.assertEqual(self.request(body={"op": "snapshot"}, token=enrolled["token"], gateway=None)[0], 401)
        admin = self.session()
        self.session("peer", HASH_D)
        status, devices = self.request(body={"op": "snapshot"}, token=admin, reader="aadarsh", digest=HASH_A)
        self.assertEqual(status, 200)
        self.assertEqual({principal["id"] for principal in devices["principals"]}, {"admin", enrolled["principal"]})
        status, revoked = self.request(body={"op": "revoke", "principal": enrolled["principal"]},
                                       token=admin, reader="aadarsh", digest=HASH_A)
        self.assertEqual(status, 200, revoked)
        self.assertEqual(self.request(body={"op": "snapshot"}, token=enrolled["token"])[0], 401)

    def test_local_mode_is_unchanged_and_incomplete_protected_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="communicate-bus-local-") as local:
            with mock.patch.dict(os.environ, {}, clear=True):
                broker = Broker(local)
            original = self.b
            self.b = broker
            handler = handler_factory(broker)
            with BusHTTPServer(("127.0.0.1", 0), handler) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                current = self.server
                self.server = server
                try:
                    self.assertEqual(self.request("/", "GET", gateway=None)[0], 200)
                    self.assertEqual(self.request("/health", "GET", gateway=None)[0], 200)
                    self.assertEqual(self.request("/_bus/session", "GET", gateway=None)[0], 404)
                    self.assertEqual(self.request(body={"op": "snapshot"}, token=broker.admin_token, gateway=None,
                                                  reader="spoofed", digest="bad")[0], 200)
                finally:
                    server.shutdown()
                    worker.join()
                    self.server = current
                    self.b = original
        with mock.patch.dict(os.environ, {"BUS_GATEWAY_SHARED_SECRET": ""}):
            with self.assertRaisesRegex(ValueError, "at least 32"):
                Broker(self.tmp.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
