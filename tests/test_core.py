import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.constants import CHANNELS, BASE_URL, TZ
from app.importer import parse_har, parse_text, ImportError
from app.client import LongforClient
from app.store import Store
from app.runner import Runner, due_slot
from app.mailer import validate_mail, send_mail
from app import create_app

DAY = '2026-10-16'


def credential(key='app'):
    c = CHANNELS[key]
    return {'activity_no': c['activity_no'], 'headers': {
        'token': 'private-login-token', 'x-lf-usertoken': 'private-login-token',
        'x-lf-channel': c['channel'], 'x-lf-bu-code': c['bu_code'],
        'x-lf-dxrisk-token': 'private-device-token', 'x-lf-dxrisk-source': c['source'],
        'x-gaia-api-key': 'application-key'}}


def entry(key='app', minute='10'):
    c = credential(key)
    return {'startedDateTime': '2026-10-16T09:'+minute+':00Z',
        'request': {'method': 'POST', 'url': BASE_URL + '/signature/clock',
        'headers': [{'name': k, 'value': v} for k,v in {
            **c['headers'], 'X-LF-DXRisk-Captcha-Token': 'one-time-proof'}.items()],
        'postData': {'text': json.dumps({'activity_no': c['activity_no']})}}}


def page(status=10):
    return {'code': '0000', 'data': {'task_show_status': 20, 'seven_days_signs': [
        {'sign_year': 2026, 'sign_month': 10, 'sign_day': 16, 'sign_status': status}]}}


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        data = self.responses.pop(0)
        if isinstance(data, Exception):
            raise data
        r = MagicMock(status_code=200, content=b'{}')
        r.json.return_value = data
        return r


class ImportTests(unittest.TestCase):
    def test_reverse_order_latest_and_no_captcha(self):
        old, new = entry(minute='01'), entry(minute='10')
        old['request']['headers'][0]['value'] = 'older'
        result = parse_har(json.dumps({'log': {'entries': [new, old]}}))
        self.assertEqual(result['app']['headers']['token'], 'private-login-token')
        self.assertNotIn('x-lf-dxrisk-captcha-token', result['app']['headers'])
    def test_both_channels_do_not_mix(self):
        out = parse_har(json.dumps({'log': {'entries': [entry(), entry('mini')]}}))
        self.assertEqual(set(out), {'app', 'mini'})
        self.assertNotEqual(out['app']['activity_no'], out['mini']['activity_no'])
    def test_curl_parsed_as_data(self):
        c = credential('mini')
        args = ['curl', "'%s/signature/clock'" % BASE_URL]
        args += ["-H '%s: %s'" % (k,v) for k,v in c['headers'].items()]
        args += ["--data-raw '%s'" % json.dumps({'activity_no': c['activity_no']})]
        out = parse_text(' '.join(args), 'mini')
        self.assertEqual(out['mini']['activity_no'], c['activity_no'])
    def test_wrong_host_rejected(self):
        c = credential()
        with self.assertRaises(ImportError):
            parse_text(json.dumps({'headers': c['headers'], 'url': 'https://evil.example/signature/clock'}), 'app')
    def test_wrong_channel_rejected(self):
        with self.assertRaises(ImportError):
            parse_text(json.dumps(credential()['headers']), 'mini')
    def test_headers_newline_rejected(self):
        c = credential(); c['headers']['cookie'] = 'a\nb'
        with self.assertRaises(ImportError):
            parse_text(json.dumps(c['headers']), 'app')


class ClientTests(unittest.TestCase):
    def test_already_signed_no_submission(self):
        t = Transport([page(20)])
        self.assertEqual(LongforClient(t).run(credential(),DAY)['state'], 'signed')
        self.assertEqual(len(t.calls), 1)
        self.assertNotIn('x-lf-dxrisk-token', t.calls[0][1]['headers'])
    def test_success_requires_readback(self):
        t = Transport([page(), {'code':'0000','data':{'is_popup':1}}, page(20)])
        self.assertEqual(LongforClient(t).run(credential(),DAY)['state'], 'signed')
        self.assertEqual(sum(url.endswith('/clock') for url,_ in t.calls), 1)
    def test_accepted_but_not_signed_is_unknown(self):
        t = Transport([page(), {'code':'0000','data':{'is_popup':1}}, page()])
        self.assertEqual(LongforClient(t).run(credential(),DAY)['state'], 'unknown')
    def test_captcha_stops(self):
        t = Transport([page(), {'code':'8040012'}])
        self.assertEqual(LongforClient(t,captcha_solver=lambda: None).run(credential(),DAY)['state'], 'captcha_required')
        self.assertEqual(len(t.calls), 2)
    def test_timeout_queries_no_second_submission(self):
        import requests
        t = Transport([page(), requests.Timeout('may contain secret'), page(20)])
        self.assertEqual(LongforClient(t).run(credential(),DAY)['state'], 'signed')
        self.assertEqual(sum(url.endswith('/clock') for url,_ in t.calls), 1)
    def test_expired_login(self):
        t = Transport([{'code':'900005'}])
        result = LongforClient(t).run(credential(), DAY)
        self.assertEqual(result['state'], 'auth_expired')
        self.assertNotIn('private', json.dumps(result))
    def test_missing_today_does_not_submit(self):
        t = Transport([{'code':'0000','data':{'task_show_status':20,'seven_days_signs':[]}}])
        self.assertEqual(LongforClient(t).run(credential(),DAY)['state'],'unknown')
        self.assertEqual(len(t.calls),1)
    def test_blocked_can_still_confirm_phone_signin(self):
        c=credential();c['blocked']='captcha_required'
        t=Transport([page(20)])
        self.assertEqual(LongforClient(t).run(c,DAY)['state'],'signed')
    def test_query_only_does_not_sign(self):
        t=Transport([page()])
        self.assertEqual(LongforClient(t).run(credential(),DAY,False)['state'],'unsigned')
        self.assertEqual(len(t.calls),1)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_encrypted_at_rest(self):
        self.store.put('credential:app',credential())
        self.assertEqual(self.store.get('credential:app'),credential())
        self.assertNotIn(b'private-login-token',self.store.path.read_bytes())
    def test_daily_dedupe_and_restart(self):
        self.store.put('credential:app',credential())
        self.store.put('credential:mini',credential('mini'))
        client=MagicMock();client.run.return_value={'state':'signed','message':'confirmed'}
        dt=datetime(2026,10,16,9,10,tzinfo=TZ)
        Runner(self.store,client).run('morning',dt=dt)
        Runner(self.store,client).run('morning',dt=dt)
        self.assertEqual(client.run.call_count,2)
        self.assertEqual(len(self.store.jobs()),1)
        Runner(self.store,client).run('afternoon',dt=dt.replace(hour=16))
        self.assertEqual(client.run.call_count,4)
    def test_afternoon_attempts_sign(self):
        self.store.put('credential:app',credential())
        client=MagicMock();client.run.return_value={'state':'unsigned','message':'pending'}
        Runner(self.store,client).run('afternoon',dt=datetime(2026,10,16,16,tzinfo=TZ))
        self.assertTrue(client.run.call_args.kwargs['allow_sign'])
    def test_risk_block_persisted(self):
        self.store.put('credential:app',credential())
        client=MagicMock();client.run.return_value={'state':'captcha_required','message':'verify'}
        Runner(self.store,client).run('morning',dt=datetime(2026,10,16,9,10,tzinfo=TZ))
        self.assertEqual(self.store.get('credential:app')['blocked'],'captcha_required')
    def test_notify_failure_never_reruns_signin(self):
        client=MagicMock();client.run.return_value={'state':'signed','message':'ok'}
        self.store.put('credential:app',credential())
        self.store.put('mail',{'provider':'qq'})
        sender=MagicMock(return_value=(False,'failed'))
        r=Runner(self.store,client,sender)
        r.run('morning',dt=datetime(2026,10,16,9,10,tzinfo=TZ))
        r.deliver()
        with self.store.connect() as db: db.execute('UPDATE outbox SET due=0')
        r.deliver()
        self.assertEqual(client.run.call_count,1)
        self.assertEqual(sender.call_count,2)
        self.assertEqual(self.store.jobs()[0]['mail_state'],'pending')
    def test_schedule_boundaries(self):
        d=datetime(2026,10,16,9,9,tzinfo=TZ)
        self.assertIsNone(due_slot(d))
        self.assertEqual(due_slot(d.replace(minute=10)), 'morning')
        self.assertEqual(due_slot(d.replace(hour=16,minute=0)), 'afternoon')
        self.assertIsNone(due_slot(d.replace(day=17,hour=0,minute=0)))
    def test_interrupted_job_recovers_by_query(self):
        self.store.put('credential:app',credential())
        dt=datetime(2026,10,16,9,10,tzinfo=TZ)
        with self.store.connect() as db:
            db.execute('INSERT INTO jobs(dedupe,kind,day,created,status) VALUES (?,?,?,?,?)',
                       (DAY+':morning','morning',DAY,dt.isoformat(),'running'))
        client=MagicMock();client.run.return_value={'state':'signed','message':'already done'}
        r=Runner(self.store,client);r.recover();r.run('morning',dt=dt)
        self.assertEqual(self.store.jobs()[0]['status'],'done')
        self.assertEqual(len(self.store.jobs()),1)
        self.assertEqual(client.run.call_count,1)
    def test_new_day_has_new_job(self):
        dt=datetime(2026,10,16,9,10,tzinfo=TZ)
        r=Runner(self.store)
        r.run('morning',dt=dt);r.run('morning',dt=dt.replace(day=17))
        self.assertEqual(len(self.store.jobs()),2)


class MailTests(unittest.TestCase):
    def test_presets_and_ssl(self):
        for provider,domain in [('qq','qq.com'),('163','163.com')]:
            c=validate_mail({'provider':provider,'sender':'test@'+domain,'recipient':'me@example.com','password':'authorization-code'})
            with patch('app.mailer.smtplib.SMTP_SSL') as smtp:
                smtp.return_value.__enter__.return_value.send_message.return_value={}
                ok,error=send_mail(c,'结果','今天签到成功')
                self.assertTrue(ok,error)
                self.assertEqual(smtp.call_args.args,('smtp.'+domain,465))
                self.assertTrue(smtp.call_args.kwargs['context'].check_hostname)
    def test_blank_password_only_kept_for_same_account(self):
        old={'provider':'qq','sender':'one@qq.com','recipient':'one@qq.com','password':'secret'}
        self.assertEqual(validate_mail({**old,'password':''},old)['password'],'secret')
        with self.assertRaises(ValueError):validate_mail({**old,'sender':'two@qq.com','password':''},old)
    def test_auth_error_is_safe(self):
        import smtplib
        c={'provider':'qq','sender':'a@qq.com','recipient':'b@qq.com','password':'secret'}
        with patch('app.mailer.smtplib.SMTP_SSL',side_effect=smtplib.SMTPAuthenticationError(535,b'secret')):
            ok,error=send_mail(c,'a','b')
            self.assertFalse(ok);self.assertNotIn('secret',error)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,password='admin-password-123456')
        self.client=self.app.test_client()
    def tearDown(self):self.tmp.cleanup()
    def signin(self):
        self.client.get('/login')
        with self.client.session_transaction() as s:csrf=s['csrf']
        self.client.post('/login',data={'csrf':csrf,'password':'admin-password-123456'})
        with self.client.session_transaction() as s:self.csrf=s['csrf']
    def test_login_required(self):self.assertEqual(self.client.get('/api/state').status_code,401)
    def test_csrf_required(self):
        self.signin()
        self.assertEqual(self.client.post('/api/schedule',json={'enabled':True}).status_code,403)
    def test_no_secrets_in_state(self):
        self.signin();self.app.extensions['store'].put('credential:app',credential())
        self.app.extensions['store'].put('mail',{'password':'smtp-secret','sender':'a@qq.com'})
        data=self.client.get('/api/state').data
        self.assertNotIn(b'private-login-token',data);self.assertNotIn(b'smtp-secret',data)
    def test_cannot_enable_without_setup(self):
        self.signin()
        r=self.client.post('/api/schedule',json={'enabled':True},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code,400)
    def test_mail_configuration_no_actual_send(self):
        self.signin()
        with patch('app.send_mail') as send:
            r=self.client.post('/api/mail',json={'provider':'163','sender':'a@163.com','recipient':'b@qq.com','password':'auth-code'},headers={'X-CSRF-Token':self.csrf})
            self.assertEqual(r.status_code,200);send.assert_not_called()


if __name__=='__main__':unittest.main()
