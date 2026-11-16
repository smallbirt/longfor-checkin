"""The native mini-program uses the existing site's session and workspace."""
import tempfile
import unittest
from unittest.mock import MagicMock
from app import create_app


class NativeSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, password='native-test-admin-password', client=MagicMock())
        self.client = self.app.test_client()
        self.accounts = self.app.extensions['accounts']
        self.accounts.register('alice', 'native-alice-password')

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, payload):
        csrf = self.client.get('/api/session').json['csrf']
        return self.client.post(path, json=payload, headers={'X-CSRF-Token': csrf})

    def login(self):
        return self.post('/api/session/login', {'username': 'alice', 'password': 'native-alice-password'})

    def test_bootstrap_and_csrf_cannot_be_skipped(self):
        response = self.client.get('/api/session')
        self.assertIsNone(response.json['user'])
        self.assertNotIn('password', response.json)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertEqual(self.client.post('/api/session/login', json={'username': 'alice', 'password': 'native-alice-password'}).status_code, 403)
        self.assertEqual(self.client.post('/api/session/register', json={}).status_code, 403)

    def test_native_login_rotates_csrf_and_shares_web_workspace(self):
        before = self.client.get('/api/session').json['csrf']
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json['csrf'], before)
        self.assertEqual(response.json['user']['username'], 'alice')
        created = self.post('/api/accounts', {'channel': 'app', 'alias': '手机账号'}).json['account']
        self.assertEqual(self.client.get('/api/state').json['accounts'][0]['id'], created['id'])
        self.assertEqual(self.client.get('/accounts/' + created['id']).status_code, 200)
        self.assertEqual(self.client.post('/api/accounts', json={'channel': 'app', 'alias': '错误'}, headers={'X-CSRF-Token': before}).status_code, 403)

    def test_bad_login_returns_json_without_authenticated_state(self):
        response = self.post('/api/session/login', {'username': 'alice', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertIn('error', response.json)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertIsNone(self.client.get('/api/session').json['user'])

    def test_registration_uses_same_users_and_respects_closed_registration(self):
        self.app.extensions['store'].put('registration_enabled', False)
        self.assertEqual(self.post('/api/session/register', {'username': 'bob', 'password': 'native-bob-password', 'confirm_password': 'native-bob-password'}).status_code, 403)
        self.app.extensions['store'].put('registration_enabled', True)
        self.assertEqual(self.post('/api/session/register', {'username': 'bob', 'password': 'native-bob-password', 'confirm_password': 'other'}).status_code, 400)
        response = self.post('/api/session/register', {'username': 'bob', 'password': 'native-bob-password', 'confirm_password': 'native-bob-password'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['user']['username'], 'bob')
        self.assertTrue(self.accounts.authenticate('bob', 'native-bob-password'))

    def test_logout_and_password_change_revoke_access(self):
        self.login()
        self.assertEqual(self.post('/api/session/logout', {}).status_code, 200)
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.login()
        user = self.accounts.authenticate('alice', 'native-alice-password')
        self.accounts.change_password(user['id'], 'new-native-alice-password')
        self.assertIsNone(self.client.get('/api/session').json['user'])
        self.assertEqual(self.client.get('/api/state').status_code, 401)

    def test_native_and_web_logins_share_rate_limit(self):
        self.app.extensions['store'].put('registration_enabled', False)
        for _ in range(30):
            self.assertEqual(self.post('/api/session/login', {'username': 'alice', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        csrf = self.client.get('/api/session').json['csrf']
        self.assertEqual(self.client.post('/login', data={'username': 'alice', 'password': 'native-alice-password', 'csrf': csrf}).status_code, 429)

    def test_foreign_account_id_cannot_switch_native_identity(self):
        bob = self.accounts.register('bob', 'native-bob-password')
        from app.program_accounts import ProgramAccounts
        store, _ = self.app.extensions['workspaces'].get(bob['id'])
        account = ProgramAccounts(store).create('app', '属于 Bob')
        self.login()
        self.assertEqual(self.client.get('/api/state?account_id=' + account['id']).status_code, 400)
        self.assertEqual(self.post('/api/accounts/' + account['id'], {'alias': '越权'}).status_code, 400)
        self.assertEqual(self.post('/api/run', {'account_id': account['id'], 'kind': 'manual'}).status_code, 400)

    def test_empty_native_username_cannot_fall_back_to_admin(self):
        self.assertEqual(self.post('/api/session/login', {'password': 'native-test-admin-password'}).status_code, 401)

    def test_real_local_mode_is_distinct_from_simulated_preview(self):
        self.app.config['LOCAL_DEBUG'] = True
        response = self.client.get('/api/session').json
        self.assertTrue(response['local_debug'])
        self.assertFalse(response['preview'])
        self.login()
        self.assertTrue(self.client.get('/api/state').json['local_debug'])
        self.assertIn('本地真实接口', self.client.get('/').text)
        self.assertIsNone(self.app.extensions['workspaces'].thread)


if __name__ == '__main__':
    unittest.main()
