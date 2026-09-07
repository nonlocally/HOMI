#!/usr/bin/env python3
"""Request-local OpenWebUI directory access without device enrollment changes."""
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
from bus_broker import Broker, BusError, BusHTTPServer, handler_factory

SECRET = "viewer-tests-only-" + "s" * 40
READER = "owui.12345678-1234-4234-8234-123456789abc"
HASH = hashlib.sha256(b"viewer-fixture").hexdigest()


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bus-viewer-tests-")
        self.now = 1000
        self.env = mock.patch.dict(os.environ, {
            "BUS_GATEWAY_SHARED_SECRET": SECRET, "BUS_OPENWEBUI_READERS": "1",
            "BUS_ADMIN_READERS": "aadarsh," + READER,
            "BUS_READER_USERS": json.dumps({"aadarsh": "aadarwal", "peer": "peer"}),
        })
        self.env.start()
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        for bus in ("qit-wilde", "other-private"):
            self.assertTrue(self.b.handle(self.b.admin_token, {"op": "create", "bus": bus})["ok"])
        for bus in ("general", "qit-wilde", "other-private"):
            self.agent = self.b.handle(self.b.admin_token, {
                "op": "register", "session_key": "fixture:shared", "name": "shared agent", "bus": bus,
            })["id"]
        self.b.handle(self.b.admin_token, {"op": "register", "session_key": "fixture:secret",
                                         "name": "unrelated secret agent", "bus": "other-private"})
        self.server = BusHTTPServer(("127.0.0.1", 0), handler_factory(self.b))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def view(self, **changes):
        return dict({"v": 1, "reader": READER, "reader_hash": HASH, "iat": self.now,
                     "exp": self.now + 60, "buses": ["qit-wilde"], "display_name": "Viewer Name"}, **changes)

    def session(self, view=None):
        return self.b.browser_session(READER, HASH, self.view() if view is None else view)["token"]

    def snapshot(self, token, view=None):
        return self.b.handle(token, {"op": "snapshot"}, reader=READER, reader_hash=HASH,
                             view=self.view() if view is None else view)

    def http(self, token=None, view=None, path="/v1", reader=READER, digest=HASH,
             extra=(), gateway=SECRET, body=None):
        method = "GET" if path == "/_bus/session" else "POST"
        raw = json.dumps(body or {"op": "snapshot"}).encode() if method == "POST" else b""
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.putrequest(method, path)
        headers = []
        if gateway is not None:
            headers.append(("X-Communicate-Bus-Gateway", gateway))
        if reader is not None:
            headers.append(("X-Communicate-Bus-Reader", reader))
        if digest is not None:
            headers.append(("X-Communicate-Bus-Reader-Hash", digest))
        if view is not None:
            headers.append(("X-Communicate-Bus-View", view if isinstance(view, str) else json.dumps(view)))
        if token:
            headers.append(("Authorization", "Bearer " + token))
        if raw:
            headers.extend((("Content-Type", "application/json"), ("Content-Length", str(len(raw)))))
        for name, value in headers + list(extra):
            conn.putheader(name, value)
        conn.endheaders(raw)
        response = conn.getresponse()
        status, value = response.status, json.loads(response.read())
        conn.close()
        return status, value

    def test_member_sees_only_asserted_bus_and_permitted_membership_tags(self):
        token = self.session()
        snap = self.snapshot(token)
        self.assertTrue(snap["ok"], snap)
        self.assertEqual([row["name"] for row in snap["buses"]], ["qit-wilde"])
        self.assertEqual(snap["buses"][0]["agents"][0]["buses"], ["qit-wilde"])
        self.assertEqual(snap["buses"][0]["agents"][0]["id"], self.agent)
        self.assertNotIn("other-private", json.dumps(snap))
        self.assertNotIn("unrelated secret agent", json.dumps(snap))
        self.assertNotIn("principals", snap)
        self.assertNotIn("users", snap)
        self.assertEqual(snap["user"], READER)
        self.assertTrue(snap["read_only"])
        self.assertFalse(snap["is_admin"], "OWUI readers never inherit BUS_ADMIN_READERS")

    def test_removal_and_missing_or_expired_context_recheck_existing_token(self):
        token = self.session()
        self.assertEqual(self.snapshot(token, self.view(buses=[]))["buses"], [])
        self.assertEqual(self.b.handle(token, {"op": "snapshot"}, reader=READER, reader_hash=HASH)["code"], "unauthorized")
        old = self.view()
        self.now += 60
        self.assertEqual(self.snapshot(token, old)["code"], "unauthorized")
        self.assertTrue(self.snapshot(token)["ok"])
        self.assertEqual(self.session(), token, "membership refresh must not rotate the stable identity")

    def test_browser_cannot_mutate_or_use_device_operations(self):
        token = self.session()
        for op in ("create", "invite", "invite_revoke", "revoke", "redeem", "register", "identify", "device",
                   "send", "reply", "receipt", "leave", "poll", "poll_device", "ack", "heartbeat", "members"):
            with self.subTest(op=op):
                result = self.b.handle(token, {"op": op}, reader=READER, reader_hash=HASH, view=self.view())
                self.assertEqual(result["code"], "forbidden")
        with self.b._connect() as db:
            principal = self.snapshot(token)["principal"]
            self.assertEqual(db.execute("SELECT COUNT(*) FROM grants WHERE principal=?", (principal,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM agents WHERE principal=?", (principal,)).fetchone()[0], 0)

    def test_display_label_is_transient_and_cannot_change_identity(self):
        token = self.session()
        first = self.snapshot(token)
        second = self.snapshot(token, self.view(display_name="Different \u03bb name"))
        self.assertEqual(first["principal"], second["principal"])
        self.assertEqual(first["user"], second["user"])
        self.assertEqual(second["display_name"], "Different \u03bb name")
        omitted = self.view()
        omitted.pop("display_name")
        self.assertNotIn("display_name", self.snapshot(token, omitted))
        self.assertEqual(self.session(self.view(display_name="New label")), token)

    def test_assertion_strict_fields_identity_times_bus_scope_and_text(self):
        cases = [
            {"v": True}, {"v": 2}, {"admin": True}, {"reader": "peer"}, {"reader_hash": "0" * 64},
            {"iat": True}, {"exp": True}, {"iat": 1000.0}, {"exp": 1060.0},
            {"iat": 1006, "exp": 1060}, {"iat": 999, "exp": 1060},
            {"iat": 940, "exp": 1000}, {"iat": 1000, "exp": 1000},
            {"buses": "qit-wilde"}, {"buses": ["qit-wilde", "qit-wilde"]},
            {"buses": ["*"]}, {"buses": ["missing-bus"]}, {"buses": [None]},
            {"buses": ["bus" + str(n) for n in range(65)]},
            {"display_name": 1}, {"display_name": "x" * 161}, {"display_name": "\u03bb" * 81},
            {"display_name": "x\ny"}, {"display_name": "x\x7fy"}, {"display_name": "x\u0085y"},
            {"display_name": "x\u202ey"}, {"display_name": "\ud800"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(BusError):
                self.session(self.view(**changes))
        for view in ({}, [], None):
            with self.subTest(view=view), self.assertRaises(BusError):
                self.b.browser_session(READER, HASH, view)
        self.assertTrue(self.snapshot(self.session(self.view(iat=1005, exp=1060)))["ok"])

    def test_http_boundaries_reject_forged_duplicate_and_malformed_assertions(self):
        status, session = self.http(path="/_bus/session", view=self.view())
        self.assertEqual(status, 200, session)
        token = session["token"]
        self.assertEqual(self.http(token, self.view())[0], 200)
        for view in (None, "null", "[]", "{}", "not-json", " " * 8193,
                     json.dumps(self.view())[:-1] + ',"v":1}'):
            with self.subTest(view=str(view)[:80]):
                self.assertEqual(self.http(token, view)[0], 401)
        for name, value in (("X-Communicate-Bus-View", json.dumps(self.view())),
                            ("X-Communicate-Bus-Reader", READER), ("X-Communicate-Bus-Reader-Hash", HASH),
                            ("X-Communicate-Bus-Gateway", SECRET)):
            with self.subTest(header=name):
                self.assertEqual(self.http(token, self.view(), extra=[(name, value)])[0], 401)
        self.assertEqual(self.http(token, self.view(), gateway=None)[0], 401)
        self.assertEqual(self.http(token, self.view(), reader=None, digest=None)[0], 401)
        self.assertEqual(self.http(token, None, body={"op": "snapshot", "view": self.view()})[0], 401)

    def test_owui_activation_and_uuid_identity_are_explicit(self):
        for reader in ("owui.not-a-uuid", READER.upper(), "owui." + READER[5:].replace("-", "")):
            with self.subTest(reader=reader), self.assertRaises(BusError):
                self.b.browser_session(reader, HASH, self.view(reader=reader))
        self.b.openwebui_readers = False
        with self.assertRaises(BusError):
            self.session()

    def test_github_roles_and_machine_access_do_not_depend_on_assertions(self):
        admin = self.b.browser_session("aadarsh", HASH)["token"]
        member = self.b.browser_session("peer", HASH)["token"]
        snap = self.b.handle(member, {"op": "snapshot"}, reader="peer", reader_hash=HASH)
        self.assertEqual([bus["name"] for bus in snap["buses"]], ["general"])
        snap = self.b.handle(admin, {"op": "snapshot"}, reader="aadarsh", reader_hash=HASH)
        self.assertTrue(snap["is_admin"])
        self.assertEqual(len(snap["buses"]), 3)
        self.assertEqual(self.b.handle(member, {"op": "snapshot"}, reader="peer", reader_hash=HASH,
                                     view=self.view(reader="peer"))["code"], "unauthorized")
        invitation = self.b.handle(self.b.admin_token, {"op": "invite", "bus": "general", "user": "peer"})
        enrolled = self.b.handle(None, {"op": "redeem", "invite": invitation["invite"], "device": "fixture"})
        self.assertTrue(self.b.handle(enrolled["token"], {"op": "register", "session_key": "device-agent",
                                                        "name": "device agent", "bus": "general"})["ok"])
        self.assertTrue(self.b.handle(enrolled["token"], {"op": "snapshot"})["ok"])
        self.assertEqual(self.http(enrolled["token"], reader=None, digest=None)[0], 200)

    def test_local_mode_ignores_browser_headers_and_does_not_enable_sso(self):
        self.b.gateway_shared_secret = None
        status, snapshot = self.http(self.b.admin_token, "not-json", reader="untrusted", digest="invalid", gateway=None)
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["is_admin"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
