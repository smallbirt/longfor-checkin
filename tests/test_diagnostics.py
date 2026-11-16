"""Diagnostic metadata must explain failures without exposing HTTP secrets."""
import json
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock
import requests
from app.client import LongforClient
from app.runner import Runner
from app.store import Store
from app.constants import TZ
from test_core import credential, page, DAY

PRIVATE = 'never-log-http-token-cookie-phone-proof'
PROOF = 'fresh-private-captcha-proof'


def response(body=None, status=200, content=b'{}', parse_error=None):
    result = MagicMock(status_code=status, content=content)
    if parse_error:
        result.json.side_effect = parse_error
    else:
        result.json.return_value = body
    return result


class Transport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class DiagnosticTests(unittest.TestCase):
    def check_private(self, result, logs):
        data = json.dumps(result) + '\n'.join(logs.output)
        for value in (PRIVATE, PROOF, 'private-login-token', 'private-device-token'):
            self.assertNotIn(value, data)

    def test_business_rejection_survives_unsigned_readback_and_logs_no_response(self):
        t = Transport([response(page()), response({'code': '901234', 'msg': PRIVATE, 'data': {'token': PRIVATE}}), response(page())])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t).run(credential('mini'), DAY)
        self.assertEqual(result['state'], 'unknown')
        self.assertIn('901234', result['message'])
        self.assertEqual(result['diagnostics'], [{'stage': 'submit', 'reason': 'business', 'http_status': 200, 'business_code': '901234'}])
        self.assertIn('"channel": "mini"', logs.output[0])
        self.assertIn('"operation": "submit"', logs.output[0])
        self.assertFalse(result['captcha_attempted'])
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)
        self.check_private(result, logs)

    def test_http_error_reports_status_without_printing_body(self):
        t = Transport([response(page()), response(status=403, content=PRIVATE.encode()), response(page())])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t).run(credential('mini'), DAY)
        self.assertIn('HTTP 403', result['message'])
        self.assertEqual(result['diagnostics'][0]['reason'], 'http')
        self.assertEqual(result['diagnostics'][0]['http_status'], 403)
        self.check_private(result, logs)

    def test_timeout_readback_unsigned_does_not_resubmit_or_log_exception_text(self):
        t = Transport([response(page()), requests.Timeout(PRIVATE), response(page())])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t).run(credential('mini'), DAY)
        self.assertEqual(result['diagnostics'], [{'stage': 'submit', 'reason': 'timeout'}])
        self.assertIn('超时', result['message'])
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)
        self.check_private(result, logs)

    def test_exception_and_bad_response_categories_have_allowlisted_metadata(self):
        cases = [(requests.exceptions.SSLError(PRIVATE), 'tls'),
            (requests.ConnectionError(PRIVATE), 'connection'),
            (requests.RequestException(PRIVATE), 'network'),
            (response(parse_error=ValueError(PRIVATE)), 'invalid_response'),
            (response(body=[PRIVATE]), 'invalid_response'),
            (response(body={'code': '0000', 'data': PRIVATE}), 'invalid_response'),
            (response(content=b'x' * (1024 * 1024 + 1)), 'response_too_large')]
        for reply, expected in cases:
            with self.subTest(reason=expected), self.assertLogs('app.client', level='WARNING') as logs:
                result = LongforClient(Transport([reply])).run(credential('mini'), DAY, allow_sign=False)
                self.assertEqual(result['diagnostics'][0]['reason'], expected)
                self.assertEqual(result['diagnostics'][0]['stage'], 'initial_status')
                self.check_private(result, logs)

    def test_unknown_or_oversized_business_code_is_not_echoed(self):
        for code in (PRIVATE, '13800000000', '9' * 1024):
            with self.subTest(length=len(code)), self.assertLogs('app.client', level='WARNING') as logs:
                result = LongforClient(Transport([response({'code': code, 'msg': PRIVATE})])).run(credential('mini'), DAY)
                self.assertNotIn('business_code', result['diagnostics'][0])
                self.assertNotIn(code, json.dumps(result) + '\n'.join(logs.output))

    def test_submit_failure_and_readback_failure_are_both_preserved(self):
        t = Transport([response(page()), response(status=503), requests.Timeout(PRIVATE)])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t).run(credential('mini'), DAY)
        self.assertEqual([d['stage'] for d in result['diagnostics']], ['submit', 'verify_status'])
        self.assertEqual([d['reason'] for d in result['diagnostics']], ['http', 'timeout'])
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)
        self.check_private(result, logs)

    def test_failure_after_captcha_has_distinct_stage_and_no_proof(self):
        t = Transport([response(page()), response({'code': '8040012', 'msg': PRIVATE}),
                       response(page()), response(status=403), response(page())])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t, lambda: PROOF).run(credential('mini'), DAY)
        self.assertTrue(result['captcha_solved'])
        self.assertEqual(result['diagnostics'][-1]['stage'], 'submit_after_captcha')
        self.assertEqual(result['diagnostics'][-1]['http_status'], 403)
        self.assertIn('"captcha_proof_used": true', logs.output[-1])
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 2)
        self.check_private(result, logs)

    def test_timeout_with_confirmed_success_still_reports_signed(self):
        t = Transport([response(page()), requests.Timeout(PRIVATE), response(page(20))])
        with self.assertLogs('app.client', level='WARNING') as logs:
            result = LongforClient(t).run(credential('mini'), DAY)
        self.assertEqual(result['state'], 'signed')
        self.assertEqual(result['diagnostics'][0]['reason'], 'timeout')
        self.assertEqual(sum(url.endswith('/clock') for url, _ in t.calls), 1)
        self.check_private(result, logs)

    def test_error_metadata_saved_to_job_and_safe_detail_in_email(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder, database_url='')
            store.put('credential:mini', credential('mini'))
            client = LongforClient(Transport([response(page()), response({'code': '901234', 'msg': PRIVATE}), response(page())]))
            with self.assertLogs('app.client', level='WARNING') as logs:
                Runner(store, client).run('manual', target='mini', dt=datetime(2026, 10, 16, 16, tzinfo=TZ))
            job = store.jobs(limit=1)[0]
            self.assertEqual(job['results'][0]['diagnostics'][0]['business_code'], '901234')
            with store.connect() as db:
                mail = db.execute('SELECT body FROM outbox WHERE job_id=?', (job['id'],)).fetchone()['body']
            self.assertIn('901234', mail)
            self.assertNotIn(PRIVATE, mail)
            self.check_private(job['results'], logs)
