"""Shared device setup uses fake SMS/status only; owner auth must never cross users."""
import copy
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock
from app import create_app
from app import shared_device
from test_core import credential
from test_login import ADMIN, PROFILE, PHONE, TOKEN
from app.login_client import checkin_credential


def existing_login():
    # Existing account credential without a sign-page device/profile (legacy data).
    return checkin_credential('app', '1234567890abcdef1234567890abcdef')


def seed_owner(root):
    root.put('login-profile:app', {**PROFILE, 'token': 'owner-profile-secret', 'phone': PHONE})
    for channel in ('app', 'mini'):
        cred = credential(channel)
        cred['headers'].update({'token': 'owner-auth-secret', 'x-lf-usertoken': 'owner-auth-secret',
            'cookie': 'owner-cookie-secret', 'referer': 'https://example.test/?token=owner-ref-secret',
            'origin': 'owner-origin-secret', 'x-lf-dxrisk-captcha-token': 'owner-captcha-secret',
            'x-lf-dxrisk-token': channel + '-owner-device', 'user-agent': channel + '-device-agent'})
        root.put('credential:' + channel, cred)


class SharedDeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.network = MagicMock(); self.network.run.return_value = {'state': 'unsigned', 'message': 'confirmed'}
        self.sms = MagicMock(); self.sms.verify.return_value = TOKEN
        self.app = create_app(self.tmp.name, password=ADMIN, client=self.network, login_client=self.sms)
        self.root = self.app.extensions['store']; seed_owner(self.root)
        self.admin, self.admin_headers = self.login('admin', ADMIN)
        user = self.app.extensions['accounts'].register('alice', 'alice-password-2026')
        self.child, _ = self.app.extensions['workspaces'].get(user['id'])
        self.child.put('credential:app', existing_login())
        self.client, self.headers = self.login('alice', 'alice-password-2026')

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, password):
        c = self.app.test_client(); c.get('/login')
        with c.session_transaction() as s: csrf = s['csrf']
        c.post('/login', data={'username': username, 'password': password, 'csrf': csrf})
        with c.session_transaction() as s: headers = {'X-CSRF-Token': s['csrf']}
        return c, headers

    def enable(self):
        r = self.admin.post('/api/admin/shared-device', json={'enabled': True}, headers=self.admin_headers)
        self.assertEqual(r.status_code, 200, r.json)

    def send(self, **extra):
        return self.client.post('/api/login/sms/send', json={'phone': PHONE, 'target': 'app', 'device_mode': 'shared', **extra}, headers=self.headers)

    def verify(self):
        return self.client.post('/api/login/sms/verify', json={'code': '012345'}, headers=self.headers)

    def test_first_import_required_then_only_device_allowlist_and_own_token(self):
        before = {key: self.root.get('credential:' + key) for key in ('app', 'mini')}
        self.enable()
        template = self.root.get(shared_device.KEY)
        raw = json.dumps(template)
        for forbidden in ('owner-auth-secret', 'owner-cookie-secret', 'owner-ref-secret', 'owner-origin-secret', 'owner-captcha-secret', 'owner-profile-secret', PHONE, PROFILE['phone_hint']):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(set(template['profile']), {'fingerprint', 'user_agent', 'kind'})
        state = self.client.get('/api/state')
        self.assertTrue(state.json['login_methods']['app']['shared_available'])
        self.assertFalse(state.json['login_methods']['app']['sms_available'])
        for private in (PROFILE['fingerprint'], 'app-owner-device', 'owner-auth-secret'):
            self.assertNotIn(private.encode(), state.data)
        self.child.put('credential:app', None)
        self.assertFalse(self.client.get('/api/state').json['login_methods']['app']['effective_sms_available'])
        self.assertEqual(self.send().status_code, 400)
        self.sms.send.assert_not_called()
        self.child.put('credential:app', existing_login())
        self.assertEqual(self.send().status_code, 200)
        self.sms.send.assert_called_once_with(PHONE, template['profile'])
        r = self.verify(); self.assertEqual(r.status_code, 200, r.json)
        self.sms.verify.assert_called_once_with(PHONE, '012345', template['profile'])
        for key in ('app',):
            cred = self.child.get('credential:' + key)
            self.assertEqual(cred['headers']['token'], TOKEN)
            self.assertEqual(cred['headers']['x-lf-dxrisk-token'], key + '-owner-device')
            self.assertEqual(cred['headers']['user-agent'], key + '-device-agent')
            self.assertEqual(cred['device_kind'], 'shared_default')
            self.assertEqual(self.root.get('credential:' + key), before[key])
            for h in ('cookie', 'referer', 'origin', 'x-lf-dxrisk-captcha-token'):
                self.assertNotIn(h, cred['headers'])
        self.assertFalse(self.child.get('schedule_enabled', False))
        self.assertIsNone(self.child.get('credential:mini'))
        self.assertEqual(self.root.get('credential:mini'), before['mini'])
        for call in self.network.run.call_args_list:
            self.assertEqual(call.args[0]['headers']['token'], TOKEN)
            self.assertFalse(call.kwargs['allow_sign'])
        for private in (TOKEN, PHONE, PROFILE['fingerprint'], 'app-owner-device'):
            self.assertNotIn(private.encode(), self.child.path.read_bytes())

    def test_admin_only_csrf_and_explicit_boolean(self):
        path = '/api/admin/shared-device'
        self.assertEqual(self.client.post(path, json={'enabled': True}, headers=self.headers).status_code, 403)
        self.assertEqual(self.admin.post(path, json={'enabled': True}).status_code, 403)
        self.assertEqual(self.admin.post(path, json={'enabled': 'true'}, headers=self.admin_headers).status_code, 400)
        self.assertIsNone(self.root.get(shared_device.KEY))
        self.assertEqual(self.client.get('/api/admin').status_code, 403)

    def test_disabled_template_never_sends_or_exposes_owner_profile(self):
        self.assertEqual(self.send().status_code, 400)
        self.sms.send.assert_not_called()
        self.assertIsNone(self.child.get('login-profile:app'))

    def test_missing_device_configuration_cannot_enable_or_replace_template(self):
        self.root.put('credential:mini', None)
        r = self.admin.post('/api/admin/shared-device', json={'enabled': True}, headers=self.admin_headers)
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.root.get(shared_device.KEY))
        self.root.put('login-profile:app', {'fingerprint': 'invalid', 'user_agent': 'bad\r\nagent'})
        with self.assertRaises(ValueError): shared_device.configure(self.root, True)

    def test_disable_or_refresh_revokes_pending_otp_without_calling_verify(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.enable()
                # Use a separate user to keep real persistent SMS limits intact.
                name = 'bob' if enabled else 'carol'
                user = self.app.extensions['accounts'].register(name, 'fixture-password-2026')
                self.client, self.headers = self.login(name, 'fixture-password-2026')
                self.child, _ = self.app.extensions['workspaces'].get(user['id'])
                self.child.put('credential:app', existing_login())
                self.assertEqual(self.send().status_code, 200)
                shared_device.configure(self.root, enabled)
                self.assertEqual(self.verify().status_code, 400)
                self.assertIsNone(self.child.get('pending_sms'))
                self.assertEqual(self.child.get('credential:app'), existing_login())
        self.sms.verify.assert_not_called()

    def test_existing_personal_device_context_is_preserved(self):
        self.enable()
        for key in ('app', 'mini'): self.child.put('credential:' + key, credential(key))
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.verify().status_code, 200)
        for key in ('app', 'mini'):
            self.assertEqual(self.child.get('credential:' + key)['headers']['x-lf-dxrisk-token'], 'private-device-token')

    def test_selected_target_binding_and_shared_device_refresh(self):
        self.enable()
        old = credential('mini'); old['device_kind'] = 'shared_default'
        self.child.put('credential:mini', old)
        self.assertEqual(self.send(target='app').status_code, 200)
        r = self.client.post('/api/login/sms/verify', json={'code': '012345', 'target': 'app', 'phone': '13900000000'}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.child.get('credential:app')['headers']['token'], TOKEN)
        self.assertEqual(self.child.get('credential:mini'), old)

    def test_failed_channel_validation_keeps_all_existing_tokens(self):
        self.enable()
        old = credential(); self.child.put('credential:app', old)
        self.network.run.return_value = {'state': 'auth_expired'}
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.verify().status_code, 400)
        self.assertEqual(self.child.get('credential:app'), old)
        self.assertIsNone(self.child.get('credential:mini'))
        self.assertIsNone(self.child.get('pending_sms'))

    def test_disable_during_readback_does_not_save_new_credentials(self):
        self.enable(); self.assertEqual(self.send().status_code, 200)
        def readback(*args, **kwargs):
            shared_device.configure(self.root, False)
            return {'state': 'unsigned'}
        self.network.run.side_effect = readback
        self.assertEqual(self.verify().status_code, 400)
        self.assertEqual(self.child.get('credential:app'), existing_login())
        self.assertIsNone(self.child.get('credential:mini'))

    def test_disable_keeps_previously_saved_user_credentials(self):
        self.enable(); self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.verify().status_code, 200)
        before = copy.deepcopy(self.child.get('credential:app'))
        shared_device.configure(self.root, False)
        self.assertEqual(self.child.get('credential:app'), before)
        self.assertFalse(self.client.get('/api/state').json['login_methods']['app']['shared_available'])


class AutomaticSelectionTests(unittest.TestCase):
    setUp = SharedDeviceTests.setUp
    tearDown = SharedDeviceTests.tearDown
    login = SharedDeviceTests.login
    send = SharedDeviceTests.send
    verify = SharedDeviceTests.verify

    def publish(self):
        shared_device.sync(self.root)

    def import_owner(self, payload):
        return self.admin.post('/api/admin/shared-device/import',
            data={'file': (io.BytesIO(payload), 'device-defaults.json')}, headers=self.admin_headers)

    def test_first_start_publishes_admin_devices_without_toggle_and_never_auth(self):
        restarted = create_app(self.tmp.name, password=ADMIN, client=self.network, login_client=self.sms)
        config = restarted.extensions['store'].get(shared_device.KEY)
        self.assertTrue(config['enabled'])
        self.assertNotIn('owner-auth-secret', json.dumps(config))
        self.assertTrue(self.client.get('/api/state').json['login_methods']['app']['effective_sms_available'])
        self.assertEqual(self.client.post('/api/login/sms/send', json={'phone': PHONE, 'target': 'app'}, headers=self.headers).status_code, 200)
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.child.get('credential:app')['headers']['token'], TOKEN)
        self.assertIsNone(self.child.get('credential:mini'))

    def test_personal_sign_device_with_platform_native_profile_is_usable(self):
        self.publish()
        self.child.put('credential:app', credential())
        sms = self.client.get('/api/state').json['login_methods']['app']
        self.assertEqual(sms['profile_source'], 'platform')
        self.assertEqual(sms['device_sources'], {'app': 'personal', 'mini': 'platform'})
        self.assertEqual(self.send(device_mode='preferred').status_code, 200)
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.child.get('credential:app')['headers']['x-lf-dxrisk-token'], 'private-device-token')
        self.assertIsNone(self.child.get('credential:mini'))
        self.assertNotEqual(self.child.get('credential:app').get('device_kind'), 'shared_default')

    def test_personal_native_profile_wins_while_platform_fills_only_missing_channels(self):
        self.publish()
        profile = {**PROFILE, 'fingerprint': 'b' * 40, 'user_agent': 'personal-native-agent'}
        self.child.put('login-profile:app', profile)
        self.child.put('credential:mini', credential('mini'))
        self.assertEqual(self.send(device_mode='preferred').status_code, 200)
        self.sms.send.assert_called_once_with(PHONE, profile)
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.child.get('credential:app')['headers']['x-lf-dxrisk-token'], 'app-owner-device')
        self.assertEqual(self.child.get('credential:mini')['headers']['x-lf-dxrisk-token'], 'private-device-token')

    def test_sign_only_admin_configuration_does_not_fake_native_login_readiness(self):
        self.root.put('login-profile:app', None); self.publish()
        state = self.client.get('/api/state').json['login_methods']['app']
        self.assertFalse(state['effective_sms_available'])
        self.assertEqual(state['profile_source'], 'missing')
        self.assertEqual(state['device_sources'], {'app': 'platform', 'mini': 'platform'})
        r = self.send(device_mode='preferred')
        self.assertEqual(r.status_code, 400)
        self.assertIn('短信登录设备', r.json['error'])
        self.sms.send.assert_not_called()

    def test_admin_import_publishes_new_sign_devices_ordinary_import_stays_personal(self):
        from unittest.mock import patch
        self.publish()
        new = credential(); new['headers']['x-lf-dxrisk-token'] = 'newly-imported-owner-device'
        bundle = {'app': {'credential': new, 'login': False}}
        with patch('app.parse_text', return_value={'app': new}), patch.object(self.app.extensions['runner'], 'submit'):
            r = self.admin.post('/api/import', json={'channel': 'app', 'text': 'fixture'}, headers=self.admin_headers)
        self.assertEqual(r.status_code, 200, r.json)
        config = self.root.get(shared_device.KEY)
        self.assertEqual(config['devices']['app']['risk_token'], 'newly-imported-owner-device')
        generation = config['generation']
        personal = credential(); personal['headers']['x-lf-dxrisk-token'] = 'personal-import-device'
        runner = self.app.extensions['workspaces'].get(2)[1]
        with patch('app.parse_text', return_value={'app': personal}), patch.object(runner, 'submit'):
            r = self.client.post('/api/import', json={'channel': 'app', 'text': 'fixture'}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.root.get(shared_device.KEY)['generation'], generation)
        self.assertEqual(self.client.get('/api/state').json['login_methods']['app']['device_sources']['app'], 'personal')

    def test_refresh_updates_only_if_device_changed_and_disable_survives_import_and_restart(self):
        self.publish(); before = self.root.get(shared_device.KEY)
        shared_device.sync(self.root)
        self.assertEqual(self.root.get(shared_device.KEY)['generation'], before['generation'])
        shared_device.configure(self.root, False)
        new = credential(); new['headers']['x-lf-dxrisk-token'] = 'changed-owner-device'
        self.root.put('credential:app', new); self.publish()
        restarted = create_app(self.tmp.name, password=ADMIN, client=self.network, login_client=self.sms)
        self.assertFalse(restarted.extensions['store'].get(shared_device.KEY)['enabled'])

    def test_mixed_selection_binds_platform_generation_and_all_personal_does_not(self):
        self.publish(); self.child.put('login-profile:app', PROFILE)
        self.assertEqual(self.send(device_mode='preferred').status_code, 200)
        shared_device.configure(self.root, False)
        self.assertEqual(self.verify().status_code, 400)
        self.sms.verify.assert_not_called()
        for key in ('app', 'mini'): self.child.put('credential:' + key, credential(key))
        selection = shared_device.resolve(self.root, self.child, ['app', 'mini'])
        self.assertIsNone(selection['shared_generation'])

    def test_device_bundle_fills_native_gap_without_replacing_newer_admin_devices(self):
        self.publish()
        config = self.root.get(shared_device.KEY)
        payload = json.dumps({'version': 1, 'profile': config['profile'], 'devices': config['devices'],
            'token': 'must-not-import-token', 'phone': PHONE, 'cookie': 'must-not-import-cookie'}).encode()
        self.root.put('login-profile:app', None)
        shared_device.configure(self.root, False)
        r = self.import_owner(payload)
        self.assertEqual(r.status_code, 200, r.json)
        config = self.root.get(shared_device.KEY)
        self.assertNotIn('must-not-import', json.dumps(config))
        self.assertIsNone(self.root.get('login-profile:app'))
        self.assertTrue(self.client.get('/api/state').json['login_methods']['app']['effective_sms_available'])
        self.assertEqual(self.send(device_mode='preferred').status_code, 200)
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.child.get('credential:app')['headers']['token'], TOKEN)

    def test_device_bundle_admin_permission_validation_and_no_partial_overwrite(self):
        self.publish(); before = self.root.get(shared_device.KEY)
        self.assertEqual(self.import_owner(b'{}').status_code, 400)
        self.assertEqual(self.import_owner(b'x' * 65537).status_code, 400)
        self.assertEqual(self.root.get(shared_device.KEY), before)
        r = self.client.post('/api/admin/shared-device/import', data={'file': (io.BytesIO(b'{}'), 'device.json')}, headers=self.headers)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.admin.post('/api/admin/shared-device/import', data={'file': (io.BytesIO(b'{}'), 'device.json')}).status_code, 403)

    def test_native_only_import_uses_platform_sign_device_without_mislabeling_it_personal(self):
        from test_login import har, login_entry
        self.publish()
        for _ in range(2):
            r = self.client.post('/api/import', data={'file': (io.BytesIO(har(login_entry())), 'own.har')}, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.json)
            methods = self.client.get('/api/state').json['login_methods']['app']
            self.assertEqual(methods['profile_source'], 'personal')
            self.assertEqual(methods['device_sources']['app'], 'platform')
            saved = self.child.get('credential:app')
            self.assertEqual(saved['headers']['x-lf-dxrisk-token'], 'app-owner-device')
            self.assertEqual(saved['device_kind'], 'shared_default')
