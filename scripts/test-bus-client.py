#!/usr/bin/env python3
"""CLI -> verified TLS broker -> outbound worker -> actual Unix socket/queue.

All identities/homes are disposable fixtures. The Codex executable is an argv
capture fixture, so this proves exact-session enqueue, not an LLM response.
"""
import base64
import http.server
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import bus
from bus_broker import Broker, handler_factory


class Listener:
    def __init__(self, path):
        self.path = str(path)
        self.messages = queue.Queue()
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(self.path)
        self.sock.listen()
        self.sock.settimeout(.2)
        self.closed = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(1)
                raw = b""
                try:
                    while b"\n" not in raw:
                        part = conn.recv(65536)
                        if not part:
                            break
                        raw += part
                    if raw:
                        self.messages.put(json.loads(raw))
                except (OSError, ValueError):
                    pass

    def close(self):
        self.closed = True
        self.sock.close()
        self.thread.join(2)


class BusClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix="cb-", dir="/tmp"))
        cls.broker = Broker(cls.temp / "hub")
        cls.admin = cls.broker.admin_token
        cert, key = cls.temp / "cert.pem", cls.temp / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                        "-addext", "basicConstraints=critical,CA:TRUE"],
                       capture_output=True, check=True)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(cls.broker))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        cls.server.socket = ctx.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "https://localhost:%d" % cls.server.server_port
        cls.envs, cls.listeners = [], []

    @classmethod
    def tearDownClass(cls):
        for env in cls.envs:
            subprocess.run([str(ROOT / "bin/communicate"), "bus", "stop"], env=env,
                           capture_output=True, timeout=15)
        for listener in cls.listeners:
            listener.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)
        shutil.rmtree(cls.temp)

    def env(self, label):
        home = self.temp / (label + "-" + uuid.uuid4().hex[:5])
        home.mkdir()
        env = dict(os.environ, HOME=str(home), COMM_STATE=str(home / "state"),
                   CLAUDE_CONFIG_DIR=str(home / ".claude"), CODEX_HOME=str(home / ".codex"),
                   SSL_CERT_FILE=str(self.temp / "cert.pem"), COMM_BUS_PORT="0")
        for key in ("CLAUDE_CODE_MESSAGING_SOCKET", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "COMM_CODEX_INDEX"):
            env.pop(key, None)
        self.envs.append(env)
        return env

    def cli(self, env, *args, ok=True):
        proc = subprocess.run([str(ROOT / "bin/communicate"), "bus", *args],
                              env=env, text=True, capture_output=True, timeout=30)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return proc.stdout.strip() if ok else proc.stderr

    def code(self, name="general", url=None):
        if name != "general":
            self.assertTrue(self.broker.handle(self.admin, {"op": "create", "bus": name})["ok"])
        invite = self.broker.handle(self.admin, {"op": "invite", "bus": name, "ttl": 60})
        raw = json.dumps({"url": url or self.url, "invite": invite["invite"]}).encode()
        return "commbus1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def attach_claude(self, env, name):
        path = self.temp / (uuid.uuid4().hex[:8] + ".sock")
        listener = Listener(path)
        self.listeners.append(listener)
        sd = Path(env["CLAUDE_CONFIG_DIR"]) / "sessions"
        sd.mkdir(parents=True, exist_ok=True)
        sid = str(uuid.uuid4())
        (sd / (str(os.getpid()) + ".json")).write_text(json.dumps({
            "pid": os.getpid(), "sessionId": sid, "name": name,
            "messagingSocketPath": str(path), "status": "idle"}))
        env["CLAUDE_CODE_MESSAGING_SOCKET"] = str(path)
        return listener

    def wait_receipt(self, env, mid, status):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            receipt = self.cli(env, "receipt", mid)
            if receipt["status"] == status:
                return receipt
            time.sleep(.2)
        self.fail("receipt did not become %s: %r" % (status, receipt))

    def test_01_default_registration_is_current_session_and_idempotent(self):
        env = self.env("local")
        self.attach_claude(env, "test-local")
        first = self.cli(env, "register")
        again = self.cli(env, "register")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["bus"], "general")
        self.assertEqual(first["status"], "live")
        named = self.cli(env, "register", "--bus", "photonics")
        self.assertEqual(first["id"], named["id"])
        self.cli(env, "leave", "--bus", "general")
        snapshot = self.cli(env, "list", "--json")
        self.assertFalse(next(b for b in snapshot["buses"] if b["name"] == "general")["agents"])
        self.assertEqual(len(next(b for b in snapshot["buses"] if b["name"] == "photonics")["agents"]), 1)
        self.cli(env, "stop")
        # Persistent memberships survive a broker+worker restart; explicit re-register resumes worker.
        restored = self.cli(env, "register", "--bus", "photonics")
        self.assertEqual(restored["id"], first["id"])

    def test_02_independent_installations_roundtrip_over_verified_tls(self):
        a, b = self.env("alice"), self.env("bob")
        listener_a, listener_b = self.attach_claude(a, "alice"), self.attach_claude(b, "bob")
        self.cli(a, "connect", self.code(), "--device", "alice-device")
        self.cli(b, "connect", self.code(), "--device", "bob-device")
        ra, rb = self.cli(a, "register"), self.cli(b, "register")
        message = "--help\nLiteral $HOME `uname` $(touch /tmp/never-execute-bus) 'quoted'\n\n"
        mid = self.cli(a, "send", rb["id"], "--", message)["id"]
        delivered = listener_b.messages.get(timeout=10)
        self.assertIn(message, delivered["message"]["content"])
        self.assertIn(ra["id"], delivered["message"]["content"])
        self.assertIn("communicate bus --hub " + self.url + " send", delivered["message"]["content"])
        self.wait_receipt(a, mid, "delivered")
        reply = self.cli(b, "send", ra["id"], "--from", rb["id"], "--", "BUS-REPLY")["id"]
        self.assertIn("BUS-REPLY", listener_a.messages.get(timeout=10)["message"]["content"])
        self.wait_receipt(b, reply, "delivered")

    def test_03_private_scope_cannot_register_or_discover_general(self):
        env = self.env("private")
        self.attach_claude(env, "private-agent")
        code = self.code("photonics")
        self.cli(env, "connect", code)
        self.cli(env, "connect", code, ok=False)
        self.cli(env, "register", ok=False)
        self.cli(env, "register", "--bus", "photonics")
        snap = self.cli(env, "list", "--json")
        self.assertEqual([b["name"] for b in snap["buses"]], ["photonics"])
        self.assertNotIn("principals", snap)
        self.cli(env, "create", "forbidden", ok=False)

    def test_04_codex_targets_exact_thread_and_reports_queued(self):
        env = self.env("codex")
        tid = str(uuid.uuid4())
        wrong = str(uuid.uuid4())
        env["CODEX_THREAD_ID"] = tid
        env["CODEX_SESSION_ID"] = wrong
        capture = Path(env["HOME"]) / "queue.json"
        binary_dir = Path(env["HOME"]) / "bin"
        binary_dir.mkdir()
        executable = binary_dir / "codex"
        executable.write_text("#!" + sys.executable + "\nimport sys,json\nfrom pathlib import Path\n"
                              "if sys.argv[1:]==['queue','--help']: print('queue help');sys.exit(0)\n"
                              "assert sys.argv[1]=='queue'\nPath(" + repr(str(capture)) + ").write_text(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o755)
        env["PATH"] = str(binary_dir) + os.pathsep + env["PATH"]
        self.cli(env, "connect", self.code())
        target = self.cli(env, "register", "--name", "codex-fixture")
        self.assertEqual(target["status"], "queueable")
        mid = self.cli(env, "send", target["id"], "--", "test queue")["id"]
        self.wait_receipt(env, mid, "queued")
        args = json.loads(capture.read_text())
        self.assertEqual(args[:2], ["queue", "--thread=" + tid])
        self.assertNotIn(wrong, args[1])
        self.assertTrue(args[2].startswith("--message="))

    def test_05_self_detection_never_guesses_latest_session(self):
        env = self.env("unknown")
        home = Path(env["CODEX_HOME"])
        home.mkdir()
        (home / "session_index.jsonl").write_text(json.dumps({"id": str(uuid.uuid4()), "thread_name": "latest"}) + "\n")
        self.assertIn("cannot identify", self.cli(env, "register", ok=False))
        self.assertFalse((Path(env["COMM_STATE"]) / "bus/server.json").exists())

    def test_06_tls_is_required_and_certificate_verified(self):
        for bad in ("http://example.com", "http://localhost:1234", "https://u:p@example.com", "https://example.com/path", "https://example.com#token=x"):
            with self.assertRaises(bus.BusError):
                bus.validate_url(bad)
        env = self.env("untrusted-tls")
        env.pop("SSL_CERT_FILE", None)
        error = self.cli(env, "connect", self.code(), ok=False)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", error)

    def test_07_revocation_stops_existing_connected_client(self):
        env = self.env("revoke")
        self.attach_claude(env, "revoked-agent")
        self.cli(env, "connect", self.code())
        registration = self.cli(env, "register")
        cfg = json.loads((Path(env["COMM_STATE"]) / "bus/client.json").read_text())
        principal = cfg["connections"][self.url]["principal"]
        self.assertTrue(self.broker.handle(self.admin, {"op": "revoke", "principal": principal})["ok"])
        self.cli(env, "list", "--json", ok=False)
        self.cli(env, "send", registration["id"], "--", "must fail", ok=False)
        self.cli(env, "connect", self.code())
        new_registration = self.cli(env, "register")
        self.assertNotEqual(new_registration["id"], registration["id"])

    def test_08_explicit_hub_reply_works_after_default_changes(self):
        env = self.env("multi-hub")
        listener = self.attach_claude(env, "multi-hub-agent")
        self.cli(env, "connect", self.code())
        remote = self.cli(env, "register")
        self.cli(env, "use", "local")
        local = self.cli(env, "register")
        self.assertNotEqual(remote["id"], local["id"])
        cfg_path = Path(env["COMM_STATE"]) / "bus/client.json"
        default = json.loads(cfg_path.read_text())["default"]
        message = self.cli(env, "--hub", self.url, "send", remote["id"], "--from", remote["id"], "--", "right hub")
        frame = listener.messages.get(timeout=10)
        self.assertIn("communicate bus --hub " + self.url, frame["message"]["content"])
        end = time.monotonic() + 10
        while time.monotonic() < end:
            receipt = self.cli(env, "--hub", self.url, "receipt", message["id"])
            if receipt["status"] == "delivered":
                break
            time.sleep(.1)
        self.assertEqual(receipt["status"], "delivered")
        self.assertEqual(json.loads(cfg_path.read_text())["default"], default)

    def test_09_unused_invitation_can_be_revoked_through_cli(self):
        owner = self.env("invite-owner")
        guest = self.env("invite-guest")
        self.cli(owner, "list", "--json")
        owner_cfg = json.loads((Path(owner["COMM_STATE"]) / "bus/client.json").read_text())
        url = owner_cfg["default"]
        code = self.cli(owner, "invite", "--url", url)
        self.cli(owner, "revoke-invite", code)
        self.cli(guest, "connect", code, ok=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
