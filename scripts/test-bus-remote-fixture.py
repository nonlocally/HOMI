#!/usr/bin/env python3
"""Deterministic tests for the opt-in remote HTTPS smoke transport."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("claude_live", Path(__file__).with_name("test-bus-claude-live.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class RemoteFixtureTest(unittest.TestCase):
    def setUp(self):
        self.payload = {"invite": "unique-private-invite-fixture", "target": "a_target", "nonce": "NONCE_FIXTURE",
                        "device": "deployment HTTPS fixture on mini", "session_id": "fixture-session"}
        self.participant = {"principal": "p_fixture", "token": "unique-private-token-fixture"}
        self.enrolled = json.dumps({"stage": "enrolled", "participant": self.participant}) + "\n"
        self.sent = {"stage": "sent", "identity": {"id": "a_hidden"}, "sent": {"id": "m_fixture", "status": "accepted"},
                     "device_metadata": {"hostname": "different-physical-device", "platform": "Darwin"}}

    def test_secret_and_message_travel_only_on_stdin(self):
        output = self.enrolled + json.dumps(self.sent) + "\n"
        completed = subprocess.CompletedProcess([], 0, output, "")
        cleanup, displayed = [], io.StringIO()
        with mock.patch.object(live.subprocess, "run", return_value=completed) as run, \
                mock.patch.object(live.socket, "gethostname", return_value="local-air"), contextlib.redirect_stdout(displayed):
            participant, identity, sent = live.remote_sender("authorized-mini", self.payload, cleanup)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertNotIn(self.payload["invite"], " ".join(command))
        self.assertNotIn(self.payload["nonce"], " ".join(command))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), self.payload)
        self.assertEqual(participant, self.participant)
        self.assertEqual(identity["id"], "a_hidden")
        self.assertEqual(sent["status"], "accepted")
        self.assertEqual(cleanup, ["p_fixture"])
        self.assertNotIn(self.participant["token"], displayed.getvalue())
        self.assertNotIn(self.payload["invite"], displayed.getvalue())

    def test_remote_failure_preserves_enrollment_for_cleanup(self):
        result = subprocess.CompletedProcess([], 1, self.enrolled + '{"stage":"error"}\n', "")
        cleanup = []
        with mock.patch.object(live.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "remote HTTPS sender fixture could not complete"):
                live.remote_sender("authorized-mini", self.payload, cleanup)
        self.assertEqual(cleanup, ["p_fixture"])

    def test_timeout_preserves_partial_captured_enrollment(self):
        error = subprocess.TimeoutExpired(["ssh"], 85, output=self.enrolled.encode())
        cleanup = []
        with mock.patch.object(live.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "remote HTTPS sender fixture could not complete"):
                live.remote_sender("authorized-mini", self.payload, cleanup)
        self.assertEqual(cleanup, ["p_fixture"])

    def test_same_host_is_not_reported_as_two_device_proof(self):
        result = subprocess.CompletedProcess([], 0, self.enrolled + json.dumps(self.sent), "")
        cleanup = []
        with mock.patch.object(live.subprocess, "run", return_value=result), \
                mock.patch.object(live.socket, "gethostname", return_value="different-physical-device"):
            with self.assertRaisesRegex(RuntimeError, "second device"):
                live.remote_sender("authorized-mini", self.payload, cleanup)
        self.assertEqual(cleanup, ["p_fixture"])


if __name__ == "__main__":
    unittest.main()
