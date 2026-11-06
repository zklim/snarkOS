#!/usr/bin/env python3
"""Regressions for recovery devnet evidence and retained input integrity."""

import io
import json
import urllib.error
import urllib.request
import pathlib
import tempfile
import unittest

from recovery_resume import file_inventory, read_cache, restore_cache, validate_upgrade
from test_disclosed_recovery import PacedHttp, accepted_height, accepted_location, read_json


class RequestPacing(unittest.TestCase):
    def test_reads_and_posts_share_endpoint_spacing(self):
        now, sleeps, calls = [0], [], []

        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay

        class Opener:
            def open(self, address, timeout):
                calls.append((now[0], address, timeout))
                return io.BytesIO(b'{}')

        http = PacedHttp(Opener(), clock=lambda: now[0], sleep=sleep)
        http.open('http://127.0.0.1:3030/state', timeout=5)
        post = urllib.request.Request('http://127.0.0.1:3030/broadcast', data=b'{}', method='POST')
        http.open(post, timeout=90)
        http.open('http://127.0.0.1:3031/state', timeout=5)
        http.open('http://127.0.0.1:3030/state', timeout=5)
        self.assertEqual(sleeps, [.2, .2])
        self.assertEqual([call[0] for call in calls], [0, .2, .2, .4])
        self.assertEqual(calls[1], (.2, post, 90))

    def test_rate_limited_post_is_forwarded_once(self):
        calls = []

        class Opener:
            def open(self, address, timeout):
                calls.append(address)
                raise urllib.error.HTTPError(address.full_url, 429, 'limited', {}, None)

        http = PacedHttp(Opener())
        post = urllib.request.Request('http://127.0.0.1:3030/broadcast', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError):
            http.open(post, timeout=90)
        self.assertEqual(calls, [post])


class RateLimitedReads(unittest.TestCase):
    def test_429_retry_is_bounded_and_other_failures_propagate(self):
        class Http:
            def __init__(self, count, code=429):
                self.count, self.code, self.calls = count, code, 0
            def open(self, address, timeout):
                self.calls += 1
                if self.calls <= self.count:
                    raise urllib.error.HTTPError(address, self.code, 'test', {'Retry-After': '1'}, None)
                return io.BytesIO(b'{"ok":true}')
        sleeps = []
        http = Http(1)
        self.assertEqual(read_json(http, 'http://local', sleeps.append), {'ok': True})
        self.assertEqual(sleeps, [1])
        http = Http(99)
        with self.assertRaises(urllib.error.HTTPError):
            read_json(http, 'http://local', lambda _: None)
        self.assertEqual(http.calls, 6)
        http = Http(1, 422)
        with self.assertRaises(urllib.error.HTTPError):
            read_json(http, 'http://local', lambda _: self.fail('Unexpected retry'))
        self.assertEqual(http.calls, 1)


class AcceptanceEvidence(unittest.TestCase):
    def setUp(self):
        self.transaction = {'id': 'transaction-A', 'type': 'recovery', 'effects': 'original'}
        self.confirmed = {'status': 'accepted', 'type': 'recovery', 'index': 0,
                          'transaction': self.transaction, 'finalize': []}
        self.block = {'transactions': [self.confirmed], 'aborted_transaction_ids': [],
                      'header': {'metadata': {'height': 60}}}

    def test_exact_accepted_effects(self):
        self.assertEqual(accepted_height(self.transaction, self.confirmed, self.block), 60)

    def test_aborted_id_or_rejected_confirmation_is_not_acceptance(self):
        self.block['transactions'] = []
        self.block['aborted_transaction_ids'] = [self.transaction['id']]
        self.assertIsNone(accepted_height(self.transaction, None, self.block))
        rejected = dict(self.confirmed, status='rejected')
        self.assertIsNone(accepted_height(self.transaction, rejected, self.block))

    def test_changed_effects_or_type(self):
        for field, value in [('effects', 'attacker'), ('type', 'execute')]:
            changed = dict(self.transaction, **{field: value})
            with self.subTest(field=field), self.assertRaises(AssertionError):
                accepted_height(changed, self.confirmed, self.block)

    def test_missing_duplicate_or_changed_block_entry(self):
        for entries in [[], [self.confirmed, self.confirmed],
                        [dict(self.confirmed, finalize=['other'])]]:
            block = dict(self.block, transactions=entries)
            with self.subTest(entries=entries), self.assertRaises(AssertionError):
                accepted_height(self.transaction, self.confirmed, block)

    def test_accepted_and_aborted_cannot_both_count(self):
        block = dict(self.block, aborted_transaction_ids=[self.transaction['id']])
        with self.assertRaises(AssertionError):
            accepted_height(self.transaction, self.confirmed, block)

    def test_aborted_lookup_does_not_query_confirmation_endpoint(self):
        block = dict(self.block, transactions=[], aborted_transaction_ids=[self.transaction['id']])
        calls = []

        def query(route):
            calls.append(route)
            if route.startswith('transaction/confirmed/'):
                raise RuntimeError('The server returns HTTP 500 for an aborted confirmation')
            return block if route.startswith('block/') else 'block-hash'

        self.assertIsNone(accepted_location(self.transaction, query))
        self.assertEqual(calls, ['find/blockHash/transaction-A', 'block/block-hash'])


class ResumeIntegrity(unittest.TestCase):
    def test_cached_proof_must_upgrade_exact_historical_serial(self):
        transaction = {'type': 'execute', 'execution': {'transitions': [
            {'program': 'credits.aleo', 'function': 'upgrade', 'inputs': [{'type': 'record', 'id': 'serial-A'}]}]}}
        validate_upgrade(transaction, 'serial-A')
        with self.assertRaises(AssertionError):
            validate_upgrade(transaction, 'serial-B')
        transaction['execution']['transitions'][0]['function'] = 'transfer_private'
        with self.assertRaises(AssertionError):
            validate_upgrade(transaction, 'serial-A')

    def test_each_node_identity_survives_and_tampering_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            run = pathlib.Path(directory) / 'retained'
            state = run / 'legacy-resume-state'
            for i in range(4):
                for kind in ['ledger', 'data']:
                    path = state / f'{kind}-{i}'
                    path.mkdir(parents=True)
                    (path / 'identity').write_text(f'{kind}:{i}')
            manifest = {'format': 1, 'state_directory': state.name,
                        'state_files': file_inventory(state), 'proofs': {}}
            (run / 'legacy-cache.json').write_text(json.dumps(manifest))
            destination = pathlib.Path(directory) / 'restored'
            destination.mkdir()
            restore_cache(run, destination)
            for i in range(4):
                self.assertEqual((destination / f'data-{i}' / 'identity').read_text(), f'data:{i}')
            (state / 'ledger-1' / 'identity').write_text('tampered')
            with self.assertRaises(AssertionError):
                read_cache(run)

    def test_symlink_is_not_a_retained_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'source').write_text('public')
            (root / 'link').symlink_to(root / 'source')
            with self.assertRaises(ValueError):
                file_inventory(root)


if __name__ == '__main__':
    unittest.main()
