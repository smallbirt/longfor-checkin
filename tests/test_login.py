"""Fake captures and mocked transports only; no SMS, OTP replay, or sign requests."""
import base64
import copy
import hashlib
import io
import json
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import requests
from app import create_app
from app.importer import parse_capture
from app.login_client import (LoginClient, LoginError, canonical, signed_headers,
    checkin_credential, LOGIN_BASE)
from app.login_constants import SIGNING_INPUTS
from test_core import credential, entry, Transport

PHONE = '13800000000'
TOKEN = 'abcdefabcdefabcdefabcdefabcdefab'
PROFILE = {'fingerprint': 'own-fake-device-' + 'a' * 25, 'user_agent': 'fake-native-client', 'phone_hint': '138****0000'}
ADMIN = 'admin-test-password-2026'


def login_entry(channel='app', minute='20'):
    app = channel == 'app'
    body = {'mobile': base64.b64encode(PHONE.encode()).decode(), 'prefix': '+86',
            'fingerprint': PROFILE['fingerprint'], 'code': '123456'}
    if not app:
        body.update(thirdId='never-retain-wx-authorization', thirdType='WX_APPLET')
    return {'startedDateTime': '2026-10-20T03:' + minute + ':00Z',
        'request': {'method': 'POST', 'url': ('https://gw2c-hw-open.longfor.com/supera/mine/' +
          ('v1_29_2/publicApi/login/smsCodeSecret' if app else 'v2_5_0/publicApi/login/thirdSmsSecret')),
          'headers': [{'name': k, 'value': v} for k, v in {
              'X-LF-Channel': 'L0' if app else 'C2', 'X-LF-Bucode': 'L00602' if app else 'C20400',
              'User-Agent': PROFILE['user_agent']}.items()], 'postData': {'text': json.dumps(body)}},
        'response': {'status': 200, 'content': {'text': json.dumps({'code': '200', 'data': {'token': TOKEN}})}}}


def har(*entries):
    return json.dumps({'log': {'entries': list(entries)}}).encode()


class ProtocolTests(unittest.TestCase):
    def test_signing_is_order_independent_and_matches_explicit_material(self):
        body = {'prefix': '+86', 'code': '012345', 'empty': '', 'ignored': None, 'mobile': 'abc'}
        expected = hashlib.md5(('code=012345|mobile=abc|prefix=+86&1792464000000&' + SIGNING_INPUTS['app']).encode()).hexdigest()
        self.assertEqual(signed_headers(body, timestamp=1792464000000)['X-LONGZHU-Sign'], expected)
        self.assertEqual(canonical({'b': [{'z': 1, 'a': True}], 'a': {'d': 'x', 'c': None}}), 'a={d=x}|b=[{a=true|z=1}]')
        self.assertNotEqual(signed_headers(body, 'mini', 1792464000000)['X-LONGZHU-Sign'], expected)
        with patch('app.login_client.time.time', side_effect=[1000, 1001]):
            self.assertNotEqual(signed_headers(body), signed_headers(body))

    def test_import_login_channels_and_minimal_profile(self):
        out = parse_capture(har(login_entry(), login_entry('mini')))
        self.assertEqual(set(out), {'app', 'mini'})
        for key, item in out.items():
            self.assertEqual(item['credential']['headers']['token'], TOKEN)
            self.assertEqual(item['profile'], PROFILE)
            rendered = json.dumps(item)
            for private in [PHONE, '123456', 'never-retain-wx-authorization', 'cookie']:
                self.assertNotIn(private, rendered)
            self.assertNotIn('x-lf-dxrisk-token', item['credential']['headers'])

    def test_newest_record_wins_across_native_login_and_sign_page(self):
        sign = entry(); sign['startedDateTime'] = '2026-10-20T03:25:00Z'
        out = parse_capture(har(sign, login_entry('app', '20'), login_entry('app', '01')))
        self.assertFalse(out['app']['login'])
        sign['startedDateTime'] = '2026-10-20T03:15:00Z'
        out = parse_capture(har(login_entry(), sign))
        self.assertTrue(out['app']['login'])
        self.assertEqual(out['app']['device']['headers']['x-lf-dxrisk-token'], 'private-device-token')

    def test_device_is_retained_when_newer_query_omits_risk_headers(self):
        for channel in ('app','mini'):
            sign=entry(channel);sign['startedDateTime']='2026-10-20T03:15:00Z'
            query=entry(channel);query['startedDateTime']='2026-10-20T03:18:00Z'
            query['request']['url']=query['request']['url'].replace('/clock','/page-info')
            query['request']['headers']=[h for h in query['request']['headers'] if h['name'].lower() not in ('x-lf-dxrisk-token','x-lf-dxrisk-captcha-token')]
            for header in query['request']['headers']:
                if header['name'].lower() in ('token','x-lf-usertoken'):header['value']='newer-query-account-token'
            result=parse_capture(har(query,sign))[channel]
            self.assertFalse(result['login'])
            self.assertEqual(result['credential']['headers']['token'],'newer-query-account-token')
            self.assertEqual(result['credential']['headers']['x-lf-dxrisk-token'],'private-device-token')
            result=parse_capture(har(login_entry(channel),query,sign))[channel]
            self.assertTrue(result['login'])
            self.assertEqual(result['credential']['headers']['token'],TOKEN)
            self.assertEqual(result['device']['headers']['x-lf-dxrisk-token'],'private-device-token')
            self.assertNotIn('x-lf-dxrisk-captcha-token',result['device']['headers'])

    def test_bad_login_response_host_and_headers_are_rejected(self):
        original = login_entry()
        for change in [lambda e: e['response'].update(status=302),
                       lambda e: e['response']['content'].update(text=json.dumps({'code': '401', 'data': {'token': TOKEN}})),
                       lambda e: e['response']['content'].update(text=json.dumps({'code': '200', 'data': {'token': 'bad\r\ntoken'}})),
                       lambda e: e['request'].update(url=e['request']['url'].replace('gw2c-hw-open.longfor.com', 'evil.example')),
                       lambda e: e['request']['headers'][0].update(value='C2')]:
            e = copy.deepcopy(original); change(e)
            with self.assertRaises(ValueError): parse_capture(har(e))
        e = copy.deepcopy(original); c = e['response']['content']; c['text'] = base64.b64encode(c['text'].encode()).decode(); c['encoding'] = 'base64'
        self.assertIn('app', parse_capture(har(e)))

    def test_fresh_signed_sms_request_is_fixed_and_preserves_leading_zero(self):
        transport = Transport([{'code':'200','data':None}, {'code':'200','data':{'token':TOKEN}}])
        client = LoginClient(transport)
        client.send(PHONE, PROFILE)
        self.assertEqual(client.verify(PHONE, '012345', PROFILE), TOKEN)
        for url, kwargs in transport.calls:
            self.assertTrue(url.startswith(LOGIN_BASE))
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['json']['mobile'], base64.b64encode(PHONE.encode()).decode())
            self.assertNotIn('Cookie', kwargs['headers'])
            self.assertEqual(kwargs['headers']['X-Client-Type'], 'app')
        self.assertEqual(transport.calls[1][1]['json']['code'], '012345')
        self.assertTrue(transport.calls[0][0].endswith('/sendSmsCodeSecret'))

    def test_transport_errors_are_sanitized_and_never_retried(self):
        for payload in [requests.Timeout('secret phone ' + PHONE), {'code':'wrong','msg':TOKEN + PHONE}]:
            transport = Transport([payload])
            with self.assertRaises(LoginError) as ctx: LoginClient(transport).send(PHONE, PROFILE)
            self.assertNotIn(PHONE, str(ctx.exception)); self.assertNotIn(TOKEN, str(ctx.exception))
            self.assertEqual(len(transport.calls), 1)
        with patch('app.login_client.requests.Session') as session:
            session.return_value.post.side_effect = requests.Timeout()
            with self.assertRaises(LoginError): LoginClient().send(PHONE, PROFILE)
            session.return_value.close.assert_called_once()
            self.assertFalse(session.return_value.trust_env)

    def test_native_fingerprint_never_becomes_checkin_risk_proof(self):
        old = credential(); old['headers']['cookie'] = 'stale-cookie'; old['headers']['x-lf-dxrisk-captcha-token'] = 'one-time'
        new = checkin_credential('app', TOKEN, old)
        self.assertEqual(new['headers']['x-lf-dxrisk-token'], 'private-device-token')
        self.assertNotIn('cookie', new['headers']); self.assertNotIn('x-lf-dxrisk-captcha-token', new['headers'])


class LoginRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.network = MagicMock()
        self.network.run.return_value = {'state':'unsigned', 'message':'服务端显示今日未签到。'}
        self.sms = MagicMock(); self.sms.verify.return_value = TOKEN
        self.app = create_app(self.tmp.name, password=ADMIN, client=self.network, login_client=self.sms)
        self.client = self.app.test_client(); self.client.get('/login')
        with self.client.session_transaction() as s: csrf = s['csrf']
        self.client.post('/login', data={'username':'admin','password':ADMIN,'csrf':csrf})
        with self.client.session_transaction() as s: self.headers = {'X-CSRF-Token':s['csrf']}
        self.store = self.app.extensions['store']
        self.store.put('credential:app', credential())
        self.store.put('login-profile:app', PROFILE)

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, endpoint, **body):
        return self.client.post('/api/login/app/' + endpoint, json=body, headers=self.headers)

    def upload(self, *entries):
        return self.client.post('/api/import', data={'file':(io.BytesIO(har(*entries)), 'own.har')}, headers=self.headers)

    def pending(self, **extra):
        self.store.put('pending_sms', {'phone':PHONE, 'phone_hint':'138****0000', 'expires':time.time()+600,
            'retry_at':time.time()+60, 'attempts':0, 'profile':PROFILE, **extra})

    def test_authenticated_send_rate_binding_and_secret_free_state(self):
        self.assertEqual(self.post('send', phone=PHONE).status_code, 200)
        self.assertEqual(self.post('send', phone=PHONE).status_code, 400)
        self.sms.send.assert_called_once_with(PHONE, PROFILE)
        state = self.client.get('/api/state')
        self.assertTrue(state.json['login_methods']['app']['pending'])
        for secret in [PHONE, PROFILE['fingerprint'], TOKEN]: self.assertNotIn(secret.encode(), state.data)
        self.assertNotIn(PHONE.encode(), self.store.path.read_bytes())
        self.assertEqual(self.post('verify', code='012345', phone='13900000000', user_id=999).status_code, 200)
        self.sms.verify.assert_called_once_with(PHONE, '012345', PROFILE)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertGreater(self.client.get('/api/state').json['login_methods']['app']['retry_after'], 0)
        self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.assertEqual(self.store.get('credential:app')['headers']['token'], TOKEN)
        self.assertEqual(self.store.get('credential:app')['headers']['x-lf-dxrisk-token'], 'private-device-token')
        self.assertIs(self.network.run.call_args.kwargs['allow_sign'], False)

    def test_first_app_import_is_required_for_all_sms_device_modes(self):
        from app import shared_device
        self.store.put('credential:mini',credential('mini'))
        shared_device.sync(self.store)
        self.store.put('credential:app',None)
        self.store.put('automatic_device',{**PROFILE,'expires':time.time()+600})
        state=self.client.get('/api/state').json
        self.assertTrue(state['login_methods']['app']['first_import_required'])
        self.assertFalse(state['login_methods']['app']['effective_sms_available'])
        for route in ('app','sms'):
            for mode in ('preferred','imported','shared','automatic'):
                response=self.client.post('/api/login/'+route+'/send',json={'phone':PHONE,'device_mode':mode},headers=self.headers)
                self.assertEqual(response.status_code,400,response.json)
        self.sms.send.assert_not_called()
        self.pending(target='app')
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.sms.verify.assert_not_called()
        self.assertIsNone(self.store.get('pending_sms'))
        response=self.client.post('/api/import',data={'channel':'app',
            'file':(io.BytesIO(har(login_entry())),'app-login-only.har')},headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertFalse(self.client.get('/api/state').json['login_methods']['app']['first_import_required'])
        self.assertEqual(self.post('send',phone=PHONE).status_code,200)

    def test_first_login_only_imports_need_no_sign_page_or_actual_sign(self):
        self.store.put('credential:app',None)
        for channel in ('app','mini'):
            response=self.client.post('/api/import',data={'channel':channel,
                'file':(io.BytesIO(har(login_entry(channel))),channel+'-login-only.har')},headers=self.headers)
            self.assertEqual(response.status_code,200,response.json)
            cred=self.store.get('credential:'+channel)
            self.assertEqual(cred['headers']['token'],TOKEN)
            self.assertNotIn('x-lf-dxrisk-token',cred['headers'])
            self.assertEqual(self.store.get('login-profile:'+channel),PROFILE)
        self.assertEqual(self.network.run.call_count,2)
        self.assertTrue(all(not call.kwargs['allow_sign'] for call in self.network.run.call_args_list))
        self.sms.send.assert_not_called();self.sms.verify.assert_not_called()

    def test_one_har_merges_both_accounts_and_devices_then_update_preserves_settings(self):
        self.store.put('credential:app',None);self.store.put('credential:mini',None)
        self.store.put('notifications',{'recipient':'own@163.com','verified':True})
        self.store.put('schedule_enabled',True)
        app=login_entry();mini=login_entry('mini')
        mini_token='b'*32
        mini['response']['content']['text']=json.dumps({'code':'200','data':{'token':mini_token}})
        signs=[]
        for channel in ('app','mini'):
            sign=entry(channel);sign['startedDateTime']='2026-10-20T03:15:00Z'
            for header in sign['request']['headers']:
                if header['name'].lower()=='x-lf-dxrisk-token':header['value']=channel+'-own-sign-device'
            signs.append(sign)
        response=self.upload(app,mini,*signs)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual({item['action'] for item in response.json['updated_channels']},{'created'})
        self.assertTrue(all(item['device_ready'] for item in response.json['updated_channels']))
        self.assertEqual(self.store.get('credential:app')['headers']['token'],TOKEN)
        self.assertEqual(self.store.get('credential:mini')['headers']['token'],mini_token)
        for channel in ('app','mini'):
            self.assertEqual(self.store.get('credential:'+channel)['headers']['x-lf-dxrisk-token'],channel+'-own-sign-device')
            self.assertEqual(self.store.get('login-profile:'+channel),PROFILE)
        old_mini=self.store.get('credential:mini')
        app['response']['content']['text']=json.dumps({'code':'200','data':{'token':'c'*32}})
        response=self.upload(app)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['updated_channels'],[{'id':'app','action':'updated','device_ready':True}])
        self.assertEqual(self.store.get('credential:app')['headers']['x-lf-dxrisk-token'],'app-own-sign-device')
        self.assertEqual(self.store.get('credential:mini'),old_mini)
        self.assertEqual(self.store.get('notifications'),{'recipient':'own@163.com','verified':True})
        self.assertTrue(self.store.get('schedule_enabled'))
        self.assertTrue(all(not call.kwargs['allow_sign'] for call in self.network.run.call_args_list))

    def test_effective_device_status_can_use_platform_without_another_user_upload(self):
        from app import shared_device
        shared_device.sync(self.store)
        user=self.app.extensions['accounts'].register('new_user','new-user-password-2026')
        client=self.app.test_client();client.get('/login')
        with client.session_transaction() as session:csrf=session['csrf']
        client.post('/login',data={'username':'new_user','password':'new-user-password-2026','csrf':csrf})
        state=client.get('/api/state').json
        app=next(channel for channel in state['channels'] if channel['id']=='app')
        self.assertFalse(app['configured']);self.assertTrue(app['device_ready'])
        self.assertEqual(app['effective_device_source'],'platform')
        self.assertNotIn('private-device-token',json.dumps(state))

    def test_missing_profile_invalid_phone_csrf_and_auth_never_send(self):
        self.store.put('login-profile:app', None)
        self.assertEqual(self.post('send', phone=PHONE).status_code, 400)
        self.assertEqual(self.post('send', phone='wrong').status_code, 400)
        self.assertEqual(self.client.post('/api/login/app/send', json={'phone':PHONE}).status_code, 403)
        stranger = self.app.test_client(); stranger.get('/login')
        with stranger.session_transaction() as s: h = {'X-CSRF-Token':s['csrf']}
        self.assertEqual(stranger.post('/api/login/app/send', json={'phone':PHONE}, headers=h).status_code, 401)
        self.sms.send.assert_not_called()

    def test_tenant_challenge_and_profiles_are_isolated(self):
        accounts = self.app.extensions['accounts']; user = accounts.register('another_user', 'another-password-2026')
        other = self.app.test_client(); other.get('/login')
        with other.session_transaction() as s: csrf=s['csrf']
        other.post('/login', data={'username':'another_user','password':'another-password-2026','csrf':csrf})
        with other.session_transaction() as s: headers={'X-CSRF-Token':s['csrf']}
        self.pending()
        self.assertFalse(other.get('/api/state').json['login_methods']['app']['sms_available'])
        self.assertEqual(other.post('/api/login/app/verify', json={'code':'012345'}, headers=headers).status_code, 400)
        self.assertIsNotNone(self.store.get('pending_sms'))
        self.sms.verify.assert_not_called()

    def test_failed_verification_attempt_limit_and_expiry_preserve_credentials(self):
        self.pending(); self.sms.verify.side_effect = LoginError('校验失败')
        for _ in range(6): self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.assertEqual(self.sms.verify.call_count, 5)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertEqual(self.store.get('credential:app'), credential())
        self.pending(expires=time.time()-1)
        self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.assertEqual(self.sms.verify.call_count, 5)

    def test_send_timeout_allows_received_code_but_verify_timeout_consumes_challenge(self):
        self.sms.send.side_effect = LoginError('超时', uncertain=True)
        self.assertTrue(self.post('send', phone=PHONE).json['uncertain'])
        self.sms.verify.side_effect = LoginError('超时', uncertain=True)
        self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.sms.verify.assert_called_once()
        self.assertEqual(self.store.get('credential:app'), credential())

    def test_readback_failure_does_not_replace_working_token(self):
        self.pending(); self.network.run.return_value={'state':'auth_expired','message':'登录失效'}
        self.assertEqual(self.post('verify', code='012345').status_code, 400)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertEqual(self.store.get('credential:app'), credential())

    def test_import_both_native_logins_and_no_secrets_or_wx_identity(self):
        self.pending()
        result = self.upload(login_entry(), login_entry('mini'))
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(self.store.get('credential:mini')['headers']['token'], TOKEN)
        self.assertNotIn('x-lf-dxrisk-token', self.store.get('credential:mini')['headers'])
        self.assertEqual(self.store.get('login-profile:app'), PROFILE)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertNotIn('thirdId', json.dumps(self.store.get('login-profile:mini')))
        self.assertFalse(self.client.get('/api/state').json['login_methods']['mini']['sms_available'])
        self.assertEqual(self.network.run.call_count, 2)
        for call in self.network.run.call_args_list: self.assertFalse(call.kwargs['allow_sign'])

    def test_import_failed_readback_keeps_old_credentials_and_profile(self):
        self.network.run.return_value={'state':'unknown'}
        self.assertEqual(self.upload(login_entry()).status_code, 400)
        self.assertEqual(self.store.get('credential:app'), credential())
        self.assertEqual(self.store.get('login-profile:app'), PROFILE)

    def test_selected_channel_missing_or_invalid_never_queries_or_saves(self):
        for channel in ('mini', 'invalid'):
            response=self.client.post('/api/import',data={'channel':channel,
                'file':(io.BytesIO(har(login_entry())),'app.har')},headers=self.headers)
            self.assertEqual(response.status_code,400,response.json)
        self.network.run.assert_not_called()
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertIsNone(self.store.get('credential:mini'))

    def test_mini_import_keeps_pending_app_otp_and_app_verify_keeps_new_mini(self):
        self.pending(target='app')
        self.assertEqual(self.client.post('/api/import',data={'channel':'mini',
            'file':(io.BytesIO(har(login_entry('mini'))),'mini.har')},headers=self.headers).status_code,200)
        mini=self.store.get('credential:mini')
        self.assertEqual(self.store.get('pending_sms')['target'],'app')
        self.assertEqual(self.post('verify',code='012345').status_code,200)
        self.assertEqual(self.store.get('credential:mini'),mini)

    def test_failed_mini_import_preserves_both_channels_and_app_challenge(self):
        self.store.put('credential:mini',credential('mini'))
        self.pending(target='app')
        self.network.run.return_value={'state':'auth_expired'}
        response=self.client.post('/api/import',data={'channel':'mini',
            'file':(io.BytesIO(har(login_entry(),login_entry('mini'))),'mixed.har')},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertEqual(self.store.get('credential:mini'),credential('mini'))
        self.assertEqual(self.store.get('pending_sms')['target'],'app')
        self.sms.verify.assert_not_called()

    def test_user_task_lock_prevents_parallel_verification_import_or_send(self):
        self.pending(); runner = self.app.extensions['runner']; runner.lock.acquire()
        try:
            self.assertEqual(self.post('send', phone=PHONE).status_code, 409)
            self.assertEqual(self.post('verify', code='012345').status_code, 409)
            self.assertEqual(self.upload(login_entry()).status_code, 409)
        finally: runner.lock.release()
        self.sms.send.assert_not_called(); self.sms.verify.assert_not_called()
        self.assertEqual(self.store.get('pending_sms')['attempts'], 0)

    def test_restart_retains_own_profile_without_saving_otp(self):
        self.upload(login_entry())
        restarted = create_app(self.tmp.name, password=ADMIN, client=self.network, login_client=self.sms)
        self.assertEqual(restarted.extensions['store'].get('login-profile:app'), PROFILE)
        self.assertNotIn(b'123456', self.store.path.read_bytes())

    def test_mini_native_import_uses_mini_activity_and_keeps_app_credential(self):
        old_mini=credential('mini');old_mini['headers']['x-lf-dxrisk-token']='own-mini-device'
        self.store.put('credential:mini',old_mini)
        r=self.client.post('/api/import',data={'channel':'mini',
            'file':(io.BytesIO(har(login_entry(),login_entry('mini'))),'mixed.har')},headers=self.headers)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.store.get('credential:app'),credential())
        mini=self.store.get('credential:mini')
        self.assertEqual(mini['headers']['token'],TOKEN)
        self.assertEqual(mini['headers']['x-lf-channel'],'C2')
        self.assertEqual(mini['activity_no'],old_mini['activity_no'])
        self.assertEqual(mini['headers']['x-lf-dxrisk-token'],'own-mini-device')
        self.assertFalse(self.network.run.call_args.kwargs['allow_sign'])
        self.assertEqual(self.network.run.call_count,1)
        self.sms.send.assert_not_called()
        self.sms.verify.assert_not_called()

    def test_app_sms_preserves_mini_token_and_device_even_if_verify_requests_mini(self):
        mini=credential('mini');mini['headers']['x-lf-dxrisk-token']='own-mini-device'
        self.store.put('credential:mini',mini)
        self.assertEqual(self.post('send',phone=PHONE,target='app').status_code,200)
        self.assertEqual(self.post('verify',code='012345',target='mini').status_code,200)
        self.assertEqual(self.network.run.call_count,1)
        calls=self.network.run.call_args_list
        self.assertEqual([c.args[0]['headers']['x-lf-channel'] for c in calls],['L0'])
        self.assertTrue(all(c.kwargs['allow_sign'] is False for c in calls))
        self.assertEqual(self.store.get('credential:app')['headers']['x-lf-dxrisk-token'],'private-device-token')
        self.assertEqual(self.store.get('credential:mini')['headers']['x-lf-dxrisk-token'],'own-mini-device')
        self.assertEqual(self.store.get('credential:app')['headers']['token'],TOKEN)
        self.assertEqual(self.store.get('credential:mini'),mini)
        self.sms.verify.assert_called_once()

    def test_old_cross_channel_challenge_is_revoked_without_consuming_otp(self):
        self.store.put('credential:mini',credential('mini'))
        self.pending(target='both')
        self.network.run.side_effect=[{'state':'signed'},{'state':'auth_expired'}]
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertEqual(self.store.get('credential:mini'),credential('mini'))
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.sms.verify.assert_not_called()

    def test_invalid_target_never_sends_or_creates_a_challenge(self):
        for target in ['mini','both','arbitrary',None,[],{}]:
            self.assertEqual(self.post('send',phone=PHONE,target=target).status_code,400)
        self.sms.send.assert_not_called()
        self.assertIsNone(self.store.get('pending_sms'))

    def test_import_cancels_pending_mini_sms_to_avoid_overwriting_new_import(self):
        self.pending(target='mini')
        self.assertEqual(self.upload(login_entry('mini')).status_code,200)
        self.assertIsNone(self.store.get('pending_sms'))
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.sms.verify.assert_not_called()

    def test_production_validation_uses_fresh_closed_session_not_runner_cookie_jar(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,password=ADMIN,login_client=self.sms)
            c=app.test_client();c.get('/login')
            with c.session_transaction() as s:csrf=s['csrf']
            c.post('/login',data={'username':'admin','password':ADMIN,'csrf':csrf})
            with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']}
            store=app.extensions['store']
            store.put('credential:app',credential())
            store.put('pending_sms',{'phone':PHONE,'phone_hint':'138****0000','expires':time.time()+600,
                'attempts':0,'profile':PROFILE,'target':'app'})
            fake=MagicMock();fake.run.return_value={'state':'signed'}
            runner=app.extensions['runner'];runner.client.transport.cookies.set('old-login','must-not-authenticate-new-token')
            with patch('app.LongforClient',side_effect=[fake,fake]) as factory:
                response=c.post('/api/login/sms/verify',json={'code':'012345'},headers=headers)
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(factory.call_count,1)
            self.assertEqual(fake.transport.close.call_count,1)
            self.assertEqual(runner.client.transport.cookies.get('old-login'),'must-not-authenticate-new-token')
            runner.client.transport.close()
