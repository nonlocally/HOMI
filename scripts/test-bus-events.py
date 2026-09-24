#!/usr/bin/env python3
"""Shared event codes preserve device identity and exact private-bus scope."""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import bus
from bus_broker import Broker, BusHTTPServer, handler_factory


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bus-events-'); self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {'BUS_GATEWAY_SHARED_SECRET': 'fixture-' + 's' * 40,
                     'BUS_READER_USERS': json.dumps({'alice-reader': 'alice', 'bob-reader': 'bob'}),
                     'BUS_ADMIN_READERS': '', 'BUS_CHAT_READERS': '{}', 'BUS_ACCOUNT_LABELS': '{}'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.now = 1800000000
        self.b = Broker(self.tmp.name, clock=lambda: self.now)
        self.sessions = {}
        for reader in ('alice-reader', 'bob-reader'):
            digest = hashlib.sha256(reader.encode()).hexdigest()
            self.sessions[reader] = (self.b.browser_session(reader, digest)['token'], digest)
        self.call('create', bus='event-bus')
        self.call('create', bus='other-bus')

    def raw(self, op, who='admin', **fields):
        if who in self.sessions:
            token, digest = self.sessions[who]
            return self.b.handle(token, dict(fields, op=op), reader=who, reader_hash=digest)
        return self.b.handle(self.b.admin_token if who == 'admin' else who, dict(fields, op=op))

    def call(self, op, who='admin', **fields):
        result = self.raw(op, who, **fields); self.assertTrue(result.get('ok'), result); return result

    def denied(self, op, who='admin', code='forbidden', **fields):
        result = self.raw(op, who, **fields); self.assertFalse(result.get('ok'), result)
        self.assertEqual(result['code'], code, result); return result

    def event(self, **fields):
        return self.call('event_create', bus='event-bus', **fields)

    def rows(self, who='admin'):
        return {row['name']: row for row in self.call('snapshot', who)['buses']}

    def test_one_code_many_guests_unique_credentials_and_no_account_impersonation(self):
        event = self.event(); self.assertEqual(event['event']['max_uses'], 40)
        self.assertEqual(event['expires_at'], self.now + 14400)
        guests = [self.call('redeem', '', invite=event['invite'], device='Person '+str(i), user='alice', owner='alice') for i in range(3)]
        for field in ('principal', 'token', 'user'):
            self.assertEqual(len({g[field] for g in guests}), 3)
        for guest in guests:
            self.assertTrue(guest['user'].startswith('guest-'))
            self.assertEqual(guest['buses'], ['event-bus'])
            snapshot = self.call('snapshot', guest['token'])
            self.assertFalse(snapshot['is_admin']); self.assertFalse(snapshot['can_create_bus'])
            self.assertNotIn('events', snapshot['buses'][0])
            self.denied('create', guest['token'], bus='mine')
            self.denied('register', guest['token'], bus='general', name='spoof', session_key='spoof')
            self.denied('register', guest['token'], bus='other-bus', name='spoof', session_key='spoof')
            self.call('register', guest['token'], bus='event-bus', name='exact', session_key='exact')
        row = self.rows()['event-bus']['events'][0]
        self.assertEqual(row['uses'], 3); self.assertNotIn('participants', row)
        detail = self.call('event_get', event=row['id'])['event']
        self.assertEqual(len(detail['participants']), 3)
        self.assertNotIn(event['invite'], json.dumps(self.call('snapshot')))
        data = self.b.db_path.read_bytes(); self.assertNotIn(event['invite'].encode(), data)
        for guest in guests: self.assertNotIn(guest['token'].encode(), data)

    def test_existing_bearer_preserves_account_and_repeated_join_is_idempotent(self):
        personal = self.call('invite', bus='other-bus', user='bob')['invite']
        device = self.call('redeem', '', invite=personal, device='Existing Bob')
        event = self.event(max_uses=1)
        result = self.call('redeem', device['token'], invite=event['invite'], user='alice', device='Spoof')
        for field in ('principal', 'token', 'user', 'device'): self.assertEqual(result[field], device[field])
        self.assertEqual(result['buses'], ['event-bus', 'other-bus'])
        self.call('redeem', device['token'], invite=event['invite'])
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'], 1)
        self.denied('redeem', '', code='limit', invite=event['invite'])
        self.denied('redeem', 'invalid-existing-bearer', invite=event['invite'])
        self.call('revoke', principal=device['principal'])
        self.denied('redeem', device['token'], invite=event['invite'])
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'], 1)

    def test_cap_is_atomic_across_broker_instances(self):
        event = self.event(max_uses=3)
        brokers = [Broker(self.tmp.name, clock=lambda: self.now) for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda b: b.handle(None, {'op':'redeem','invite':event['invite'],'device':'concurrent'}), brokers))
        self.assertEqual(sum(r['ok'] for r in results), 3)
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'], 3)
        self.assertTrue(all(r.get('code') == 'limit' for r in results if not r['ok']))

    def test_same_authenticated_device_racing_retries_consume_one_slot(self):
        personal=self.call('invite',bus='other-bus',user='bob')['invite']
        device=self.call('redeem','',invite=personal)
        event=self.event(max_uses=1)
        brokers=[Broker(self.tmp.name,clock=lambda:self.now) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda b:b.handle(device['token'],{'op':'redeem','invite':event['invite']}),brokers))
        self.assertTrue(all(r['ok'] for r in results),results)
        self.assertEqual({r['principal'] for r in results},{device['principal']})
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'],1)

    def test_expiry_and_revocation_stop_joins_but_keep_admitted_devices(self):
        event = self.event(ttl=60)
        guest = self.call('redeem', '', invite=event['invite'])
        self.now += 60
        self.denied('redeem', '', invite=event['invite'])
        self.assertFalse(self.rows()['event-bus']['events'][0]['active'])
        self.assertEqual(list(self.rows(guest['token'])), ['event-bus'])
        second = self.event(); self.call('event_revoke', event=second['event']['id'])
        self.call('event_revoke', event=second['event']['id'])
        self.denied('redeem', '', invite=second['invite'])
        self.assertEqual(list(self.rows(guest['token'])), ['event-bus'])

    def test_account_owner_can_manage_events_without_account_membership_for_guests(self):
        self.call('create', 'alice-reader', bus='owned')
        self.call('member_add', 'alice-reader', bus='owned', user='bob')
        event = self.call('event_create', 'alice-reader', bus='owned')
        guest = self.call('redeem', '', invite=event['invite'])
        self.assertEqual(list(self.rows(guest['token'])), ['owned'])
        self.assertEqual(self.rows('alice-reader')['owned']['members'], [{'user':'alice','role':'owner'},{'user':'bob','role':'member'}])
        for who in ('bob-reader', guest['token']):
            self.denied('event_create', who, bus='owned')
            self.denied('event_revoke', who, event=event['event']['id'])
            self.denied('event_remove', who, event=event['event']['id'], principal=guest['principal'])
        self.denied('redeem', 'alice-reader', invite=event['invite'])
        self.denied('redeem', 'admin', invite=event['invite'])
        self.assertNotIn('events', self.rows('bob-reader')['owned'])
        self.call('event_revoke', 'alice-reader', event=event['event']['id'])

    def test_other_private_bus_owner_cannot_inspect_or_manage_foreign_event(self):
        self.call('create','alice-reader',bus='alice-bus')
        self.call('create','bob-reader',bus='bob-bus')
        event=self.call('event_create','alice-reader',bus='alice-bus')
        guest=self.call('redeem','',invite=event['invite'])
        self.assertNotIn(event['event']['id'],json.dumps(self.call('snapshot','bob-reader')))
        self.denied('event_get','bob-reader',event=event['event']['id'])
        self.denied('event_revoke','bob-reader',event=event['event']['id'])
        self.denied('event_remove','bob-reader',event=event['event']['id'],principal=guest['principal'])
        self.denied('event_create','bob-reader',bus='alice-bus')
        self.assertTrue(self.rows('alice-reader')['alice-bus']['events'][0]['active'])

    def test_event_details_require_exact_bus_owner_or_admin_and_never_return_secrets(self):
        self.call('create', 'alice-reader', bus='owned')
        self.call('create', 'bob-reader', bus='bobs-bus')
        self.call('member_add', 'alice-reader', bus='owned', user='bob')
        event = self.call('event_create', 'alice-reader', bus='owned')
        guest = self.call('redeem', '', invite=event['invite'], device='Guest')
        event_id = event['event']['id']
        summary = self.rows('alice-reader')['owned']['events'][0]
        self.assertNotIn('participants', summary)
        for who in ('alice-reader', 'admin'):
            detail = self.call('event_get', who, event=event_id)['event']
            self.assertEqual({k: v for k, v in detail.items() if k != 'participants'}, summary)
            self.assertEqual([p['principal'] for p in detail['participants']], [guest['principal']])
            self.assertNotIn(event['invite'], json.dumps(detail))
            self.assertNotIn(guest['token'], json.dumps(detail))
            self.assertNotIn('digest', detail)
        for who in ('bob-reader', guest['token']):
            self.denied('event_get', who, event=event_id)
        self.denied('event_get', '', code='unauthorized', event=event_id)
        self.denied('event_get', 'alice-reader', code='not_found', event='missing')
        for invalid in (None, [], {}, 123, 'x' * 129):
            self.denied('event_get', 'alice-reader', code='invalid_request', event=invalid)
        self.assertEqual(self.rows('alice-reader')['owned']['events'][0]['uses'], 1)
        self.assertEqual(list(self.rows(guest['token'])), ['owned'])

    def test_large_valid_event_history_stays_below_existing_client_response_limit(self):
        # Reproduce the reported overflow using only permitted operations:
        # two buses, 20 events each, the same 100 devices, and valid 128-byte
        # labels. No new principals or fake capacity overrides after event 1.
        guests = []
        for name in ('event-bus', 'other-bus'):
            for _ in range(20):
                event = self.call('event_create', bus=name, max_uses=100)
                if not guests:
                    guests = [self.call('redeem', '', invite=event['invite'], device='\U0001f9ea' * 32)
                              for _ in range(100)]
                else:
                    for guest in guests:
                        self.call('redeem', guest['token'], invite=event['invite'])
                self.call('event_revoke', event=event['event']['id'])
        with self.b._connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_joins').fetchone()[0], 4000)
        snapshot = self.call('snapshot')
        raw = json.dumps(snapshot).encode()
        self.assertLess(len(raw), 2 * 1024 * 1024)
        history = [event for row in snapshot['buses'] for event in row.get('events', [])]
        self.assertEqual(len(history), 40)
        self.assertTrue(all('participants' not in event for event in history))
        detail = self.call('event_get', event=history[0]['id'])['event']
        self.assertEqual(len(detail['participants']), 100)
        self.assertLess(len(json.dumps(detail).encode()), 2 * 1024 * 1024)
        # Exercise the unchanged v0.4 client decoder and its byte cap without
        # opening a socket or widening that cap.
        with mock.patch('bus.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(raw)
            decoded = bus.request({'url': 'https://broker.example', 'token': self.b.admin_token}, 'snapshot')
        self.assertEqual(decoded, snapshot)

    def test_participant_removal_only_affects_target_bus_and_closes_pending_delivery(self):
        event = self.event()
        person = self.call('redeem', '', invite=self.call('invite', bus='other-bus', user='bob')['invite'])
        guest = self.call('redeem', person['token'], invite=event['invite'])
        other = self.call('redeem', '', invite=event['invite'])
        for g in (guest,other):g['agent']=self.call('register', g['token'], bus='event-bus', name='guest', session_key='s')['id']
        message = self.call('send', other['token'], bus='event-bus', sender=other['agent'], target=guest['agent'], message='pending')
        self.call('poll', guest['token'], agent=guest['agent'])
        self.call('event_remove', event=event['event']['id'], principal=guest['principal'])
        self.call('event_remove', event=event['event']['id'], principal=guest['principal'])
        self.assertEqual(self.call('receipt', other['token'], id=message['id'])['status'], 'cancelled')
        self.assertEqual(list(self.rows(guest['token'])), ['other-bus'])
        self.denied('redeem', guest['token'], invite=event['invite'])
        row = self.call('event_get', event=event['event']['id'])['event']
        self.assertTrue(next(p for p in row['participants'] if p['principal']==guest['principal'])['removed'])
        self.assertEqual(row['uses'], 2, 'removal must not refill the public event cap')
        self.denied('event_remove', code='not_found', event=event['event']['id'], principal='admin')

    def test_remove_prevents_replay_through_other_already_joined_event_on_same_bus(self):
        first,second = self.event(),self.event()
        guest = self.call('redeem', '', invite=first['invite'])
        self.call('redeem', guest['token'], invite=second['invite'])
        self.call('event_remove', event=first['event']['id'], principal=guest['principal'])
        self.denied('redeem', guest['token'], invite=second['invite'])
        self.assertEqual(self.rows(guest['token']), {})

    def test_personal_invites_stay_one_use_and_event_limits_validate_before_writes(self):
        personal = self.call('invite', bus='event-bus', user='alice')['invite']
        self.call('redeem', '', invite=personal)
        self.denied('redeem', '', invite=personal)
        for fields in ({'ttl':59},{'ttl':86401},{'ttl':True},{'ttl':2.5},{'max_uses':0},{'max_uses':101},{'max_uses':True}):
            self.denied('event_create', code='invalid_request', bus='event-bus', **fields)
        self.denied('event_create', bus='general')
        with mock.patch('bus_broker.MAX_ACTIVE_EVENTS_PER_BUS',1):
            self.event(); self.denied('event_create', code='limit', bus='event-bus')
        self.assertEqual(len(self.rows()['event-bus']['events']), 1)

    def test_failed_guest_validation_and_capacity_do_not_consume_use_or_create_principal(self):
        event = self.event(max_uses=1)
        with self.b._connect() as db: before=db.execute('SELECT COUNT(*) FROM principals').fetchone()[0]
        self.denied('redeem','',code='invalid_request',invite=event['invite'],device_metadata={'user':'alice'})
        with mock.patch('bus_broker.MAX_PRINCIPALS',before):self.denied('redeem','',code='limit',invite=event['invite'])
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'], 0)
        with self.b._connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM principals').fetchone()[0],before)
        self.call('redeem','',invite=event['invite'])

    def test_old_broker_refuses_shared_code_without_consumption_then_reupgrade_works(self):
        event=self.event();guest=self.call('redeem','',invite=event['invite'])
        fixture=Path(__file__).parent/'fixtures/bus_broker_v04.py'
        spec=importlib.util.spec_from_file_location('event_old_broker',fixture);released=importlib.util.module_from_spec(spec);spec.loader.exec_module(released)
        old=released.Broker(self.tmp.name,clock=lambda:self.now)
        refused=old.handle(None,{'op':'redeem','invite':event['invite']})
        self.assertFalse(refused['ok']);self.assertEqual(refused['code'],'forbidden')
        self.assertEqual(old.handle(guest['token'],{'op':'snapshot'})['buses'][0]['name'],'event-bus')
        self.b=Broker(self.tmp.name,clock=lambda:self.now)
        self.assertEqual(self.rows()['event-bus']['events'][0]['uses'],1)
        self.call('redeem','',invite=event['invite'])

    def test_unmodified_v04_connect_stdin_works_and_invalid_existing_auth_never_falls_back(self):
        # lib/bus.py has no changes from published v0.4.0; exercise its actual
        # connect parser, HTTP transport, config save and unauthorized fallback.
        with tempfile.TemporaryDirectory(prefix='event-cli-broker-') as state, mock.patch.dict(os.environ, {}, clear=True):
            broker=Broker(state,clock=lambda:self.now)
            self.assertTrue(broker.handle(broker.admin_token,{'op':'create','bus':'event-bus'})['ok'])
            event=broker.handle(broker.admin_token,{'op':'event_create','bus':'event-bus'})
            self.assertTrue(event['ok'],event)
            server=BusHTTPServer(('127.0.0.1',0),handler_factory(broker));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                origin='http://127.0.0.1:'+str(server.server_port)
                code='commbus1.'+base64.urlsafe_b64encode(json.dumps({'url':origin,'invite':event['invite']}).encode()).decode().rstrip('=')
                with tempfile.TemporaryDirectory(prefix='event-cli-home-') as home, mock.patch.dict(os.environ,{'HOME':home,'COMM_STATE':home+'/state'}), mock.patch('bus.device_metadata',return_value={}):
                    with mock.patch('sys.stdin',io.StringIO(code)):
                        joined=bus.run(bus.parser().parse_args(['connect','--invite-stdin','--device','Attendee']))
                    self.assertEqual(joined['buses'],['event-bus']);self.assertTrue(joined['user'].startswith('guest-'))
                    cfg=bus.config();cfg['connections'][origin]['token']='invalid-stored-credential';bus.write_json(bus.state_dir()/'client.json',cfg)
                    before=(bus.state_dir()/'client.json').read_bytes()
                    with mock.patch('sys.stdin',io.StringIO(code)),self.assertRaises(bus.BusError) as refused:
                        bus.run(bus.parser().parse_args(['connect','--invite-stdin']))
                    self.assertEqual(refused.exception.code,'forbidden')
                    self.assertEqual((bus.state_dir()/'client.json').read_bytes(),before)
                    self.assertEqual(broker.handle(broker.admin_token,{'op':'snapshot'})['buses'][0]['events'][0]['uses'],1)
            finally:server.shutdown();thread.join();server.server_close()


if __name__=='__main__':unittest.main(verbosity=2)
