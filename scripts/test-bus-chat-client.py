#!/usr/bin/env python3
"""Human inbox -> unchanged TLS worker -> Claude socket/Codex argv -> CLI reply.

Disposable homes and session fixtures only. No real agent or user is messaged.
Gateway authentication is covered separately by test-bus-chat.py.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bus_client_fixtures", ROOT / "scripts/test-bus-client.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
Base = fixtures.BusClientTest
READER_HASH = hashlib.sha256(b"isolated-human-client-fixture").hexdigest()


class HumanChatClientTest(unittest.TestCase):
    env = Base.env
    cli = Base.cli
    code = Base.code
    attach_claude = Base.attach_claude
    wait_receipt = Base.wait_receipt
    tearDownClass = classmethod(Base.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        fixtures.BusClientTest.setUpClass.__func__(cls)
        cls.broker.gateway_shared_secret = "isolated-client-fixture-" + "s" * 40
        cls.broker.reader_users = {"human-fixture": "human-fixture"}
        cls.broker.chat_readers = {"human-fixture": {"identity": "human-fixture", "buses": ["general"]}}
        real_handler = fixtures.handler_factory(cls.broker)

        class GatewayFixture(real_handler):
            def _gateway_context(self):
                # This local TLS endpoint models the existing trusted gateway.
                self.headers["X-Communicate-Bus-Gateway"] = cls.broker.gateway_shared_secret
                return super()._gateway_context()

        cls.server.RequestHandlerClass = GatewayFixture
        cls.human_token = cls.broker.browser_session("human-fixture", READER_HASH)["token"]

    def human(self, op, **fields):
        result = self.broker.handle(self.human_token, {"op": op, **fields}, reader="human-fixture", reader_hash=READER_HASH)
        self.assertTrue(result["ok"], result)
        return result

    def send_human(self, target):
        chat = self.human("chat_open", bus="general", agent=target)["chat"]["id"]
        text = "Human fixture only: literal $(not-executed) `not-executed`\nWhat is the update?"
        sent = self.human("chat_send", chat=chat, request_id=str(uuid.uuid4()), message=text)["message"]
        return chat, sent, text

    def assert_reply(self, env, registration, chat, sent, content):
        result = self.cli(env, "--hub", self.url, "reply", sent["id"], "--from", registration,
                          "--", content)
        self.assertEqual(result["status"], "replied")
        history = self.human("chat_messages", chat=chat)["messages"]
        self.assertEqual([row["role"] for row in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"], content)
        self.assertEqual(history[1]["in_reply_to"], sent["id"])
        self.assertEqual(self.human("chat_list")["chats"][0]["unread"], 1)

    def test_claude_socket_receives_human_and_cli_reply_enters_own_inbox(self):
        env = self.env("human-to-claude")
        listener = self.attach_claude(env, "existing-claude-fixture")
        self.cli(env, "connect", self.code())
        agent = self.cli(env, "register")["id"]
        chat, sent, text = self.send_human(agent)
        delivered = listener.messages.get(timeout=10)["message"]["content"]
        self.assertIn(text, delivered)
        self.assertIn("human.human-fixture", delivered)
        self.assertIn("reply " + sent["id"] + " --from " + agent, delivered)
        self.wait_receipt(env, sent["id"], "delivered")
        self.assert_reply(env, agent, chat, sent, "Claude fixture reply")
        self.assertTrue(listener.messages.empty(), "Human reply must not be routed back into the agent")

    def test_codex_queues_exact_thread_and_cli_reply_enters_own_inbox(self):
        env = self.env("human-to-codex")
        thread = str(uuid.uuid4())
        wrong = str(uuid.uuid4())
        env["CODEX_THREAD_ID"], env["CODEX_SESSION_ID"] = thread, wrong
        capture = Path(env["HOME"]) / "human-queue.json"
        executable = Path(env["HOME"]) / "bin/codex"
        executable.write_text("#!" + sys.executable + "\nimport sys,json\nfrom pathlib import Path\n"
                              "if sys.argv[1:]==['queue','--help']: print('queue help');sys.exit(0)\n"
                              "assert sys.argv[1]=='queue'\nPath(" + repr(str(capture)) + ").write_text(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o755)
        self.cli(env, "connect", self.code())
        agent = self.cli(env, "register")["id"]
        chat, sent, text = self.send_human(agent)
        self.wait_receipt(env, sent["id"], "queued")
        args = json.loads(capture.read_text())
        self.assertEqual(args[:2], ["queue", "--thread=" + thread])
        self.assertNotIn(wrong, args[1])
        self.assertIn(text, args[2])
        self.assertIn("reply " + sent["id"] + " --from " + agent, args[2])
        self.assert_reply(env, agent, chat, sent, "Codex fixture reply")


del Base  # Keep unittest discovery limited to the two human-chat checks.

if __name__ == "__main__":
    unittest.main()
