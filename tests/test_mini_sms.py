"""Mini SMS experiment uses mock transports only, never real messages or OTPs."""
import copy
import io
import json
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import requests
from app import create_app, shared_device
from app.mini_login import MiniLoginClient, BASE, APP_ID, SOURCE, PENDING, RETRY
from app.login_client import LoginError, signed_headers
from test_core import credential, Transport
from test_login import ADMIN, PROFILE, PHONE, TOKEN, har, login_entry


class MiniProtocolTests(unittest.TestCase):
    def test_native_mini_requests_omit_wechat_auth_and_do_not_use_app_endpoints(self):
        transport=Transport([{'code':'200','data':None},{'code':'200','data':{'token':TOKEN}}])
        client=MiniLoginClient(transport)
        client.send(PHONE,PROFILE)
        self.assertEqual(client.verify(PHONE,'012345',PROFILE),TOKEN)
        for url,request in transport.calls:
            self.assertTrue(url.startswith(BASE))
            self.assertFalse(request['allow_redirects'])
            self.assertEqual(request['headers']['X-LF-Channel'],'C2')
            self.assertEqual(request['headers']['X-Client-Type'],'microApp')
            self.assertEqual(request['json']['appId'],APP_ID)
            for key in ('thirdId','authCode','ticket','Cookie','token'):
                self.assertNotIn(key,request['json'])
                self.assertNotIn(key,request['headers'])
            signature=signed_headers(request['json'],'mini',request['headers']['X-LONGZHU-TimeStamp'])
            self.assertEqual(signature['X-LONGZHU-Sign'],request['headers']['X-LONGZHU-Sign'])
        self.assertTrue(transport.calls[0][0].endswith('/thirdSendSecret'))
        self.assertTrue(transport.calls[1][0].endswith('/thirdSmsSecret'))
        self.assertEqual(transport.calls[1][1]['json']['code'],'012345')
        self.assertEqual(transport.calls[1][1]['json']['source'],SOURCE)

    def test_business_error_displays_only_safe_code_and_classification(self):
        transport=Transport([{'code':'901001','msg':'thirdId required '+PHONE+TOKEN}])
        with self.assertLogs('app.mini_login',level='WARNING') as logs:
            with self.assertRaises(LoginError) as context:MiniLoginClient(transport).send(PHONE,PROFILE)
        result=str(context.exception)+'\n'.join(logs.output)
        self.assertIn('901001',result)
        for private in (PHONE,TOKEN,'thirdId required',PROFILE['fingerprint']):
            self.assertNotIn(private,result)
        self.assertFalse(context.exception.uncertain)
        self.assertEqual(len(transport.calls),1)

    def test_network_failure_closes_fresh_session_and_never_retries(self):
        with patch('app.mini_login.requests.Session') as factory:
            factory.return_value.post.side_effect=requests.Timeout(PHONE+TOKEN)
            with self.assertRaises(LoginError) as context:MiniLoginClient().send(PHONE,PROFILE)
            self.assertTrue(context.exception.uncertain)
            self.assertNotIn(PHONE,str(context.exception))
            self.assertFalse(factory.return_value.trust_env)
            factory.return_value.close.assert_called_once()
            factory.return_value.post.assert_called_once()

    def test_invalid_profile_phone_and_code_never_make_requests(self):
        transport=Transport([]);client=MiniLoginClient(transport)
        for phone,profile,code in [('wrong',PROFILE,None),(PHONE,{},None),(PHONE,PROFILE,'bad')]:
            with self.assertRaises(LoginError if phone==PHONE else ValueError):client.request(phone,profile,code)
        self.assertEqual(transport.calls,[])


class MiniRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.network=MagicMock();self.network.run.return_value={'state':'unsigned','message':'confirmed'}
        self.mini=MagicMock();self.mini.verify.return_value=TOKEN
        self.app_sms=MagicMock()
        self.app=create_app(self.tmp.name,password=ADMIN,client=self.network,
            login_client=self.app_sms,mini_login_client=self.mini)
        self.app.config['MINI_SMS_EXPERIMENTAL']=True
        self.store=self.app.extensions['store']
        self.store.put('login-profile:mini',PROFILE)
        self.store.put('credential:app',credential())
        self.store.put('credential:mini',credential('mini'))
        self.client,self.headers=self.login('admin',ADMIN)

    def tearDown(self):self.tmp.cleanup()

    def login(self,username,password):
        client=self.app.test_client();client.get('/login')
        with client.session_transaction() as session:csrf=session['csrf']
        client.post('/login',data={'username':username,'password':password,'csrf':csrf})
        with client.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
        return client,headers

    def post(self,op,**body):
        return self.client.post('/api/login/mini/'+op,json=body,headers=self.headers)

    def pending(self,**extra):
        from app.mini_login import selection
        self.store.put(PENDING,{'phone':PHONE,'phone_hint':'138****0000','expires':time.time()+600,
            'attempts':0,'target':'mini','device_mode':'mini_experiment',**selection(self.store,self.store),**extra})

    def test_success_binds_phone_keeps_app_and_never_submits_checkin(self):
        before=self.store.get('credential:app')
        app_pending={'target':'app','private':'unchanged','expires':time.time()+600}
        self.store.put('pending_sms',app_pending)
        self.assertEqual(self.post('send',phone=PHONE).status_code,200)
        state=self.client.get('/api/state').json
        self.assertTrue(state['login_methods']['mini']['pending'])
        for private in (PHONE,TOKEN,PROFILE['fingerprint']):self.assertNotIn(private,json.dumps(state))
        response=self.post('verify',code='012345',phone='13900000000',target='app')
        self.assertEqual(response.status_code,200,response.json)
        self.mini.verify.assert_called_once_with(PHONE,'012345',shared_device.native_profile(PROFILE))
        self.assertEqual(self.store.get('credential:app'),before)
        self.assertEqual(self.store.get('pending_sms'),app_pending)
        self.assertEqual(self.store.get('credential:mini')['headers']['token'],TOKEN)
        self.assertEqual(self.store.get('credential:mini')['headers']['x-lf-dxrisk-token'],'private-device-token')
        self.assertEqual(self.network.run.call_args.args[0]['headers']['x-lf-channel'],'C2')
        self.assertFalse(self.network.run.call_args.kwargs['allow_sign'])
        self.assertIsNone(self.store.get(PENDING))
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.app_sms.send.assert_not_called();self.app_sms.verify.assert_not_called()
        for private in (PHONE,TOKEN,PROFILE['fingerprint'],'012345'):self.assertNotIn(private.encode(),self.store.path.read_bytes())

    def test_disabled_auth_and_csrf_never_send(self):
        self.app.config['MINI_SMS_EXPERIMENTAL']=False
        self.assertEqual(self.post('send',phone=PHONE).status_code,400)
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertFalse(self.client.get('/api/state').json['login_methods']['mini']['experimental'])
        self.app.config['MINI_SMS_EXPERIMENTAL']=True
        self.assertEqual(self.client.post('/api/login/mini/send',json={'phone':PHONE}).status_code,403)
        stranger=self.app.test_client();stranger.get('/login')
        with stranger.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
        self.assertEqual(stranger.post('/api/login/mini/send',json={'phone':PHONE},headers=headers).status_code,401)
        self.mini.send.assert_not_called()

    def test_missing_mini_profile_never_falls_back_to_app(self):
        self.store.put('login-profile:mini',None);self.store.put('login-profile:app',PROFILE)
        self.assertEqual(self.post('send',phone=PHONE).status_code,400)
        self.mini.send.assert_not_called()

    def test_business_reject_keeps_old_credentials_and_app_challenge(self):
        self.mini.send.side_effect=LoginError('rejected')
        self.store.put('pending_sms',{'target':'app'})
        before=self.store.get('credential:mini')
        self.assertEqual(self.post('send',phone=PHONE).status_code,400)
        self.assertEqual(self.store.get('credential:mini'),before)
        self.assertEqual(self.store.get('pending_sms'),{'target':'app'})
        self.assertIsNone(self.store.get(PENDING))
        self.assertGreater(self.store.get(RETRY),time.time())

    def test_timeout_retains_send_challenge_but_verify_is_not_replayed(self):
        self.mini.send.side_effect=LoginError('uncertain',uncertain=True)
        self.assertTrue(self.post('send',phone=PHONE).json['uncertain'])
        self.mini.verify.side_effect=LoginError('uncertain',uncertain=True)
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.mini.verify.assert_called_once()
        self.assertEqual(self.store.get('credential:mini'),credential('mini'))

    def test_expired_and_attempt_limit_do_not_replace_credential(self):
        self.pending(expires=time.time()-1)
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.mini.verify.assert_not_called()
        self.pending();self.mini.verify.side_effect=LoginError('bad code')
        for _ in range(6):self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertEqual(self.mini.verify.call_count,5)
        self.assertIsNone(self.store.get(PENDING))
        self.assertEqual(self.store.get('credential:mini'),credential('mini'))

    def test_validation_failure_preserves_both_credentials(self):
        self.pending();self.network.run.return_value={'state':'auth_expired'}
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertEqual(self.store.get('credential:mini'),credential('mini'))
        self.assertIsNone(self.store.get(PENDING))

    def test_import_mini_revokes_only_mini_challenge(self):
        self.pending();self.store.put('pending_sms',{'target':'app'})
        response=self.client.post('/api/import',data={'channel':'mini',
            'file':(io.BytesIO(har(login_entry('mini'))),'mini.har')},headers=self.headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertIsNone(self.store.get(PENDING))
        self.assertEqual(self.store.get('pending_sms'),{'target':'app'})

    def test_same_user_cannot_bypass_app_sms_rate_limit(self):
        self.store.put('login-profile:app',PROFILE)
        self.assertEqual(self.client.post('/api/login/app/send',json={'phone':PHONE},headers=self.headers).status_code,200)
        self.assertEqual(self.post('send',phone=PHONE).status_code,400)
        self.mini.send.assert_not_called()

    def test_platform_only_shares_device_and_refresh_revokes_otp(self):
        from test_shared_device import seed_owner
        seed_owner(self.store);shared_device.sync(self.store)
        user=self.app.extensions['accounts'].register('alice','alice-password-2026')
        self.client,self.headers=self.login('alice','alice-password-2026')
        child,_=self.app.extensions['workspaces'].get(user['id'])
        self.assertEqual(self.post('send',phone=PHONE).status_code,200)
        self.assertEqual(child.get(PENDING)['profile'],shared_device.native_profile(PROFILE))
        self.assertIsNone(child.get('credential:mini'))
        shared_device.configure(self.store,False)
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.assertIsNone(child.get(PENDING));self.mini.verify.assert_not_called()

    def test_personal_profile_change_revokes_otp(self):
        self.pending();self.store.put('login-profile:mini',{**PROFILE,'fingerprint':'b'*40})
        self.assertEqual(self.post('verify',code='012345').status_code,400)
        self.mini.verify.assert_not_called();self.assertIsNone(self.store.get(PENDING))

    def test_user_and_runner_lock_isolation(self):
        self.pending()
        user=self.app.extensions['accounts'].register('alice','alice-password-2026')
        stranger,headers=self.login('alice','alice-password-2026')
        self.assertEqual(stranger.post('/api/login/mini/verify',json={'code':'012345'},headers=headers).status_code,400)
        lock=self.app.extensions['runner'].lock;lock.acquire()
        try:
            self.assertEqual(self.post('send',phone=PHONE).status_code,409)
            self.assertEqual(self.post('verify',code='012345').status_code,409)
        finally:lock.release()
        self.mini.send.assert_not_called();self.mini.verify.assert_not_called()
        self.assertEqual(self.store.get(PENDING)['attempts'],0)
