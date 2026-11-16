import json
import tempfile
import threading
import unittest
import uuid
from unittest.mock import MagicMock, patch
from app import create_app
from app.entitlements import Wallet
from app.migrate import snapshot
from app.program_accounts import ProgramAccounts
from app.store import Store
from test_core import credential

ADMIN = 'admin-user-management-password'
PASSWORD = 'own-test-password-2026'


class UserManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.network = MagicMock()
        self.network.run.return_value = {'state': 'signed', 'message': '已签到'}
        self.app = create_app(self.tmp.name, password=ADMIN, client=self.network)
        self.root = self.app.extensions['store']
        self.accounts = self.app.extensions['accounts']
        self.spaces = self.app.extensions['workspaces']
        self.alice = self.accounts.register('alice', PASSWORD)
        self.bob = self.accounts.register('bob', PASSWORD)
        self.admin = self.login('admin', ADMIN)
        self.a = self.login('alice', PASSWORD)
        self.b = self.login('bob', PASSWORD)
        self.store, self.runner = self.spaces.get(self.alice['id'])
        self.wallet = Wallet(self.store)
        self.registry = ProgramAccounts(self.store)
        self.root.put('mail', {'provider': 'qq', 'sender': 'sender@qq.com', 'password': 'private-smtp'})
        self.store.put('notifications', {'recipient': 'alice@qq.com', 'verified': True})

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, name, password):
        client = self.app.test_client()
        client.get('/login')
        with client.session_transaction() as s:
            csrf = s['csrf']
        r = client.post('/login', data={'username': name, 'password': password, 'csrf': csrf})
        self.assertEqual(r.status_code, 302)
        return client

    def post(self, path, payload=None, client=None):
        client = client or self.admin
        with client.session_transaction() as s:
            csrf = s['csrf']
        return client.post('/api/' + path, json=payload or {}, headers={'X-CSRF-Token': csrf})

    def grant(self, value=30, ids=None, key=None):
        return self.post('admin/users/credits', {'user_ids': ids or [self.alice['id']], 'kind': 'grant',
                         'value': value, 'reason': '测试积分发放', 'request_id': key or uuid.uuid4().hex})

    def account(self, name, enabled=False):
        a = self.registry.create('app', name)
        self.registry.scoped(a['id']).put('credential:app', credential())
        if enabled:
            self.registry.update(a['id'], {'enabled': True})
        return a

    def test_admin_only_and_pc_navigation(self):
        for path in ('/admin/users', '/api/admin/users'):
            self.assertEqual(self.a.get(path).status_code, 403)
            self.assertEqual(self.admin.get(path).status_code, 200)
        self.assertEqual(self.post('admin/users/credits', {}, self.a).status_code, 403)
        self.assertIn(b'/admin/users', self.admin.get('/admin').data)
        self.assertNotIn(b'/admin/users', self.a.get('/').data)
        self.assertEqual(self.a.get('/credits').status_code, 200)
        self.assertNotIn(b'/static/app.js', self.a.get('/credits').data)
        self.assertEqual(self.admin.post('/api/admin/users/credits', json={}).status_code, 403)

    def test_tenant_wallets_and_sensitive_data_are_isolated(self):
        self.grant()
        self.account('私有账号')
        self.assertEqual(self.a.get('/api/credits?user_id=' + str(self.bob['id'])).json['wallet']['points'], 30)
        self.assertEqual(self.b.get('/api/credits?user_id=' + str(self.alice['id'])).json['wallet']['points'], 0)
        output = self.admin.get('/api/admin/users').get_data(as_text=True)
        for secret in ('private-login-token', 'private-smtp', PASSWORD, 'password_hash', 'alice@qq.com'):
            self.assertNotIn(secret, output)
        self.assertIn('al***@qq.com', output)

    def test_grant_retries_and_forged_operation_keys(self):
        key = uuid.uuid4().hex
        self.assertEqual(self.grant(key=key).status_code, 200)
        self.assertEqual(self.grant(key=key).status_code, 200)
        self.assertEqual(self.wallet.public()['points'], 30)
        self.assertEqual(len(self.wallet.ledger()), 1)
        self.assertEqual(self.grant(value=40, key=key).status_code, 400)
        self.assertEqual(self.wallet.public()['points'], 30)
        for value in (-1, True, 0, 2.5, '10'):
            self.assertEqual(self.grant(value=value).status_code, 400)

    def test_insufficient_balance_does_not_create_purchase_or_quota(self):
        self.grant(5)
        response = self.post('credits/purchase', {'price': 10, 'request_id': uuid.uuid4().hex}, self.a)
        self.assertEqual(response.status_code, 400)
        self.assertIn('5', response.json['error'])
        self.assertEqual(self.wallet.public()['points'], 5)
        self.assertEqual(self.wallet.public()['purchased'], 0)
        self.assertEqual(len(self.wallet.ledger()), 1)

    def test_purchase_is_atomic_idempotent_and_survives_restart(self):
        self.grant(30)
        payload = {'price': 10, 'request_id': uuid.uuid4().hex}
        for _ in range(3):
            self.assertEqual(self.post('credits/purchase', payload, self.a).status_code, 200)
        self.assertEqual(self.wallet.public(), {'points': 20, 'free': 2, 'purchased': 1, 'used': 0, 'total': 3, 'price': 10})
        other = Store(self.store.root, database_url='', user_id=self.alice['id'])
        self.assertEqual(Wallet(other).public()['points'], 20)
        self.assertEqual(len(Wallet(other).ledger()), 2)
        self.assertEqual(sum(r['delta'] for r in self.wallet.ledger()), 20)
        self.assertEqual(snapshot(self.tmp.name)[0][self.alice['id']][1]['user_wallet'][0]['balance'], 20)

    def test_credit_transaction_rolls_back_if_ledger_write_fails(self):
        self.grant(10)
        from app.database import Session
        original = Session.execute
        def execute(db, sql, params=()):
            if sql.startswith('INSERT INTO credit_ledger'):
                raise RuntimeError('simulated disk failure')
            return original(db, sql, params)
        with patch.object(Session, 'execute', execute), self.assertRaises(RuntimeError):
            self.wallet.change('purchase', 10, '购买 1 个自动打卡名额', 'alice', uuid.uuid4().hex)
        self.assertEqual(self.wallet.public()['points'], 10)
        self.assertEqual(self.wallet.public()['purchased'], 0)

    def test_concurrent_purchases_never_overdraw(self):
        self.grant(10)
        stores = [self.store, Store(self.store.root, database_url='', user_id=self.alice['id'])]
        results = []
        barrier = threading.Barrier(2)
        def buy(i):
            barrier.wait()
            try:
                Wallet(stores[i]).change('purchase', 10, '购买名额', 'alice', uuid.uuid4().hex)
                results.append('ok')
            except ValueError:
                results.append('insufficient')
        threads = [threading.Thread(target=buy, args=(i,)) for i in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(10)
        self.assertCountEqual(results, ['ok', 'insufficient'])
        self.assertEqual(self.wallet.public()['points'], 0)
        self.assertEqual(self.wallet.public()['purchased'], 1)

    def test_quota_enforced_on_all_activation_paths_and_reused_without_charge(self):
        a, b, c = self.account('一号', True), self.account('二号', True), self.account('三号')
        self.assertEqual(self.post('accounts/' + c['id'], {'enabled': True}, self.a).status_code, 400)
        self.registry.update(a['id'], {'enabled': False})
        self.assertEqual(self.post('accounts/' + c['id'], {'enabled': True}, self.a).status_code, 200)
        self.assertEqual(self.wallet.public()['used'], 2)
        self.assertEqual(self.wallet.public()['points'], 0)
        self.assertEqual(self.wallet.ledger(), [])
        self.registry.archive(c['id'])
        self.registry.update(a['id'], {'enabled': True})
        self.assertEqual(self.wallet.public()['used'], 2)

    def test_concurrent_plan_enables_share_one_remaining_slot(self):
        self.account('已有计划', True)
        targets = [self.account('计划一'), self.account('计划二')]
        other = Store(self.store.root, database_url='', user_id=self.alice['id'])
        stores = [self.store, other]
        errors = []
        barrier = threading.Barrier(2)
        def enable(i):
            barrier.wait()
            try: ProgramAccounts(stores[i]).update(targets[i]['id'], {'enabled': True})
            except ValueError: errors.append('full')
        threads = [threading.Thread(target=enable, args=(i,)) for i in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(10)
        self.assertEqual(errors, ['full'])
        self.assertEqual(self.wallet.public()['used'], 2)

    def test_legacy_schedule_switch_never_partially_enables_over_quota(self):
        for i in range(3): self.account(str(i))
        self.assertEqual(self.post('schedule', {'enabled': True}, self.a).status_code, 400)
        self.assertFalse(any(a['enabled'] for a in self.registry.list()))
        self.assertFalse(self.store.get('schedule_enabled', False))

    def test_rule_changes_do_not_rewrite_existing_free_slots_or_completed_purchases(self):
        self.grant(30)
        self.assertEqual(self.wallet.public()['free'], 2)
        key = uuid.uuid4().hex
        self.post('credits/purchase', {'price': 10, 'request_id': key}, self.a)
        self.assertEqual(self.post('admin/entitlement-rules', {'free': 3, 'price': 15}).status_code, 200)
        self.assertEqual(self.wallet.public()['free'], 2)
        self.assertEqual(self.post('credits/purchase', {'price': 10, 'request_id': key}, self.a).status_code, 200)
        self.assertEqual(self.post('credits/purchase', {'price': 10, 'request_id': uuid.uuid4().hex}, self.a).status_code, 409)
        later = self.accounts.register('later_user', PASSWORD)
        self.assertEqual(Wallet(self.spaces.get(later['id'])[0]).public()['free'], 3)
        # Bob has never opened his wallet, but was registered under the old default.
        self.assertEqual(Wallet(self.spaces.get(self.bob['id'])[0]).public()['free'], 2)

    def test_admin_cannot_reduce_quota_below_used_or_overdraw_points(self):
        for i in range(2): self.account(str(i), True)
        for kind, value in (('quota', 1), ('deduct', 10)):
            r = self.post('admin/users/credits', {'user_ids': [self.alice['id']], 'kind': kind, 'value': value,
                          'reason': '边界验证', 'request_id': uuid.uuid4().hex})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.wallet.public()['free'], 2)
        self.assertEqual(self.wallet.ledger(), [])

    def test_stop_invalidates_sessions_and_pauses_worker_without_erasing_plans(self):
        self.account('保留计划', True)
        self.store.put('schedule_enabled', True)
        self.grant(20)
        self.assertEqual(self.post('admin/users/%s/access' % self.alice['id'], {'action': 'disable', 'reason': '暂停使用'}).status_code, 200)
        self.assertEqual(self.a.get('/api/state').status_code, 401)
        self.assertIsNone(self.accounts.authenticate('alice', PASSWORD))
        self.assertIsNone(self.a.get('/api/session').json['user'])
        with patch.object(self.runner, 'tick') as tick, patch.object(self.runner, 'deliver') as deliver:
            self.spaces.tick()
            tick.assert_not_called(); deliver.assert_not_called()
        self.assertIsNone(self.runner.run('manual'))
        self.assertTrue(self.registry.list()[0]['enabled'])
        self.assertEqual(self.wallet.public()['points'], 20)
        self.assertEqual(self.post('admin/users/%s/access' % self.alice['id'], {'action': 'restore', 'reason': '恢复使用'}).status_code, 200)
        self.assertTrue(self.accounts.authenticate('alice', PASSWORD))
        self.assertEqual(self.a.get('/api/state').status_code, 401)
        self.assertEqual(self.b.get('/api/state').status_code, 200)

    def test_cannot_disable_running_user_or_administrator(self):
        self.runner.lock.acquire()
        try:
            r = self.post('admin/users/%s/access' % self.alice['id'], {'action': 'disable', 'reason': '测试'})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(self.post('credits/purchase', {'price': 10, 'request_id': uuid.uuid4().hex}, self.a).status_code, 409)
        finally:
            self.runner.lock.release()
        self.assertEqual(self.accounts.get(self.alice['id'])['status'], 'active')
        self.assertEqual(self.post('admin/users/1/access', {'action': 'disable', 'reason': '测试'}).status_code, 400)
        self.assertEqual(self.post('admin/users/credits', {'user_ids': [1], 'kind': 'grant', 'value': 1, 'reason': '测试', 'request_id': uuid.uuid4().hex}).status_code, 400)

    def test_batch_grants_are_retry_safe_and_report_partial_failures(self):
        key = uuid.uuid4().hex
        ids = [self.alice['id'], self.bob['id']]
        original = Wallet.change
        def change(wallet, *args):
            if wallet.store.user_id == self.bob['id']: raise OSError('private-path')
            return original(wallet, *args)
        with patch.object(Wallet, 'change', change):
            r = self.grant(ids=ids, key=key)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['completed'], [self.alice['id']])
        self.assertEqual(r.json['errors'][0]['id'], self.bob['id'])
        self.assertNotIn('private-path', r.get_data(as_text=True))
        self.assertEqual(self.grant(ids=ids, key=key).json['errors'], [])
        for uid in ids:
            self.assertEqual(Wallet(self.spaces.get(uid)[0]).public()['points'], 30)

    def test_create_activation_and_password_reset_one_time_expiry(self):
        r = self.post('admin/users', {'username': 'new_user', 'name': '新用户'})
        self.assertEqual(r.status_code, 201)
        token = r.json['reset_url'].split('#')[1]
        self.assertNotIn(token.encode(), self.root.path.read_bytes())
        visitor = self.app.test_client(); visitor.get('/reset-password')
        payload = {'token': token, 'password': 'new-user-password', 'confirm_password': 'new-user-password'}
        self.assertEqual(self.post('reset-password', payload, visitor).status_code, 200)
        self.assertTrue(self.accounts.authenticate('new_user', 'new-user-password'))
        self.assertEqual(self.post('reset-password', payload, visitor).status_code, 400)
        link = self.accounts.issue_reset(self.alice['id'])
        with self.root.connect() as db: db.execute('UPDATE user_resets SET expires=0')
        payload['token'] = link.split('#')[1]
        self.assertEqual(self.post('reset-password', payload, visitor).status_code, 400)
        self.assertEqual(self.a.get('/api/state').status_code, 401)

    def test_reset_revocation_and_notes_survive_restart_without_plaintext(self):
        link = self.accounts.issue_reset(self.alice['id'])
        self.accounts.revoke(self.alice['id'])
        with self.assertRaises(ValueError): self.accounts.reset_password(link.split('#')[1], 'replacement-password')
        text = '仅管理员可见的处理说明'
        self.assertEqual(self.post('admin/users/%s/profile' % self.alice['id'], {'note': text}).status_code, 200)
        self.assertNotIn(text.encode(), self.root.path.read_bytes())
        restarted = create_app(self.tmp.name, password=ADMIN, client=self.network)
        self.assertTrue(restarted.extensions['accounts'].authenticate('bob', PASSWORD))
        self.assertEqual(self.admin.get('/api/admin/users').json['users'][0]['note'], text)

    def test_existing_enabled_plans_are_grandfathered(self):
        items = []
        for i in range(4):
            a = self.account('旧计划%s' % i)
            items.append({**a, 'enabled': True})
        self.store.put('program_accounts_v1', items)
        self.assertEqual(self.wallet.public()['free'], 4)
        self.assertEqual(self.wallet.public()['points'], 0)
        self.assertEqual(self.wallet.ledger(), [])

    def test_audit_failure_rolls_back_account_state_and_notes(self):
        before = self.accounts.get(self.alice['id'])
        with patch.object(self.accounts, 'record_event', side_effect=RuntimeError('audit unavailable')):
            r = self.post('admin/users/%s/access' % self.alice['id'], {'action': 'disable', 'reason': '验证原子提交'})
            self.assertEqual(r.status_code, 500)
            r = self.post('admin/users/%s/profile' % self.alice['id'], {'note': '不应保存'})
            self.assertEqual(r.status_code, 500)
        after = self.accounts.get(self.alice['id'])
        self.assertEqual(after['status'], 'active')
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['note'], before['note'])

    def test_capacity_limit_does_not_charge_and_price_cannot_be_forged(self):
        self.grant(30)
        self.assertEqual(self.post('credits/purchase', {'price': 1, 'request_id': uuid.uuid4().hex}, self.a).status_code, 409)
        self.wallet.change('quota', 30, '最大容量', 'admin', uuid.uuid4().hex)
        r = self.post('credits/purchase', {'price': 10, 'request_id': uuid.uuid4().hex}, self.a)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.wallet.public()['points'], 30)
        self.assertEqual(self.wallet.public()['purchased'], 0)

    def test_disabled_user_cannot_deliver_pending_notifications(self):
        self.account('自动计划', True)
        self.runner.run('morning')
        self.runner.sender = MagicMock(return_value=(True, ''))
        self.assertEqual(self.store.jobs()[0]['mail_state'], 'pending')
        self.post('admin/users/%s/access' % self.alice['id'], {'action': 'disable', 'reason': '暂停用户'})
        self.runner.deliver()
        self.runner.sender.assert_not_called()
        self.assertEqual(self.store.jobs()[0]['mail_state'], 'pending')


if __name__ == '__main__':
    unittest.main()
