"""Device initialization with fake vendor results. Never sends real SMS/sign-in."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import requests
from app import create_app
from app.device import DeviceClient,DeviceRejected,DEVICE_URL
from test_login import PROFILE,PHONE,TOKEN,ADMIN
from test_core import credential

DEVICE='a'*40
OTHER='b'*40
UA='fixture-browser/1.0'
PAYLOAD='opaque-sdk-payload-'+('x'*100)

class DeviceTransportTests(unittest.TestCase):
    def transport(self,result):
        transport=MagicMock();response=MagicMock(status_code=200,content=b'{}')
        response.json.return_value=result;transport.get.return_value=response
        return transport

    def test_fixed_service_minimal_headers_and_sanitized_success(self):
        t=self.transport({'status':2,'data':DEVICE,'msg':'do not echo upstream text'})
        result=DeviceClient(t).collect(PAYLOAD,UA,'http://localhost:18091')
        self.assertEqual(result,{'status':2,'data':DEVICE,'msg':'success'})
        call=t.get.call_args;self.assertEqual(call.args[0],DEVICE_URL)
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(call.kwargs['headers']['Param'],PAYLOAD)
        self.assertNotIn('Cookie',call.kwargs['headers']);self.assertNotIn('Authorization',call.kwargs['headers'])

    def test_service_rejection_never_accepts_a_client_generated_fallback(self):
        t=self.transport({'status':-4,'data':DEVICE,'msg':PHONE+TOKEN})
        with self.assertRaises(DeviceRejected) as error:DeviceClient(t).collect(PAYLOAD,UA)
        self.assertIn('-4',str(error.exception));self.assertNotIn(PHONE,str(error.exception));self.assertNotIn(TOKEN,str(error.exception))
        self.assertEqual(t.get.call_count,1)

    def test_invalid_payload_never_reaches_network(self):
        t=self.transport({'status':2,'data':DEVICE})
        for value in [None,'short','x'*16385,'x'*40+'\r\nCookie: forged','非ASCII'*40]:
            with self.assertRaises(ValueError):DeviceClient(t).collect(value,UA)
        t.get.assert_not_called()

    def test_bad_vendor_data_and_timeout_are_closed_and_not_retried(self):
        for result in [None,[],{'status':2,'data':'x'*1786},{'status':True,'data':DEVICE},{'status':0,'data':DEVICE}]:
            t=self.transport(result)
            with self.assertRaises(ValueError):DeviceClient(t).collect(PAYLOAD,UA)
            self.assertEqual(t.get.call_count,1)
        with patch('app.device.requests.Session') as session:
            session.return_value.get.side_effect=requests.Timeout('secret '+PAYLOAD)
            with self.assertRaises(ValueError) as error:DeviceClient().collect(PAYLOAD,UA)
            self.assertNotIn(PAYLOAD,str(error.exception));session.return_value.close.assert_called_once()
            self.assertFalse(session.return_value.trust_env)

    def test_sdk_bytes_are_pinned_and_contain_no_app_secret(self):
        p=Path(__file__).resolve().parents[1]/'app/static/vendor/device-sdk.js'
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),'a7efb2f73cd517f6db61bd373d3237823e19c1f9aa31d60fca7a8753ad208576')
        self.assertNotIn(b'AppSecret',p.read_bytes())

class DeviceRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.network=MagicMock()
        self.network.run.return_value={'state':'unsigned','message':'fixture not signed'}
        self.sms=MagicMock();self.sms.verify.return_value=TOKEN
        self.device=MagicMock();self.device.collect.return_value={'status':2,'data':DEVICE,'msg':'success'}
        self.app=create_app(self.tmp.name,password=ADMIN,client=self.network,login_client=self.sms,device_client=self.device)
        self.store=self.app.extensions['store'];self.c=self.login('admin',ADMIN)

    def tearDown(self):self.tmp.cleanup()

    def login(self,username,password):
        c=self.app.test_client();c.get('/login')
        with c.session_transaction() as s:csrf=s['csrf']
        self.assertEqual(c.post('/login',data={'username':username,'password':password,'csrf':csrf}).status_code,302)
        return c

    def post(self,path,body=None,c=None):
        c=c or self.c
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']}
        return c.post('/api/'+path,json=body or {},headers=headers)

    def setup_device(self):
        r=self.post('device/start');self.assertEqual(r.status_code,200,r.json);return r.json

    def collect(self,setup,c=None):
        path=setup['frame_url'].replace('/device/frame/','/device/collect/')+'/udid/c1'
        return (c or self.c).get(path,headers={'Param':PAYLOAD,'User-Agent':UA,'Cookie':'must-not-forward'})

    def finish(self,setup,token=DEVICE,c=None):
        return self.post('device/finish',{'ticket':setup['ticket'],'token':token},c)

    def test_automatic_initialization_does_not_skip_first_import(self):
        setup=self.setup_device();self.assertIsNone(self.store.get('login-profile:app'))
        self.assertEqual(self.collect(setup).status_code,200)
        self.assertEqual(self.finish(setup).status_code,200)
        state=self.c.get('/api/state').json
        self.assertTrue(state['login_methods']['app']['automatic_ready'])
        self.assertNotIn(DEVICE,json.dumps(state));self.assertIsNone(self.store.get('credential:app'))
        self.assertIsNone(self.store.get('login-profile:app'))
        self.sms.send.assert_not_called();self.network.run.assert_not_called()
        self.assertEqual(self.post('login/sms/send',{'phone':PHONE,'target':'app','device_mode':'automatic'}).status_code,400)
        self.sms.send.assert_not_called();self.sms.verify.assert_not_called()
        self.assertIsNone(self.store.get('credential:app'))
        self.assertNotIn(DEVICE.encode(),self.store.path.read_bytes())
        self.assertIsNone(self.store.get('credential:mini'))

    def test_unissued_client_token_is_rejected_and_existing_configuration_preserved(self):
        self.store.put('credential:app',credential());self.store.put('login-profile:app',PROFILE)
        setup=self.setup_device()
        self.assertEqual(self.finish(setup).status_code,400)
        self.collect(setup)
        self.assertEqual(self.finish(setup,OTHER).status_code,400)
        self.assertIsNone(self.store.get('automatic_device'))
        self.assertEqual(self.store.get('credential:app'),credential());self.assertEqual(self.store.get('login-profile:app'),PROFILE)

    def test_old_ticket_expiry_tampering_and_consumption(self):
        old=self.setup_device();new=self.setup_device()
        self.assertEqual(self.collect(old).status_code,400)
        self.assertEqual(self.c.get('/device/frame/forged-ticket').status_code,400)
        self.collect(new);self.assertEqual(self.finish(new).status_code,200)
        self.assertEqual(self.finish(new).status_code,400)
        expired=self.setup_device();state=self.store.get('device_setup');state['expires']=time.time()-1;self.store.put('device_setup',state)
        self.assertEqual(self.collect(expired).status_code,400)

    def test_ticket_cannot_initialize_another_user_or_survive_password_change(self):
        user=self.app.extensions['accounts'].register('another_user','another-password-2026')
        other=self.login('another_user','another-password-2026')
        setup=self.setup_device();self.collect(setup)
        self.assertEqual(self.finish(setup,c=other).status_code,403)
        own,_=self.app.extensions['workspaces'].get(user['id']);self.assertIsNone(own.get('automatic_device'))
        with self.store.connect() as db:db.execute('UPDATE users SET version=version+1 WHERE id=1')
        self.assertEqual(self.collect(setup).status_code,400)

    def test_collect_rejection_saves_only_safe_error_and_cannot_enable_sms(self):
        self.device.collect.side_effect=DeviceRejected('设备服务未认可当前环境（设备码 -4）。')
        setup=self.setup_device();self.assertEqual(self.collect(setup).status_code,400)
        self.assertIn('-4',self.c.get('/api/device/status').json['error'])
        self.assertEqual(self.finish(setup).status_code,400)
        self.assertFalse(self.c.get('/api/state').json['login_methods']['app']['automatic_ready'])
        self.assertEqual(self.post('login/sms/send',{'phone':PHONE,'device_mode':'automatic'}).status_code,400)
        self.sms.send.assert_not_called()

    def test_imported_devices_are_not_overwritten_by_automatic_mode(self):
        self.store.put('credential:app',credential())
        setup=self.setup_device();self.collect(setup);self.finish(setup)
        self.assertEqual(self.post('login/sms/send',{'phone':PHONE,'device_mode':'automatic'}).status_code,200)
        self.assertEqual(self.post('login/sms/verify',{'code':'012345'}).status_code,200)
        self.assertEqual(self.store.get('credential:app')['headers']['x-lf-dxrisk-token'],'private-device-token')
        self.assertEqual(self.store.get('credential:app')['headers']['x-lf-dxrisk-source'],'2')

    def test_expired_device_and_initialization_during_sms_are_rejected(self):
        setup=self.setup_device();self.collect(setup);self.finish(setup)
        device=self.store.get('automatic_device');device['expires']=time.time()-1;self.store.put('automatic_device',device)
        self.assertEqual(self.post('login/sms/send',{'phone':PHONE,'device_mode':'automatic'}).status_code,400)
        self.store.put('pending_sms',{'expires':time.time()+600})
        self.assertEqual(self.post('device/start').status_code,400)
        self.sms.send.assert_not_called()

    def test_frame_csp_auth_csrf_and_relay_do_not_set_account_cookies(self):
        anon=self.app.test_client();anon.get('/login')
        self.assertEqual(self.post('device/start',c=anon).status_code,401)
        self.assertEqual(self.c.post('/api/device/start',json={}).status_code,403)
        setup=self.setup_device();frame=anon.get(setup['frame_url'])
        self.assertEqual(frame.status_code,200)
        self.assertIn('sandbox allow-scripts allow-same-origin',frame.headers['Content-Security-Policy'])
        self.assertIn('nonce-',frame.headers['Content-Security-Policy'])
        self.assertEqual(frame.headers['X-Frame-Options'],'SAMEORIGIN')
        self.assertNotIn('unsafe-eval',self.c.get('/credentials').headers['Content-Security-Policy'])
        fresh=self.app.test_client();response=self.collect(setup,c=fresh)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('Set-Cookie',response.headers)
        self.assertEqual(self.device.collect.call_args.args[:2],(PAYLOAD,UA))
        self.assertEqual(self.c.get('/device/collect/forged').status_code,400)

    def test_initialization_rate_and_runner_lock(self):
        runner=self.app.extensions['runner'];runner.lock.acquire()
        try:self.assertEqual(self.post('device/start').status_code,409)
        finally:runner.lock.release()
        for _ in range(6):self.assertEqual(self.post('device/start').status_code,200)
        self.assertEqual(self.post('device/start').status_code,400)

    def test_failed_activity_validation_preserves_old_credential(self):
        self.store.put('credential:app',credential());setup=self.setup_device();self.collect(setup);self.finish(setup)
        self.post('login/sms/send',{'phone':PHONE,'device_mode':'automatic'})
        self.network.run.return_value={'state':'auth_expired'}
        self.assertEqual(self.post('login/sms/verify',{'code':'012345'}).status_code,400)
        self.assertEqual(self.store.get('credential:app'),credential())
