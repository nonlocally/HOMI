#!/usr/bin/env python3
"""Offline origin migration preserves local adapters and delivery deduplication."""
import json
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import bus

OLD = "https://old.example"
NEW = "https://new.example"
OTHER = "https://other.example"


class RehomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bus-rehome-")
        self.env = mock.patch.dict(os.environ, {"COMM_STATE": self.tmp.name})
        self.env.start()
        self.root = bus.state_dir()
        self.conn = {"url": OLD, "token": "fixture-only-device-credential", "principal": "p_existing", "local": False,
                     "server_id": "same-broker"}
        self.cfg = {"connections": {OLD: self.conn, OTHER: {"url": OTHER, "token": "other-token", "principal": "p_other"}},
                    "default": OLD}
        self.expiry = time.time() + 3600
        self.regs = {
            OLD + "|codex:private": {"id": "a_private", "url": OLD, "session_key": "codex:private", "kind": "codex",
                                       "thread": "private", "name": "private", "buses": ["photonics"], "reply_until": 0},
            OLD + "|claude:hidden": {"id": "a_hidden", "url": OLD, "session_key": "claude:hidden", "kind": "claude",
                                        "session_id": "hidden", "socket": "/fixture.sock", "name": "hidden", "buses": [],
                                        "reply_until": self.expiry},
            OTHER + "|codex:other": {"id": "a_other", "url": OTHER, "session_key": "codex:other", "kind": "codex",
                                       "thread": "other", "name": "other", "buses": ["general"], "reply_until": 0}}
        bus.write_json(self.root / "client.json", self.cfg)
        bus.write_json(self.root / "registrations.json", self.regs)
        self.response = {"ok": True, "principal": "p_existing", "device_id": "p_existing", "user": "peer",
                         "device": "existing-device", "server_id": "same-broker", "buses": []}
        self.request = mock.patch("bus.request", side_effect=lambda conn, op: dict(self.response))
        self.request_mock = self.request.start()
        self.real_start_worker = bus.start_worker
        self.start = mock.patch("bus.start_worker")
        self.start_mock = self.start.start()
        self.start_mock.side_effect = lambda: (self.root / "worker.stop").unlink(missing_ok=True)

    def tearDown(self):
        self.start.stop()
        self.request.stop()
        self.env.stop()
        self.tmp.cleanup()

    def run_rehome(self):
        return bus.run(bus.parser().parse_args(["rehome", OLD, NEW]))

    def seed_journal(self):
        with sqlite3.connect(self.root / "receipts.sqlite") as db:
            db.execute("CREATE TABLE delivered(hub TEXT,id TEXT,status TEXT,detail TEXT,at REAL,PRIMARY KEY(hub,id))")
            db.executemany("INSERT INTO delivered VALUES(?,?,?,?,?)", [
                (OLD, "delivered-before-ack", "queued", "already queued", 1),
                (OLD, "same-message", "queued", "old record", 1),
                (NEW, "same-message", "delivered", "existing destination receipt", 2),
                (OTHER, "unrelated", "queued", "other hub", 1)])

    def journal(self):
        with sqlite3.connect(self.root / "receipts.sqlite") as db:
            return db.execute("SELECT * FROM delivered ORDER BY hub,id").fetchall()

    def test_move_preserves_private_and_hidden_adapters_and_journal_dedup(self):
        self.seed_journal()
        result = self.run_rehome()
        cfg = bus.config()
        self.assertNotIn(OLD, cfg["connections"])
        self.assertEqual(cfg["default"], NEW)
        self.assertEqual(cfg["connections"][NEW], dict(self.conn, url=NEW))
        self.assertEqual(cfg["connections"][OTHER], self.cfg["connections"][OTHER])
        self.assertEqual(cfg["aliases"], {OLD: NEW})
        expected = {key.replace(OLD, NEW): dict(row, url=NEW) if row["url"] == OLD else row for key, row in self.regs.items()}
        self.assertEqual(bus.registrations(), expected)
        self.assertEqual(bus.local_agent(cfg["connections"][NEW], "a_hidden")["reply_until"], self.expiry)
        self.assertEqual(self.journal(), [
            (NEW, "delivered-before-ack", "queued", "already queued", 1),
            (NEW, "same-message", "delivered", "existing destination receipt", 2),
            (OLD, "delivered-before-ack", "queued", "already queued", 1),
            (OLD, "same-message", "queued", "old record", 1),
            (OTHER, "unrelated", "queued", "other hub", 1)])
        self.assertNotIn(self.conn["token"], json.dumps(result))
        self.assertEqual(result["principal"], self.conn["principal"])
        self.assertEqual([call.args[1] for call in self.request_mock.call_args_list], ["snapshot", "snapshot"])
        self.start_mock.assert_called_once_with()
        self.assertFalse((self.root / "broker.stop").exists())
        self.assertFalse((self.root / "worker.stop").exists())

    def test_retired_old_origin_can_move_and_persists_confirmed_server(self):
        self.cfg["connections"][OLD].pop("server_id")
        bus.write_json(self.root / "client.json", self.cfg)
        def snapshot(conn, op):
            if conn["url"] == OLD:
                raise bus.BusError("hub redirects are refused")
            return dict(self.response)
        self.request_mock.side_effect = snapshot
        self.run_rehome()
        self.assertEqual(bus.config()["connections"][NEW]["server_id"], "same-broker")

    def test_previously_coached_old_hub_reply_and_read_commands_use_explicit_alias(self):
        self.run_rehome()
        self.request_mock.reset_mock()
        self.request_mock.side_effect = lambda conn, op, **payload: dict(
            self.response, id="reply-result", conversation_expires_at=self.expiry)
        output = io.StringIO()
        with mock.patch("bus.sys.argv", ["bus.py", "--hub", OLD, "reply", "received-message",
                                         "--from", "a_hidden", "--", "the exact answer"]), \
                mock.patch("sys.stdout", output):
            self.assertEqual(bus.main(), 0)
        call = self.request_mock.call_args
        self.assertEqual(call.args[0]["url"], NEW)
        self.assertEqual(call.args[1], "reply")
        self.assertEqual(call.kwargs, {"sender": "a_hidden", "id": "received-message", "message": "the exact answer"})
        self.assertNotIn(self.conn["token"], output.getvalue())
        self.assertEqual(bus.run(bus.parser().parse_args(["use", OLD]))["hub"], NEW)
        self.assertEqual(bus.run(bus.parser().parse_args(["--hub", OLD, "status", "--no-start"]))["hub"], NEW)
        self.assertNotIn(OLD, bus.config()["connections"])

    def test_further_move_flattens_old_aliases_without_duplicate_polling_connections(self):
        self.run_rehome()
        cfg = bus.config()
        cfg["aliases"]["https://older.example"] = OLD
        cfg["aliases"]["https://unrelated-alias.example"] = OTHER
        bus.write_json(self.root / "client.json", cfg)
        latest = "https://latest.example"
        bus.rehome_connection(NEW, latest)
        cfg = bus.config()
        self.assertEqual(cfg["aliases"], {OLD: latest, NEW: latest, "https://older.example": latest,
                                          "https://unrelated-alias.example": OTHER})
        self.assertEqual(set(cfg["connections"]), {latest, OTHER})
        self.assertEqual(bus.connection(hub=OLD)["url"], latest)
        self.assertEqual({record["url"] for record in bus.registrations().values()}, {latest, OTHER})

    def test_loop_dangling_and_non_https_aliases_fail_without_network_or_local_fallback(self):
        for aliases in ({OLD: NEW, NEW: OLD}, {OLD: NEW}, {OLD: "http://127.0.0.1:7433"}):
            with self.subTest(aliases=aliases):
                bus.write_json(self.root / "client.json", dict(self.cfg, aliases=aliases))
                with self.assertRaises(bus.BusError), mock.patch("bus.local_connection") as local:
                    bus.connection(hub=OLD)
                local.assert_not_called()
        self.request_mock.assert_not_called()

    def test_registration_finishing_after_rehome_updates_only_canonical_adapter(self):
        ident = dict(self.regs[OLD + "|codex:private"], status="queueable")
        def response(conn, op, **payload):
            if op == "register":
                self.run_rehome()
                return dict(self.response, id="a_private", buses=["photonics"])
            return dict(self.response)
        self.request_mock.side_effect = response
        with mock.patch("bus.identity", return_value=ident), mock.patch("bus.device_metadata", return_value={}):
            result = bus.run(bus.parser().parse_args(["register", "--bus", "photonics"]))
        self.assertEqual(result["hub"], NEW)
        self.assertNotIn(OLD + "|codex:private", bus.registrations())
        self.assertEqual(bus.registrations()[NEW + "|codex:private"]["id"], "a_private")
        self.assertEqual(bus.registrations()[NEW + "|codex:private"]["buses"], ["photonics"])

    def test_hidden_identification_finishing_after_rehome_preserves_reply_window(self):
        ident = dict(self.regs[OLD + "|claude:hidden"], status="live")
        def response(conn, op, **payload):
            if op == "identify":
                self.run_rehome()
                return dict(self.response, id="a_hidden", buses=[])
            return dict(self.response)
        self.request_mock.side_effect = response
        with mock.patch("bus.identity", return_value=ident), mock.patch("bus.device_metadata", return_value={}):
            record = bus.identify_sender(self.conn)
        self.assertEqual(record["url"], NEW)
        self.assertEqual(record["reply_until"], self.expiry)
        self.assertEqual(record["buses"], [])
        self.assertNotIn(OLD + "|claude:hidden", bus.registrations())

    def test_send_finishing_after_rehome_retains_canonical_hidden_reply_adapter(self):
        extended = self.expiry + 600
        def response(conn, op, **payload):
            if op == "send":
                self.run_rehome()
                return {"ok": True, "id": "sent-message", "conversation_expires_at": extended}
            return dict(self.response)
        self.request_mock.side_effect = response
        args = bus.parser().parse_args(["send", "published-target", "--from", "a_hidden"])
        args.message = ["fixture question"]
        bus.run(args)
        record = bus.registrations()[NEW + "|claude:hidden"]
        self.assertEqual(record["reply_until"], extended)
        self.assertEqual(record["buses"], [])

    def test_leave_finishing_after_rehome_removes_canonical_membership(self):
        def response(conn, op, **payload):
            if op == "leave":
                self.run_rehome()
                return {"ok": True, "left": True}
            return dict(self.response)
        self.request_mock.side_effect = response
        bus.run(bus.parser().parse_args(["leave", "a_private", "--bus", "photonics"]))
        self.assertEqual(bus.registrations()[NEW + "|codex:private"]["buses"], [])

    def test_another_default_and_expired_unpublished_adapter_stay_unpublished(self):
        self.cfg["default"] = OTHER
        bus.write_json(self.root / "client.json", self.cfg)
        hidden = dict(self.regs[OLD + "|claude:hidden"], reply_until=0)
        bus.write_json(self.root / "registrations.json", {OLD + "|claude:hidden": hidden})
        self.run_rehome()
        self.assertEqual(bus.config()["default"], OTHER)
        self.assertEqual(bus.registrations()[NEW + "|claude:hidden"]["buses"], [])
        self.start_mock.assert_not_called()

    def test_conflicting_destination_enrollment_is_rejected_before_credentials_sent(self):
        self.cfg["connections"][NEW] = dict(self.conn, url=NEW, token="separate-token", principal="p_separate")
        bus.write_json(self.root / "client.json", self.cfg)
        with self.assertRaisesRegex(bus.BusError, "different enrollment"):
            self.run_rehome()
        self.request_mock.assert_not_called()
        self.assertEqual(bus.config(), self.cfg)
        self.assertEqual(bus.registrations(), self.regs)
        self.start_mock.assert_not_called()

    def test_previous_incomplete_migration_does_not_infer_hidden_ownership(self):
        cfg = {"connections": {NEW: dict(self.conn, url=NEW)}, "default": NEW}
        bus.write_json(self.root / "client.json", cfg)
        with self.assertRaisesRegex(bus.BusError, "restore the original client.json from backup"):
            self.run_rehome()
        self.assertEqual(bus.config(), cfg)
        self.assertEqual(bus.registrations(), self.regs)
        self.request_mock.assert_not_called()

    def test_connect_saves_broker_identity_for_future_retired_origin_check(self):
        card = bus.base64.urlsafe_b64encode(json.dumps({"url": NEW, "invite": "fixture-invitation"}).encode()).decode()
        self.request_mock.side_effect = lambda *args, **kwargs: dict(self.response, token="new-fixture-token")
        with mock.patch("bus.device_metadata", return_value={}):
            bus.run(bus.parser().parse_args(["connect", "commbus1." + card]))
        self.assertEqual(bus.config()["connections"][NEW]["server_id"], "same-broker")

    def test_invitation_for_moved_origin_is_rejected_before_redemption(self):
        self.run_rehome()
        cfg, regs = bus.config(), bus.registrations()
        self.request_mock.reset_mock()
        self.start_mock.reset_mock()
        card = bus.base64.urlsafe_b64encode(json.dumps({"url": OLD, "invite": "fixture-invitation"}).encode()).decode()
        with self.assertRaisesRegex(bus.BusError, "ask for an invitation from the current hub origin"):
            bus.run(bus.parser().parse_args(["connect", "commbus1." + card]))
        self.request_mock.assert_not_called()
        self.start_mock.assert_not_called()
        self.assertEqual(bus.config(), cfg)
        self.assertEqual(bus.registrations(), regs)

    def test_wrong_principal_or_device_or_missing_server_identity_does_not_move(self):
        for changed in ({"principal": "p_other"}, {"device_id": "p_other"}, {"server_id": None}):
            with self.subTest(changed=changed):
                self.request_mock.side_effect = lambda conn, op: dict(self.response, **changed)
                with self.assertRaisesRegex(bus.BusError, "confirm this device"):
                    self.run_rehome()
                self.assertEqual(bus.config(), self.cfg)
                self.assertEqual(bus.registrations(), self.regs)

    def test_known_server_is_required_even_when_old_origin_is_unavailable(self):
        def snapshot(conn, op):
            if conn["url"] == OLD:
                raise bus.BusError("retired")
            return dict(self.response, server_id="different-broker")
        self.request_mock.side_effect = snapshot
        with self.assertRaisesRegex(bus.BusError, "different broker"):
            self.run_rehome()
        self.assertEqual(bus.config(), self.cfg)

    def test_old_live_server_mismatch_is_rejected_without_saved_server_id(self):
        self.cfg["connections"][OLD].pop("server_id")
        bus.write_json(self.root / "client.json", self.cfg)
        self.request_mock.side_effect = lambda conn, op: dict(self.response, server_id=conn["url"])
        with self.assertRaisesRegex(bus.BusError, "different broker"):
            self.run_rehome()
        self.assertEqual(bus.config(), self.cfg)

    def test_https_and_explicit_different_origins_required(self):
        for old, new in ((OLD, OLD), (OLD, "http://127.0.0.1:7433"), ("http://127.0.0.1:7433", NEW),
                         (OLD, "https://username:secret@new.example"), (OLD, "https://new.example/path")):
            with self.subTest(old=old, new=new), self.assertRaises(bus.BusError):
                bus.rehome_connection(old, new)
        self.request_mock.assert_not_called()
        self.assertEqual(bus.config(), self.cfg)

    def test_destination_adapter_conflict_does_not_overwrite_session(self):
        regs = dict(self.regs)
        regs[NEW + "|codex:private"] = dict(self.regs[OLD + "|codex:private"], url=NEW, id="another-agent")
        bus.write_json(self.root / "registrations.json", regs)
        with self.assertRaisesRegex(bus.BusError, "conflicting session adapter"):
            self.run_rehome()
        self.assertEqual(bus.registrations(), regs)
        self.assertEqual(bus.config(), self.cfg)

    def test_partial_json_write_failure_rolls_back_and_preserves_receipts(self):
        self.seed_journal()
        original_journal = self.journal()
        real_write = bus.write_json
        calls = []
        def fail_once(path, value):
            calls.append(path.name)
            if len(calls) == 3:
                raise OSError("fixture write failure")
            real_write(path, value)
        with mock.patch("bus.write_json", side_effect=fail_once), self.assertRaises(OSError):
            self.run_rehome()
        self.assertEqual(bus.config(), self.cfg)
        self.assertEqual(bus.registrations(), self.regs)
        self.assertEqual(self.journal(), original_journal)
        self.start_mock.assert_not_called()

    def test_each_durable_prefix_retains_adapter_and_delivery_dedup(self):
        self.seed_journal()
        real_write = bus.write_json
        def inspect(path, value):
            real_write(path, value)
            cfg, regs = bus.config(), bus.registrations()
            for suffix in ("codex:private", "claude:hidden"):
                self.assertTrue(any(row["session_key"] == suffix and row["url"] in cfg["connections"] for row in regs.values()))
            current = cfg["default"]
            self.assertTrue(any(row[0] == current and row[1] == "delivered-before-ack" for row in self.journal()))
        with mock.patch("bus.write_json", side_effect=inspect):
            self.run_rehome()

    def test_rollback_prefixes_keep_adapters_reachable_after_final_registration_write(self):
        self.seed_journal()
        real_write = bus.write_json
        writes = []
        def inspect_and_fail(path, value):
            real_write(path, value)
            writes.append(path.name)
            cfg, regs = bus.config(), bus.registrations()
            for suffix in ("codex:private", "claude:hidden"):
                self.assertTrue(any(row["session_key"] == suffix and row["url"] in cfg["connections"] for row in regs.values()))
            self.assertTrue(any(row[0] == cfg["default"] and row[1] == "delivered-before-ack" for row in self.journal()))
            if len(writes) == 3:
                raise OSError("fixture post-write failure")
        with mock.patch("bus.write_json", side_effect=inspect_and_fail), self.assertRaises(OSError):
            self.run_rehome()
        self.assertEqual(bus.config(), self.cfg)
        self.assertEqual(bus.registrations(), self.regs)

    def test_cooperative_worker_pause_never_stops_broker(self):
        code = """import fcntl,pathlib,sys,time
root=pathlib.Path(sys.argv[1])
with (root/'worker.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 print('ready',flush=True)
 while not (root/'worker.stop').exists(): time.sleep(.01)
"""
        process = subprocess.Popen([sys.executable, "-c", code, str(self.root)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.run_rehome()
            self.assertEqual(process.wait(timeout=3), 0)
            self.assertFalse((self.root / "broker.stop").exists())
            self.start_mock.assert_called_once_with()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdout.close()

    def test_unresponsive_worker_times_out_without_moving_state_or_stopping_broker(self):
        code = """import fcntl,pathlib,sys,time
with (pathlib.Path(sys.argv[1])/'worker.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 print('ready',flush=True)
 time.sleep(60)
"""
        process = subprocess.Popen([sys.executable, "-c", code, str(self.root)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with mock.patch("bus.time.monotonic", side_effect=[0, 31]), self.assertRaisesRegex(bus.BusError, "did not pause"):
                self.run_rehome()
            self.assertEqual(bus.config(), self.cfg)
            self.assertEqual(bus.registrations(), self.regs)
            self.assertFalse((self.root / "broker.stop").exists())
            self.assertFalse((self.root / "worker.stop").exists())
        finally:
            process.terminate()
            process.wait(timeout=3)
            process.stdout.close()

    def test_timeout_keeps_stop_marker_until_same_runtime_draining_worker_is_replaced(self):
        code = """import fcntl,pathlib,sys,time
root=pathlib.Path(sys.argv[1])
with (root/'worker.lock').open('a+') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 print('ready',flush=True)
 while not (root/'worker.stop').exists(): time.sleep(.005)
 time.sleep(.15)
"""
        process = subprocess.Popen([sys.executable, "-c", code, str(self.root)], stdout=subprocess.PIPE, text=True)
        real_clock = time.monotonic
        times = iter([0, 31])
        replacement = mock.Mock(pid=987654321)
        replacement.poll.return_value = None
        def spawn(command):
            self.assertEqual(command, ["__worker"])
            process.wait(timeout=3)
            bus.write_json(self.root / "worker.json", {"pid": replacement.pid, "runtime": bus.WORKER_RUNTIME})
            return replacement
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            bus.write_json(self.root / "worker.json", {"pid": process.pid, "runtime": bus.WORKER_RUNTIME})
            self.start_mock.side_effect = self.real_start_worker
            with mock.patch("bus.time.monotonic", side_effect=lambda: next(times, real_clock())), \
                    mock.patch("bus.spawn_daemon", side_effect=spawn) as spawned, \
                    self.assertRaisesRegex(bus.BusError, "did not pause"):
                self.run_rehome()
            spawned.assert_called_once_with(["__worker"])
            self.assertEqual(bus.config(), self.cfg)
            self.assertFalse((self.root / "broker.stop").exists())
            self.assertFalse((self.root / "worker.stop").exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
