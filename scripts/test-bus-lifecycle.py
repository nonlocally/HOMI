#!/usr/bin/env python3
"""A live socket is insufficient when its original Claude session disappears."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import bus


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bus-life-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)})
        self.env.start()
        self.listeners = []
        self.original_socket = self.listener("original.sock")
        self.record = {"kind": "claude", "session_id": "original-session", "socket": self.original_socket}

    def tearDown(self):
        self.env.stop()
        for listener in self.listeners:
            listener.close()
        self.temp.cleanup()

    def listener(self, filename):
        path = str(self.root / filename)
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(path)
        listener.listen(8)
        self.listeners.append(listener)
        return path

    def sidecar(self, pid, session_id, path):
        (self.sessions / (str(pid) + ".json")).write_text(json.dumps({
            "sessionId": session_id, "messagingSocketPath": path, "name": "same-name", "pid": pid}))

    def test_removed_sidecar_does_not_follow_still_listening_old_path(self):
        self.assertTrue(bus.probe(self.original_socket))
        self.assertEqual(bus.current_status(self.record), "offline")

    def test_other_session_reusing_path_or_name_is_not_registered_session(self):
        self.sidecar(101, "different-session", self.original_socket)
        self.assertTrue(bus.probe(self.original_socket))
        self.assertEqual(bus.current_status(self.record), "offline")

    def test_ambiguous_original_session_fails_closed(self):
        resumed = self.listener("resumed.sock")
        self.sidecar(101, "original-session", self.original_socket)
        self.sidecar(102, "original-session", resumed)
        self.assertEqual(bus.current_status(self.record), "offline")

    def test_resume_follows_only_exact_original_session(self):
        resumed = self.listener("resumed.sock")
        self.sidecar(101, "original-session", resumed)
        self.sidecar(102, "same-name-other-session", self.original_socket)
        self.assertEqual(bus.current_status(self.record), "live")
        self.assertEqual(self.record["socket"], resumed)

    def test_resumed_self_uses_original_session_id_before_reregistration(self):
        resumed = self.listener("resumed-self.sock")
        self.sidecar(101, "original-session", resumed)
        record = dict(self.record, url="https://hub.invalid", id="agent-1", name="alias", buses=["general"])
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_MESSAGING_SOCKET": resumed, "CODEX_THREAD_ID": ""}), \
                mock.patch.object(bus, "registrations", return_value={"one": record}):
            self.assertEqual(bus.local_agent({"url": record["url"]})["id"], "agent-1")

    def test_registered_config_root_does_not_drift_with_worker_environment(self):
        self.sidecar(101, "original-session", self.original_socket)
        record = dict(self.record, claude_config_dir=str(self.root))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "different-config")}):
            self.assertEqual(bus.current_status(record), "live")

    def test_worker_is_ready_before_network_and_heartbeats_continue_during_delivery(self):
        state = self.root / "worker-state"
        state.mkdir()
        good = {"id": "good-agent", "kind": "codex", "binary": sys.executable,
                "url": "https://good.invalid", "buses": ["general"]}
        slow = {"id": "slow-agent", "kind": "codex", "binary": sys.executable,
                "url": "https://slow.invalid", "buses": ["general"]}
        records = {"good": good, "slow": slow}
        cfg = {"connections": {record["url"]: {"url": record["url"]} for record in records.values()}}
        slow_started, release_slow = threading.Event(), threading.Event()
        delivering, release_delivery = threading.Event(), threading.Event()
        healthy_once, healthy_twice = threading.Event(), threading.Event()
        counts = {"heartbeats": 0, "polls": 0}
        worker_errors = []

        def fake_request(connection, operation, **payload):
            if operation == "heartbeat":
                if connection["url"] == slow["url"]:
                    slow_started.set()
                    if not release_slow.wait(8):
                        raise bus.BusError("simulated unreachable hub")
                else:
                    counts["heartbeats"] += 1
                    healthy_once.set()
                    if counts["heartbeats"] >= 2:
                        healthy_twice.set()
                return {"ok": True}
            if operation == "poll":
                counts["polls"] += 1
                return {"messages": [{"id": "message-1", "lease": "lease"}] if counts["polls"] == 1 else []}
            if operation == "ack":
                return {"ok": True}
            raise AssertionError(operation)

        def fake_deliver(record, envelope):
            delivering.set()
            if not release_delivery.wait(8):
                raise bus.BusError("simulated slow delivery")
            return "queued", "fixture accepted"

        def run_worker():
            try:
                bus.worker()
            except Exception as exc:
                worker_errors.append(exc)

        with mock.patch.object(bus, "state_dir", return_value=state), \
                mock.patch.object(bus, "config", return_value=cfg), \
                mock.patch.object(bus, "registrations", return_value=records), \
                mock.patch.object(bus, "request", side_effect=fake_request), \
                mock.patch.object(bus, "deliver", side_effect=fake_deliver), \
                mock.patch.object(bus.signal, "signal"):
            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            try:
                self.assertTrue(slow_started.wait(2))
                self.assertTrue(delivering.wait(2))
                self.assertTrue(healthy_once.wait(2))
                initial = json.loads((state / "worker.json").read_text())
                self.assertEqual(initial["pid"], os.getpid())
                self.assertFalse(release_slow.is_set())
                first_heartbeat = initial["heartbeat_at"]
                release_slow.set()
                self.assertTrue(healthy_twice.wait(5), "delivery blocked the independent heartbeat ticker")
                self.assertFalse(release_delivery.is_set())
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    latest = json.loads((state / "worker.json").read_text())
                    if latest["heartbeat_at"] > first_heartbeat:
                        break
                    time.sleep(.01)
                self.assertGreater(latest["heartbeat_at"], first_heartbeat)
            finally:
                (state / "worker.stop").touch()
                release_slow.set()
                release_delivery.set()
                worker.join(6)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker_errors, [])
            self.assertEqual(json.loads((state / "worker.json").read_text())["state"], "stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
