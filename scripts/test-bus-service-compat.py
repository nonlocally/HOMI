#!/usr/bin/env python3
"""Preserve deployed broker service identities and durable reply deduplication.

Broker-only regressions adapted from test-bus-service.py on the specialist
branch. No service client, provider, network, or production state is used.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from bus_broker import Broker


class ServiceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bus-service-compat-')
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            'BUS_GATEWAY_SHARED_SECRET': 'service-fixture-' + 's' * 40, 'BUS_READER_USERS': '{}',
            'BUS_ADMIN_READERS': '', 'BUS_CHAT_READERS': '{}',
            'BUS_ACCOUNT_LABELS': '{}', 'BUS_OPENWEBUI_READERS': '0',
            'BUS_CHAT_OPENWEBUI_TARGETS': '[]'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.now = 1800000000
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        self.sender = self.call('register', session_key='requester', name='Requester', kind='codex')['id']
        # Model a registration already committed by the deployed service patch.
        self.service = self.call('register', session_key='service:fixture', name='Service', kind='codex')['id']
        with self.b._connect() as db:
            db.execute("UPDATE agents SET kind='service' WHERE id=?", (self.service,))
        self.message = self.call('send', sender=self.sender, target=self.service, bus='general', message='Fixture task')['id']
        self.assertEqual(self.call('poll', agent=self.service)['messages'][0]['id'], self.message)

    def call(self, op, **fields):
        result = self.b.handle(self.b.admin_token, dict(fields, op=op))
        self.assertTrue(result.get('ok'), result)
        return result

    def reply(self, **fields):
        return self.call('reply', **dict({'sender': self.service, 'id': self.message,
                                         'message': 'Exact answer', 'request_id': 'stable-key'}, **fields))

    def test_existing_service_registration_keeps_id_and_membership_after_restart(self):
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        registered = self.call('register', session_key='service:fixture', name='Renamed service', kind='service')
        self.assertEqual(registered['id'], self.service)
        self.assertEqual(registered['buses'], ['general'])
        agents = [agent for bus in self.call('snapshot')['buses'] for agent in bus['agents']]
        self.assertEqual([a['id'] for a in agents if a['kind'] == 'service'], [self.service])
        with self.b._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agents').fetchone()[0], 2)

    def test_exact_reply_retry_survives_restart_without_duplicate_delivery(self):
        one = self.reply()
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        self.assertEqual(self.reply(), one)
        messages = self.call('poll', agent=self.sender)['messages']
        self.assertEqual([m['id'] for m in messages], [one['id']])
        self.assertEqual(messages[0]['message'], 'Exact answer')
        with self.b._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reply_requests').fetchone()[0], 1)

    def test_concurrent_reply_retry_is_atomic_and_conflicting_content_is_denied(self):
        brokers = [Broker(self.tmp.name, clock=lambda: self.now) for _ in range(8)]
        request = {'op': 'reply', 'sender': self.service, 'id': self.message,
                   'message': 'Exact answer', 'request_id': 'stable-key'}
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda i: brokers[i % 8].handle(self.b.admin_token, request), range(16)))
        self.assertTrue(all(r['ok'] for r in replies), replies)
        self.assertEqual(len({r['id'] for r in replies}), 1)
        conflict = self.b.handle(self.b.admin_token, dict(request, message='Changed answer'))
        self.assertEqual(conflict.get('code'), 'conflict')
        for field in ('target', 'bus', 'conversation', 'chat', 'identity'):
            denied = self.b.handle(self.b.admin_token, dict(request, **{field: 'forged'}))
            self.assertFalse(denied['ok'], field)
        denied = self.b.handle(self.b.admin_token, dict(request, sender=self.sender))
        self.assertFalse(denied['ok'])
        with self.b._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages WHERE sender=?', (self.service,)).fetchone()[0], 1)

    def test_legacy_replies_without_request_id_keep_original_semantics(self):
        fields = {'sender': self.service, 'id': self.message, 'message': 'Legacy answer'}
        one = self.call('reply', **fields)
        two = self.call('reply', **fields)
        self.assertNotEqual(one['id'], two['id'])
        with self.b._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reply_requests').fetchone()[0], 0)

    def test_human_reply_retry_survives_restart_without_duplicate_chat_entry(self):
        spec = importlib.util.spec_from_file_location('service_chat_fixture', ROOT / 'scripts/test-bus-chat.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        fixture = module.ChatTests(); fixture.setUp()
        try:
            fixture.send(); message = fixture.poll()[0]
            fields = {'sender': fixture.agent, 'id': message['id'],
                      'message': 'Exact human answer', 'request_id': 'human-key'}
            one = fixture.admin('reply', **fields)
            fixture.b = Broker(fixture.tmp.name, clock=lambda: fixture.now)
            self.assertEqual(fixture.admin('reply', **fields), one)
            with fixture.b._connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM human_chat_messages WHERE role='assistant'").fetchone()[0], 1)
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
