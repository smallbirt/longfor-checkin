import json
import tempfile
import unittest
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch
import requests
from app import create_app
from app.mini_push import MiniPush, WechatPushClient, validate_config, PLATFORM, BINDING
from app.migrate import snapshot
from app.program_accounts import ProgramAccounts
from app.runner import Runner
from app.store import Store, now
from test_core import credential

FORM = {'appid': 'wx5442ffd1e656a143', 'secret': 'private-app-secret-123456789',
        'template_id': 'chosen-template-id-123456789', 'fields': {'account': 'thing1', 'result': 'phrase2', 'time': 'time3'},
        'environment': 'developer', 'enabled': True}
OPENID = 'private-openid-123456789'


class PushClientTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_config(FORM)
        self.client = WechatPushClient()
        self.client.request = MagicMock()

    def test_config_validates_and_preserves_only_same_app_secret(self):
        self.assertEqual(validate_config({**FORM, 'secret': ''}, self.config)['secret'], FORM['secret'])
        for patch_value in [{'appid': 'other'}, {'template_id': ''}, {'secret': ''}, {'enabled': 'yes'},
                {'fields': {'account': 'thing1', 'result': 'thing1', 'time': 'time3'}}, {'environment': 'release'},
                {'scenario': 'other'}, {'url': 'https://evil.test'}]:
            with self.subTest(patch=patch_value), self.assertRaises(ValueError):
                validate_config({**FORM, **patch_value})
        with self.assertRaises(ValueError):
            validate_config({**FORM, 'appid': 'wx1111111111111111', 'secret': ''}, self.config)

    def test_disabled_template_draft_needs_secret_before_enabling(self):
        draft = validate_config({**FORM, 'enabled': False, 'secret': '', 'scenario': 'failure'})
        self.assertEqual(draft['secret'], '')
        with self.assertRaises(ValueError):
            validate_config({**FORM, 'secret': '', 'enabled': True}, draft)
        with self.assertRaises(ValueError):
            validate_config({**FORM, 'secret': 'bad', 'enabled': False})

    def test_code_is_exchanged_server_side_and_session_key_is_not_returned(self):
        self.client.request.return_value = {'openid': OPENID, 'session_key': 'must-not-save'}
        self.assertEqual(self.client.exchange(self.config, 'fresh-code'), OPENID)
        args, kwargs = self.client.request.call_args
        self.assertEqual(args, ('GET', 'sns/jscode2session'))
        self.assertEqual(kwargs['params']['js_code'], 'fresh-code')
        for value in [{'errcode': 40029}, {}, {'openid': OPENID, 'errcode': 40163}]:
            self.client.request.return_value = value
            with self.assertRaises(ValueError): self.client.exchange(self.config, 'fresh-code')

    def test_token_is_cached_and_expired_rejection_refreshes_once(self):
        self.client.request.side_effect = [{'access_token': 'token1', 'expires_in': 7200}, {'errcode': 0}, {'errcode': 0}]
        for _ in range(2): self.assertEqual(self.client.send(self.config, {'openid': OPENID}, {}), ('sent', ''))
        self.assertEqual(self.client.request.call_count, 3)
        self.client.request.reset_mock()
        self.client.request.side_effect = [{'errcode': 42001}, {'access_token': 'token2', 'expires_in': 7200}, {'errcode': 0}]
        self.assertEqual(self.client.send(self.config, {'openid': OPENID}, {}), ('sent', ''))
        self.assertEqual(self.client.request.call_count, 3)

    def test_uncertain_send_is_never_blindly_retried(self):
        self.client.token = MagicMock(return_value='token')
        for error in (requests.ReadTimeout('secret'), requests.ConnectionError('secret'), ValueError('secret')):
            self.client.request.side_effect = error
            state, message = self.client.send(self.config, {'openid': OPENID}, {})
            self.assertEqual(state, 'uncertain'); self.assertNotIn('secret', message)
        self.client.request.side_effect = requests.ConnectTimeout('secret')
        self.assertEqual(self.client.send(self.config, {'openid': OPENID}, {})[0], 'pending')

    def test_business_errors_and_malformed_response(self):
        self.client.token = MagicMock(return_value='token')
        for code, expected in [(43101, 'failed'), (47003, 'failed'), (45009, 'pending'), (-1, 'pending'), (None, 'uncertain'), (False, 'uncertain')]:
            self.client.request.return_value = {'errcode': code, 'errmsg': FORM['secret']}
            state, error = self.client.send(self.config, {'openid': OPENID}, {})
            self.assertEqual(state, expected); self.assertNotIn(FORM['secret'], error)

    def test_fixed_https_endpoint_no_redirect_and_response_limit(self):
        with patch('app.mini_push.requests.Session') as factory:
            transport = factory.return_value.__enter__.return_value
            response = transport.request.return_value
            response.status_code = 200; response.content = b'{}'; response.json.return_value = {'errcode': 0}
            self.assertEqual(WechatPushClient.request('POST', 'cgi-bin/message/subscribe/send', json={}), {'errcode': 0})
            self.assertEqual(transport.request.call_args.args[1], 'https://api.weixin.qq.com/cgi-bin/message/subscribe/send')
            self.assertFalse(transport.request.call_args.kwargs['allow_redirects'])
            response.status_code = 302
            with self.assertRaises(ValueError): WechatPushClient.request('GET', 'sns/jscode2session')


class PushQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, database_url='')
        self.store.put(PLATFORM, validate_config(FORM))
        self.client = MagicMock(); self.client.exchange.return_value = OPENID; self.client.send.return_value = ('sent', '')
        self.push = MiniPush(self.store, self.client)
        self.registry = ProgramAccounts(self.store)
        self.account = self.registry.create('app', '主账号')
        self.other = self.registry.create('app', '另一账号')
        for a in (self.account, self.other): self.registry.scoped(a['id']).put('credential:app', credential())
        self.network = MagicMock(); self.network.run.return_value = {'state': 'unsigned', 'message': '尚未签到'}
        self.runner = Runner(self.store, self.network, mini_push=self.push)

    def tearDown(self): self.tmp.cleanup()

    def consent(self, account=None):
        value = self.push.prepare(self.store, (account or self.account)['id'], 'fresh-code')
        self.push.accept(self.store, value['nonce']); return value

    def run_review(self, offset=0, account=None):
        return self.runner.run('afternoon', target=(account or self.account)['id'], dt=now() + timedelta(days=offset, seconds=1))

    def test_failure_template_keeps_grant_until_abnormal_review(self):
        self.store.put(PLATFORM, validate_config({**FORM, 'scenario': 'failure',
            'template_id': 'bp6mARiocGcAyqMVwCDTz2TAGk-yUkUWttejegTulLE',
            'fields': {'account': 'thing4', 'result': 'phrase10', 'time': 'time13'}}))
        self.consent()
        self.assertEqual(self.push.public(self.store)['scenario'], 'failure')
        self.network.run.return_value = {'state': 'signed', 'message': '今日签到已确认'}
        self.run_review(); self.runner.deliver()
        self.client.send.assert_not_called()
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.network.run.return_value = {'state': 'unsigned', 'message': '尚未签到'}
        self.run_review(offset=1); self.runner.deliver()
        self.client.send.assert_called_once()
        payload = self.client.send.call_args.args[2]
        self.assertEqual(set(payload['data']), {'thing4', 'phrase10', 'time13'})
        self.assertEqual(payload['data']['phrase10']['value'], '未签到')
        self.assertEqual(self.push.public(self.store)['subscriptions'], [])

    def test_one_consent_one_future_review_no_morning_or_query_consumption(self):
        prepared = self.consent()
        self.push.accept(self.store, prepared['nonce'])
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.push.prepare(self.store, self.other['id'], 'another-code')
        self.push.accept(self.store, prepared['nonce'])
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.runner.run('morning', target=self.account['id'])
        self.runner.run('check', target=self.account['id'])
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.run_review(); self.runner.deliver(); self.runner.deliver()
        self.assertEqual(self.client.send.call_count, 1)
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'sent')
        self.assertIsNone(self.store.jobs()[0]['mail_state'])
        self.assertEqual(self.push.public(self.store)['subscriptions'], [])
        self.run_review(offset=1); self.runner.deliver(); self.assertEqual(self.client.send.call_count, 1)

    def test_success_keeps_grant_and_any_account_failure_uses_it(self):
        self.consent()
        self.network.run.return_value = {'state': 'signed', 'message': '已签到'}
        self.run_review(); self.runner.deliver()
        self.run_review(account=self.other); self.runner.deliver()
        self.client.send.assert_not_called()
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.network.run.return_value = {'state': 'auth_expired', 'message': '登录失效'}
        self.run_review(offset=1, account=self.other); self.runner.deliver()
        self.client.send.assert_called_once()
        payload = self.client.send.call_args.args[2]
        self.assertIn(self.other['alias'], payload['data']['thing1']['value'])
        self.assertEqual(self.push.public(self.store)['subscriptions'], [])

    def test_subscription_does_not_apply_to_jobs_started_before_consent(self):
        self.consent()
        self.runner.run('afternoon', target=self.account['id'], dt=now() - timedelta(days=1))
        self.runner.deliver(); self.client.send.assert_not_called()
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.run_review(); self.runner.deliver(); self.client.send.assert_called_once()

    def test_combined_job_with_multiple_failures_sends_once_and_duplicate_enqueue_keeps_next_grant(self):
        self.consent(); self.consent()
        self.network.run.side_effect = [{'state': 'signed', 'message': '已签到'}, {'state': 'request_error', 'message': '执行失败'}]
        dt = now() + timedelta(seconds=1)
        jid = self.runner.run('afternoon', dt=dt)
        job = self.store.jobs()[0]
        self.assertEqual(len(job['results']), 2)
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        with self.store.connect() as db:
            self.push.enqueue(self.store, db, jid, 'afternoon', job['results'], dt)
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.runner.deliver(); self.runner.deliver(); self.client.send.assert_called_once()
        self.assertIn(self.other['alias'], self.client.send.call_args.args[2]['data']['thing1']['value'])
        self.consent()
        self.network.run.side_effect = None
        self.network.run.return_value = {'state': 'auth_expired', 'message': '登录失效'}
        self.runner.run('afternoon', dt=dt + timedelta(days=1))
        self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
        self.runner.deliver(); self.assertEqual(self.client.send.call_count, 2)

    def test_legacy_grant_covers_other_accounts_and_survives_original_account_removal(self):
        self.consent()
        with self.store.connect() as db:
            db.execute('UPDATE mini_push_subscriptions SET account_id=?', (self.account['id'],))
        self.registry.archive(self.account['id'])
        self.assertEqual(self.push.public(self.store)['subscriptions'], [{'account_id': '*', 'remaining': 1}])
        self.run_review(account=self.other); self.runner.deliver(); self.client.send.assert_called_once()

    def test_failure_preference_controls_review_without_consuming_disabled_grants(self):
        for i, state in enumerate(('unsigned', 'auth_expired', 'request_error', 'unknown', 'captcha_required')):
            with self.subTest(state=state):
                self.consent()
                self.network.run.return_value = {'state': state, 'message': state}
                self.store.put('notification_events', {'success': True, 'failure': False, 'afternoon': True})
                self.run_review(offset=2 * i); self.runner.deliver()
                self.assertEqual(self.client.send.call_count, i)
                self.assertEqual(self.push.public(self.store)['subscriptions'][0]['remaining'], 1)
                self.store.put('notification_events', {'success': False, 'failure': True, 'afternoon': False})
                self.run_review(offset=2 * i + 1); self.runner.deliver()
                self.assertEqual(self.client.send.call_count, i + 1)
                self.assertEqual(self.push.public(self.store)['subscriptions'], [])

    def test_payload_deep_link_and_secret_protection(self):
        self.consent(); jid = self.run_review(); self.runner.deliver()
        payload = self.client.send.call_args.args[2]
        self.assertIn('pages/result/index?job_id=' + str(jid), payload['page'])
        self.assertEqual(parse_qs(urlsplit(payload['page']).query)['account_id'][0], self.account['id'])
        self.assertEqual(payload['data']['phrase2']['value'], '未签到')
        self.assertEqual(payload['miniprogram_state'], 'developer')
        self.assertNotIn('touser', payload)
        raw = self.store.path.read_bytes()
        self.assertNotIn(OPENID.encode(), raw); self.assertNotIn(FORM['secret'].encode(), raw)
        data, _ = snapshot(self.tmp.name)
        self.assertEqual(len(data[1][1]['mini_push_outbox']), 1)

    def test_pending_retry_and_crash_recovery_do_not_repeat_checkin(self):
        self.consent(); self.run_review(); self.client.send.return_value = ('pending', 'temporary')
        self.runner.deliver()
        with self.store.connect() as db: db.execute('UPDATE mini_push_outbox SET due=0')
        self.client.send.return_value = ('sent', '')
        Runner(self.store, self.network, mini_push=self.push).deliver()
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'sent')
        self.assertEqual(self.network.run.call_count, 1)
        with self.store.connect() as db: db.execute("UPDATE mini_push_outbox SET state='sending'")
        self.runner.recover(); self.runner.deliver()
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'uncertain')
        self.assertEqual(self.client.send.call_count, 2)

    def test_timeout_is_not_retried_and_archiving_an_account_keeps_user_notification(self):
        self.consent(); self.run_review(); self.client.send.return_value = ('uncertain', 'unknown')
        self.runner.deliver(); self.runner.deliver(); self.assertEqual(self.client.send.call_count, 1)
        self.consent(); self.run_review(offset=1)
        self.client.send.return_value = ('sent', '')
        self.registry.archive(self.account['id']); self.runner.deliver()
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'sent')
        self.assertEqual(self.client.send.call_count, 2)

    def test_configuration_change_cancels_pending_and_email_failure_does_not_block_push(self):
        self.consent(); self.store.put('mail', {'provider': 'qq'})
        self.runner.sender = MagicMock(side_effect=RuntimeError('private'))
        self.run_review(); self.runner.deliver()
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'sent')
        self.consent(); self.run_review(offset=1)
        self.store.put(PLATFORM, validate_config(FORM)); self.runner.deliver()
        self.assertEqual(self.store.jobs()[0]['mini_push_state'], 'cancelled')

    def test_old_wecom_queue_is_cancelled_on_upgrade(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO wecom_outbox(job_id,destination,subject,body) VALUES (1,'old','old','old')")
        Store(self.tmp.name, database_url='')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM wecom_outbox').fetchone()['state'], 'cancelled')


class PushAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(cls.tmp.name, password='push-admin-password-2026')
        cls.root = cls.app.extensions['store']; cls.spaces = cls.app.extensions['workspaces']
        cls.users = cls.app.extensions['accounts']
        cls.alice = cls.users.register('pushalice', 'push-alice-password')
        cls.bob = cls.users.register('pushbob', 'push-bob-password')

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.users.allow = MagicMock(return_value=True)
        self.a = self.login('pushalice', 'push-alice-password'); self.b = self.login('pushbob', 'push-bob-password')
        self.admin = self.login('admin', 'push-admin-password-2026')
        self.store, self.runner = self.spaces.get(self.alice['id'])
        self.root.put(PLATFORM, validate_config(FORM)); self.store.put(BINDING, None)
        with self.root.connect() as db: db.execute('DELETE FROM mini_push_owners')
        self.push = self.spaces.push
        self.push.client = MagicMock(); self.push.client.exchange.return_value = OPENID; self.push.client.send.return_value = ('sent', '')
        self.registry = ProgramAccounts(self.store); self.account = self.registry.create('app', '本次测试账号')
        self.registry.scoped(self.account['id']).put('credential:app', credential())

    def login(self, username, password):
        client = self.app.test_client(); token = client.get('/api/session').json['csrf']
        self.assertEqual(client.post('/api/session/login', json={'username': username, 'password': password}, headers={'X-CSRF-Token': token}).status_code, 200)
        return client

    def post(self, path, payload=None, client=None):
        client = client or self.a
        with client.session_transaction() as session: csrf = session['csrf']
        return client.post('/api/' + path, json=payload or {}, headers={'X-CSRF-Token': csrf})

    def prepare(self):
        result = self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'fresh-code'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['account_id'], self.account['id'])  # Legacy tap handler checks this echo.
        self.assertEqual(result.json['scope'], 'user')
        return result.json

    def consent(self):
        prepared = self.prepare()
        response = self.post('mini-push/subscribe', {'nonce': prepared['nonce'], 'accepted': True})
        self.assertEqual(response.status_code, 200, response.json); return prepared

    def test_account_free_prepare_and_subscribe_cover_current_user_only(self):
        prepared = self.post('mini-push/prepare', {'code': 'fresh-code'})
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.json['scope'], 'user')
        self.assertEqual(prepared.json['scenario'], 'failure')
        self.assertEqual(prepared.json['account_id'], '*')
        accepted = self.post('mini-push/subscribe', {'nonce': prepared.json['nonce'], 'accepted': True})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json['subscriptions'], [{'account_id': '*', 'remaining': 1}])
        self.assertEqual(self.b.get('/api/mini-push').json['subscriptions'], [])

    def test_unconfigured_and_web_pages_do_not_fake_subscription(self):
        self.root.put(PLATFORM, None)
        self.assertFalse(self.a.get('/api/state').json['mini_push']['configured'])
        self.assertEqual(self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'code'}).status_code, 400)
        self.push.client.exchange.assert_not_called()
        self.assertIn('小程序推送提醒', self.a.get('/settings').text)
        self.assertNotIn('企业微信通知', self.a.get('/settings').text)

    def test_admin_config_secrets_are_never_exposed(self):
        self.assertEqual(self.post('admin/mini-push', FORM).status_code, 403)
        self.assertEqual(self.post('admin/mini-push', FORM, self.admin).status_code, 200)
        for client, path in [(self.admin, '/api/admin'), (self.a, '/api/state'), (self.a, '/api/mini-push')]:
            value = client.get(path).text
            self.assertNotIn(FORM['secret'], value); self.assertNotIn(OPENID, value)
        self.assertEqual(self.post('admin/mini-push', {**FORM, 'secret': ''}, self.admin).status_code, 200)
        self.assertEqual(self.root.get(PLATFORM)['secret'], FORM['secret'])

    def test_admin_can_save_template_without_secret_but_cannot_subscribe(self):
        self.root.put(PLATFORM, None)
        draft = {**FORM, 'secret': '', 'enabled': False, 'scenario': 'failure'}
        self.assertEqual(self.post('admin/mini-push', draft, self.admin).status_code, 200)
        summary = self.admin.get('/api/admin').json['mini_push']
        self.assertTrue(summary['draft']); self.assertFalse(summary['configured'])
        self.assertEqual(summary['template_id'], FORM['template_id'])
        self.assertNotIn('secret', summary)
        self.assertFalse(self.a.get('/api/mini-push').json['configured'])
        self.assertEqual(self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'code'}).status_code, 400)
        self.push.client.exchange.assert_not_called()
        self.assertEqual(self.post('admin/mini-push', {**draft, 'enabled': True}, self.admin).status_code, 400)

    def test_csrf_auth_and_old_endpoints_removed(self):
        self.assertEqual(self.a.post('/api/mini-push/subscribe', json={}).status_code, 403)
        anon = self.app.test_client(); anon.get('/api/session')
        self.assertEqual(self.post('mini-push/subscribe', {}, anon).status_code, 401)
        for path in ('wecom', 'wecom/test', 'wecom/remove', 'wecom/retry'):
            self.assertEqual(self.post(path).status_code, 404)

    def test_identity_and_account_cannot_be_supplied_by_client(self):
        self.assertEqual(self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'code', 'openid': 'forged'}).status_code, 400)
        self.assertEqual(self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'code'}, self.b).status_code, 400)
        prepared = self.prepare()
        self.assertNotIn(OPENID, json.dumps(prepared))
        self.assertEqual(self.post('mini-push/subscribe', {'nonce': prepared['nonce'], 'accepted': True}, self.b).status_code, 400)
        self.assertEqual(self.post('mini-push/subscribe', {'nonce': prepared['nonce'], 'accepted': False}).status_code, 400)

    def test_duplicate_ack_is_idempotent_and_allows_push_only_schedule(self):
        prepared = self.consent()
        self.post('mini-push/subscribe', {'nonce': prepared['nonce'], 'accepted': True})
        current = self.a.get('/api/state').json
        self.assertTrue(current['notification_configured']); self.assertFalse(current['mail_configured'])
        self.assertEqual(current['mini_push']['subscriptions'][0]['remaining'], 1)
        self.assertEqual(self.post('accounts/' + self.account['id'], {'enabled': True}).status_code, 200)

    def test_one_wechat_identity_cannot_be_bound_to_two_site_users(self):
        self.prepare()
        bobstore, _ = self.spaces.get(self.bob['id']); other = ProgramAccounts(bobstore).create('app', 'Bob账号')
        self.assertEqual(self.post('mini-push/prepare', {'account_id': other['id'], 'code': 'bob-code'}, self.b).status_code, 400)
        self.assertEqual(self.post('mini-push/unbind').status_code, 200)
        self.assertEqual(self.post('mini-push/prepare', {'account_id': other['id'], 'code': 'bob-code'}, self.b).status_code, 200)

    def test_pause_expired_intent_and_rate_limit(self):
        prepared = self.prepare(); binding = self.store.get(BINDING); binding['intent']['expires'] = 0; self.store.put(BINDING, binding)
        self.assertEqual(self.post('mini-push/subscribe', {'nonce': prepared['nonce'], 'accepted': True}).status_code, 400)
        self.consent(); self.post('mini-push/pause')
        state = self.a.get('/api/mini-push').json
        self.assertFalse(state['enabled']); self.assertEqual(state['subscriptions'], [])
        self.users.allow.return_value = False
        self.assertEqual(self.post('mini-push/prepare', {'account_id': self.account['id'], 'code': 'code'}).status_code, 400)

    def test_card_result_is_authenticated_and_receipt_is_user_scoped(self):
        self.consent()
        self.runner.injected_client = True; self.runner.client = MagicMock()
        self.runner.client.run.return_value = {'state': 'unsigned', 'message': '未签到'}
        jid = self.runner.run('afternoon', target=self.account['id'], dt=now() + timedelta(seconds=1))
        with self.store.connect() as db: payload = json.loads(db.execute('SELECT payload FROM mini_push_outbox WHERE job_id=?', (jid,)).fetchone()['payload'])
        receipt = parse_qs(urlsplit(payload['page']).query)['receipt'][0]
        self.assertEqual(self.a.get('/api/jobs/' + str(jid) + '?receipt=' + receipt).status_code, 200)
        self.assertEqual(self.b.get('/api/jobs/' + str(jid) + '?receipt=' + receipt).status_code, 404)
        self.assertEqual(self.a.get('/api/jobs/' + str(jid) + '?receipt=wrong').status_code, 404)
        self.assertEqual(self.a.get('/api/jobs/999999').status_code, 404)


if __name__ == '__main__': unittest.main()
