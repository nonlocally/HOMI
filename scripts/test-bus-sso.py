#!/usr/bin/env python3
"""Offline HTTP and durable single-use tests for the private SSO handoff."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import bus_broker
from bus_broker import Broker, BusError, BusHTTPServer, handler_factory

SECRET = "sso-gateway-fixture-" + "s" * 40
ENDPOINT = "/_bus/sso/consume"


class SSOReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="communicate-bus-sso-")
        self.addCleanup(self.tmp.cleanup)
        self.environment = mock.patch.dict(os.environ, {
            "BUS_GATEWAY_SHARED_SECRET": SECRET,
            "BUS_ADMIN_READERS": "aadarsh",
            "BUS_READER_USERS": "{}",
            "BUS_OPENWEBUI_READERS": "1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.now = 1_800_000_000
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        self.server = self.serve(self.b)

    def serve(self, broker):
        server = BusHTTPServer(("127.0.0.1", 0), handler_factory(broker))
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        worker.start()
        def close():
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()
        self.addCleanup(close)
        return server

    def payload(self, **overrides):
        return {"jti": str(uuid.uuid4()), "exp": self.now + 60, **overrides}

    def request(self, body=None, *, raw=None, headers=(), gateway=SECRET, method="POST", server=None):
        server = server or self.server
        data = raw if raw is not None else json.dumps(self.payload() if body is None else body).encode()
        if isinstance(data, str):
            data = data.encode()
        supplied = list(headers)
        if gateway is not None:
            supplied.insert(0, ("X-Communicate-Bus-Gateway", gateway))
        if not any(key.lower() == "content-type" for key, _ in supplied):
            supplied.append(("Content-Type", "application/json"))
        if not any(key.lower() == "content-length" for key, _ in supplied):
            supplied.append(("Content-Length", str(len(data))))
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            conn.putrequest(method, ENDPOINT)
            for key, value in supplied:
                conn.putheader(key, value)
            conn.endheaders(data)
            response = conn.getresponse()
            raw_response = response.read()
            result = json.loads(raw_response) if "application/json" in response.getheader("Content-Type", "") else raw_response
            return response.status, result, dict(response.getheaders())
        finally:
            conn.close()

    def stored(self):
        with sqlite3.connect(self.b.db_path) as db:
            return db.execute("SELECT jti,expires_at FROM sso_replays ORDER BY jti").fetchall()

    def assert_error(self, status, code, **kwargs):
        actual, body, _ = self.request(**kwargs)
        self.assertEqual(actual, status, body)
        self.assertFalse(body.get("ok"), body)
        self.assertEqual(body.get("code"), code, body)

    def test_first_consume_then_replay_is_durable_and_contains_no_identity_or_credentials(self):
        body = self.payload()
        status, result, headers = self.request(body)
        self.assertEqual((status, result), (200, {"ok": True}))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(self.stored(), [(body["jti"], body["exp"] + 30)])
        self.assert_error(409, "conflict", body=body)
        reopened = Broker(self.tmp.name, clock=lambda: self.now)
        second_server = self.serve(reopened)
        self.assert_error(409, "conflict", body=body, server=second_server)
        self.assertEqual(reopened.server_id, self.b.server_id)
        with sqlite3.connect(self.b.db_path) as db:
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(sso_replays)")], ["jti", "expires_at"])
        self.assertNotIn(SECRET.encode(), self.b.db_path.read_bytes())

    def test_concurrent_separate_brokers_accept_exactly_once_over_http(self):
        brokers = [Broker(self.tmp.name, clock=lambda: self.now) for _ in range(4)]
        servers = [self.serve(broker) for broker in brokers]
        barrier = threading.Barrier(8)
        body = self.payload()
        def consume(index):
            barrier.wait(timeout=3)
            return self.request(body, server=servers[index % len(servers)])[:2]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(consume, range(8)))
        self.assertEqual(sorted(status for status, _ in results), [200] + [409] * 7)
        self.assertTrue(all(result == {"ok": True} if status == 200 else result.get("code") == "conflict"
                            for status, result in results))
        self.assertEqual(len(self.stored()), 1)

    def test_expiry_bounds_are_inclusive_integer_only_and_do_not_consume_invalid_ids(self):
        for exp in (self.now - 31, self.now + 91, True, False, float(self.now), str(self.now), None):
            with self.subTest(exp=exp):
                self.assert_error(400, "invalid_request", body=self.payload(exp=exp))
        self.assertEqual(self.stored(), [])
        for exp in (self.now - 30, self.now + 90):
            with self.subTest(exp=exp):
                body = self.payload(exp=exp)
                self.assertEqual(self.request(body)[0], 200)
                self.assert_error(409, "conflict", body=body)

    def test_uuid_must_be_canonical_lowercase_version_four(self):
        valid = "d31a6e98-a4b6-4f23-8d6b-381de964798a"
        malformed = (valid.upper(), valid.replace("-", ""), "{" + valid + "}", " " + valid,
                     str(uuid.uuid1()), "00000000-0000-4000-0000-000000000000", "not-a-uuid", "", None, 123)
        for value in malformed:
            with self.subTest(jti=value):
                self.assert_error(400, "invalid_request", body=self.payload(jti=value))
        self.assertEqual(self.stored(), [])

    def test_exact_request_shape_and_duplicate_json_keys_fail_without_consumption(self):
        body = self.payload()
        malformed = ([], "string", 1, True, {}, {"jti": body["jti"]}, {"exp": body["exp"]},
                     dict(body, token="fixture-jwt-must-not-be-stored"), dict(body, op="snapshot"))
        for value in malformed:
            with self.subTest(shape=type(value).__name__):
                self.assert_error(400, "invalid_request", body=value)
        for raw in ("{", "null", b"\xff", '{"jti":"%s","jti":"%s","exp":%d}' % (body["jti"], body["jti"], body["exp"]),
                    '{"jti":"%s","exp":%d,"exp":%d}' % (body["jti"], body["exp"], body["exp"])):
            with self.subTest(raw_type=type(raw).__name__):
                self.assert_error(400, "invalid_request", raw=raw)
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.request(body)[0], 200)

    def test_missing_invalid_and_duplicate_gateway_headers_fail_before_consumption(self):
        body = self.payload()
        for gateway in (None, "", "incorrect-fixture-secret"):
            with self.subTest(gateway_present=gateway is not None):
                self.assert_error(401, "unauthorized", body=body, gateway=gateway)
        self.assert_error(401, "unauthorized", body=body,
                          headers=[("x-communicate-bus-gateway", SECRET)])
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.request(body)[0], 200)

    def test_any_browser_or_machine_credential_header_is_forbidden_even_empty_or_partial(self):
        body = self.payload()
        contexts = ("X-Communicate-Bus-Reader", "X-Communicate-Bus-Reader-Hash", "X-Communicate-Bus-View",
                    "Authorization", "Cookie")
        for name in contexts:
            for value in ("", "untrusted-fixture"):
                with self.subTest(header=name, empty=not value):
                    self.assert_error(403, "forbidden", body=body, headers=[(name, value)])
        self.assert_error(403, "forbidden", body=body, headers=[
            ("X-Communicate-Bus-Reader", "aadarsh"), ("X-Communicate-Bus-Reader-Hash", "a" * 64)])
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.request(body)[0], 200)

    def test_ambiguous_framing_and_wrong_media_type_are_rejected_without_consumption(self):
        raw = json.dumps(self.payload()).encode()
        cases = ([('Content-Length', str(len(raw))), ('content-length', str(len(raw)))],
                 [('Content-Type', 'application/json'), ('content-type', 'application/json')],
                 [('Content-Type', 'text/plain')], [('Transfer-Encoding', 'chunked')],
                 [('Content-Length', 'not-a-number')])
        for headers in cases:
            with self.subTest(headers=[name for name, _ in headers]):
                self.assert_error(400, "invalid_request", raw=raw, headers=headers)
        self.assertEqual(self.stored(), [])

    def test_bounded_store_keeps_live_entries_and_reclaims_only_expired_records(self):
        first, second, third = self.payload(exp=self.now), self.payload(), self.payload()
        with mock.patch.object(bus_broker, "MAX_SSO_REPLAYS", 2):
            self.assertEqual(self.request(first)[0], 200)
            self.assertEqual(self.request(second)[0], 200)
            self.assert_error(409, "conflict", body=first)
            self.assert_error(429, "limit", body=third)
            self.now += 29
            self.assert_error(409, "conflict", body=first)
            self.assert_error(429, "limit", body=third)
            self.now += 2
            self.assertEqual(self.request(third)[0], 200)
            self.assertEqual({jti for jti, _ in self.stored()}, {second["jti"], third["jti"]})
            self.assert_error(409, "conflict", body=second)

    def test_endpoint_is_unavailable_without_protected_gateway_and_cannot_be_consumed_by_get(self):
        with tempfile.TemporaryDirectory(prefix="communicate-bus-sso-local-") as directory:
            with mock.patch.dict(os.environ, {}, clear=True):
                local = Broker(directory, clock=lambda: self.now)
            local_server = self.serve(local)
            for gateway in (None, SECRET):
                self.assert_error(404, "not_found", server=local_server, gateway=gateway)
            with self.assertRaises(BusError) as rejected:
                local.consume_sso(self.payload())
            self.assertEqual(rejected.exception.code, "not_found")
        self.assertEqual(self.request(method="GET")[0], 404)
        self.assertEqual(self.stored(), [])

    def test_schema_upgrade_preserves_enrolled_devices_memberships_and_pending_messages(self):
        def call(op, token=None, **fields):
            result = self.b.handle(self.b.admin_token if token is None else token, {"op": op, **fields})
            self.assertTrue(result.get("ok"), result)
            return result
        call("create", bus="qit-wilde")
        invite = call("invite", bus="qit-wilde")["invite"]
        peer = call("redeem", token="", invite=invite, device="migration-fixture")
        target = call("register", token=peer["token"], session_key="target", name="target", bus="qit-wilde")["id"]
        sender = call("register", session_key="sender", name="sender", bus="qit-wilde")["id"]
        message = call("send", sender=sender, target=target, bus="qit-wilde", message="fixture pending payload")["id"]
        tables = ("meta", "principals", "tokens", "browser_credentials", "buses", "grants", "invites", "agents",
                  "memberships", "conversations", "messages")
        def rows():
            with sqlite3.connect(self.b.db_path) as db:
                return {table: db.execute('SELECT * FROM "%s" ORDER BY rowid' % table).fetchall() for table in tables}
        original = rows()
        with sqlite3.connect(self.b.db_path) as db:
            db.execute("DROP TABLE sso_replays")
        reopened = Broker(self.tmp.name, clock=lambda: self.now)
        self.assertEqual(rows(), original)
        self.assertEqual(reopened.admin_token, self.b.admin_token)
        self.assertEqual(reopened.server_id, self.b.server_id)
        self.assertEqual(reopened.consume_sso(self.payload()), {"ok": True})
        self.assertEqual(rows(), original, "consuming identity handoff must not alter any device or agent authority")
        snapshot = reopened.handle(peer["token"], {"op": "snapshot"})
        self.assertTrue(snapshot["ok"])
        self.assertEqual([bus["name"] for bus in snapshot["buses"]], ["qit-wilde"])
        pending = reopened.handle(peer["token"], {"op": "poll", "agent": target})
        self.assertEqual([item["id"] for item in pending["messages"]], [message])


if __name__ == "__main__":
    unittest.main(verbosity=2)
