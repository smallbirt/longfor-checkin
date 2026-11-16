import copy
import io
import json
import random
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

from app.captcha import SliderSolver, CaptchaError, valid_proof
from app.client import LongforClient
from app.constants import TZ
from app.runner import Runner
from app.store import Store
from app.vendor.longfor_captcha.trajectory_enhanced import generate_realistic_trajectory
from app.vendor.longfor_captcha.ac_encoder_full import ACEncoderFull
from test_core import credential, page, Transport, DAY


PROOF = 'fresh-test-proof'
CLOCK_OK = {'code': '0000', 'data': {}}


class CaptchaClientTests(unittest.TestCase):
    def test_both_channels_use_fresh_proof_once_and_confirm(self):
        for key in ['app', 'mini']:
            with self.subTest(channel=key):
                cred = credential(key)
                before = copy.deepcopy(cred)
                t = Transport([page(), {'code': '8040012'}, page(), CLOCK_OK, page(20)])
                solver = MagicMock(return_value=PROOF)
                result = LongforClient(t, solver).run(cred, DAY)
                self.assertEqual(result['state'], 'signed')
                self.assertTrue(result['captcha_solved'])
                solver.assert_called_once_with()
                clock_calls = [kw for url, kw in t.calls if url.endswith('/clock')]
                self.assertEqual(len(clock_calls), 2)
                self.assertNotIn('x-lf-dxrisk-captcha-token', clock_calls[0]['headers'])
                self.assertEqual(clock_calls[1]['headers']['x-lf-dxrisk-captcha-token'], PROOF)
                self.assertEqual(clock_calls[1]['headers']['x-lf-channel'], cred['headers']['x-lf-channel'])
                self.assertEqual(cred, before)
                self.assertNotIn(PROOF, json.dumps(result))
                for url, kw in t.calls:
                    if not url.endswith('/clock'):
                        self.assertNotIn('x-lf-dxrisk-captcha-token', kw['headers'])

    def test_old_block_recovers_without_an_unverified_clock_request(self):
        cred = credential(); cred['blocked'] = 'captcha_required'
        t = Transport([page(), page(), CLOCK_OK, page(20)])
        result = LongforClient(t, lambda: PROOF).run(cred, DAY)
        self.assertEqual(result['state'], 'signed')
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)

    def test_solver_failure_does_not_resubmit_or_leak(self):
        t = Transport([page(), {'code': '8040012'}])
        result = LongforClient(t, MagicMock(side_effect=RuntimeError(PROOF))).run(credential(), DAY)
        self.assertEqual(result['state'], 'captcha_required')
        self.assertEqual(len(t.calls), 2)
        self.assertNotIn(PROOF, json.dumps(result))

    def test_rejected_fresh_proof_does_not_loop(self):
        t = Transport([page(), {'code': '8040012'}, page(), {'code': '8040016'}])
        solver = MagicMock(return_value=PROOF)
        result = LongforClient(t, solver).run(credential(), DAY)
        self.assertEqual(result['state'], 'captcha_required')
        solver.assert_called_once()
        self.assertEqual(len(t.calls), 4)

    def test_timeout_after_proof_only_queries(self):
        t = Transport([page(), {'code': '8040012'}, page(), requests.Timeout(PROOF), page(20)])
        solver = MagicMock(return_value=PROOF)
        result = LongforClient(t, solver).run(credential(), DAY)
        self.assertEqual(result['state'], 'signed')
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 2)
        solver.assert_called_once()

    def test_signin_during_solver_skips_submission(self):
        t = Transport([page(), {'code': '8040012'}, page(20)])
        result = LongforClient(t, lambda: PROOF).run(credential(), DAY)
        self.assertEqual(result['state'], 'signed')
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)

    def test_recheck_failure_does_not_resubmit(self):
        t = Transport([page(), {'code': '8040012'}, requests.Timeout()])
        result = LongforClient(t, lambda: PROOF).run(credential(), DAY)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)

    def test_queries_and_auth_blocks_do_not_solve(self):
        for blocked, allow_sign in [('captcha_required', False), ('auth_expired', True)]:
            cred = credential(); cred['blocked'] = blocked
            solver = MagicMock()
            result = LongforClient(Transport([page()]), solver).run(cred, DAY, allow_sign)
            self.assertIn(result['state'], ['unsigned', 'auth_expired'])
            solver.assert_not_called()

    def test_successful_query_clears_legacy_block_without_storing_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            cred = credential(); cred['blocked'] = 'captcha_required'
            store.put('credential:app', cred)
            client = LongforClient(Transport([page(20)]), MagicMock())
            Runner(store, client).run('check', 'app', datetime(2026, 10, 16, 10, tzinfo=TZ))
            self.assertNotIn('blocked', store.get('credential:app'))
            self.assertNotIn(PROOF, json.dumps(store.get('credential:app')))

    def test_signed_result_does_not_unblock_new_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            old = credential(); old['blocked'] = 'captcha_required'
            store.put('credential:app', old)
            newer = copy.deepcopy(old)
            newer['headers']['token'] = newer['headers']['x-lf-usertoken'] = 'new-import'
            def finish_old(*args, **kwargs):
                store.put('credential:app', newer)
                return {'state': 'signed', 'message': 'confirmed'}
            client = MagicMock(); client.run.side_effect = finish_old
            Runner(store, client).run('check', 'app', datetime(2026, 10, 16, 10, tzinfo=TZ))
            self.assertEqual(store.get('credential:app'), newer)


class SolverTransport:
    def __init__(self, data):
        self.data = iter(data)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = next(self.data)
        response = MagicMock(status_code=200)
        response.content = value if isinstance(value, bytes) else json.dumps(value).encode()
        response.json.return_value = value
        return response


def picture(size):
    out = io.BytesIO()
    Image.new('RGB', size).save(out, 'PNG')
    return out.getvalue()


class SolverTests(unittest.TestCase):
    def pipeline(self, final):
        return SolverTransport([
            {'code': '200', 'data': 'c'},
            {'success': True, 'result': 1, 'sid': 'test-sid', 'y': 66,
             'o': list(range(32)), 'p1': '/api/p1?sid=test', 'p2': '/api/p2?sid=test'},
            picture((400, 200)), picture((67, 67)), final])

    def test_fresh_proof_and_temp_cleanup(self):
        transport = self.pipeline({'success': True, 'token': PROOF})
        with tempfile.TemporaryDirectory() as parent, patch('tempfile.tempdir', parent), \
             patch('app.vendor.longfor_captcha.image_recognition_v2.find_gap_position', return_value=(202, 66)):
            self.assertEqual(SliderSolver(transport, sleeper=lambda _: None).solve(), PROOF)
            from pathlib import Path
            self.assertEqual(list(Path(parent).iterdir()), [])
        self.assertEqual(len(transport.calls), 5)
        self.assertEqual(sum(m == 'POST' for m, _, _ in transport.calls), 1)
        self.assertTrue(all(kw['allow_redirects'] is False for _, _, kw in transport.calls))
        self.assertTrue(all(kw['timeout'] == (10, 20) for _, _, kw in transport.calls))

    def test_secondary_challenge_not_marked_success(self):
        transport = self.pipeline({'success': False, 'token': PROOF, 'msg': '二次验证'})
        with patch('app.vendor.longfor_captcha.image_recognition_v2.find_gap_position', return_value=(202, 66)):
            with self.assertRaises(CaptchaError):
                SliderSolver(transport, sleeper=lambda _: None).solve()
        self.assertEqual(sum(m == 'POST' for m, _, _ in transport.calls), 1)

    def test_other_hosts_rejected_before_network(self):
        transport = MagicMock()
        with self.assertRaises(CaptchaError):
            SliderSolver(transport).request('GET', 'https://evil.example/api/a')
        transport.request.assert_not_called()

    def test_proof_header_validation(self):
        for value in [None, '', '\r\nHeader: x', 'x' * 8193]:
            self.assertFalse(valid_proof(value))

    def test_fixed_trajectory_contract(self):
        for distance in [0, 1, 50, 203, 249]:
            for seed in range(100):
                random.seed(seed)
                points = generate_realistic_trajectory(distance)
                self.assertEqual(points[-1][:2], (distance, 0))
                self.assertTrue(all(b[2] > a[2] for a, b in zip(points, points[1:])))
                self.assertTrue(ACEncoderFull('test').generate(points, distance, 66).startswith('s_v3#'))
