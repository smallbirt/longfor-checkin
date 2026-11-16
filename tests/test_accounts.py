import io
import json
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from app import create_app
from app.accounts import Accounts, Workspaces
from app.importer import parse_har
from app.store import Store
from app.constants import TZ
from test_core import credential, entry

ADMIN = 'admin-password-123456'
PASSWORD = 'own-password-12345'
MAIL = {'provider':'qq','sender':'owner@qq.com','password':'smtp-super-secret','recipient':'owner@qq.com'}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.network = MagicMock()
        self.network.run.side_effect = lambda *a, **kw: {'state':'signed','message':'已确认'}
        self.app = create_app(self.tmp.name, password=ADMIN, client=self.network)
        self.root = self.app.extensions['store']
        self.accounts = self.app.extensions['accounts']
        self.spaces = self.app.extensions['workspaces']
        self.alice = self.accounts.register('alice', PASSWORD)
        self.bob = self.accounts.register('bob', PASSWORD)
        self.a = self.login('alice', PASSWORD)
        self.b = self.login('bob', PASSWORD)
        self.admin = self.login('admin', ADMIN)

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, password):
        c = self.app.test_client()
        c.get('/login')
        r = c.post('/login', data={'username':username,'password':password,'csrf':self.csrf(c)})
        self.assertEqual(r.status_code, 302)
        return c

    def csrf(self, c):
        with c.session_transaction() as s:
            return s['csrf']

    def post(self, c, path, data=None):
        return c.post('/api/'+path, json=data or {}, headers={'X-CSRF-Token':self.csrf(c)})

    def test_registration_and_password_storage(self):
        c = self.app.test_client(); c.get('/register')
        r = c.post('/register', data={'csrf':self.csrf(c),'username':'New_User','password':PASSWORD,'confirm_password':PASSWORD})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get('/api/state').json['user']['username'], 'new_user')
        self.assertNotIn(PASSWORD.encode(), self.root.path.read_bytes())
        self.assertNotIn(ADMIN.encode(), self.root.path.read_bytes())
        with self.assertRaises(ValueError): self.accounts.register('ALICE', PASSWORD)
        with self.assertRaises(ValueError): self.accounts.register('../alice', PASSWORD)
        with self.assertRaises(ValueError): self.accounts.register('user123', 'short')

    def test_tenant_isolation_in_state_history_and_mutations(self):
        a, ar = self.spaces.get(self.alice['id']); b, br = self.spaces.get(self.bob['id'])
        self.root.put('credential:app', credential())
        a.put('credential:app', credential()); a.put('schedule_enabled', True)
        ar.run('check')
        self.assertFalse(self.b.get('/api/state?user_id='+str(self.alice['id'])).json['channels'][0]['configured'])
        self.assertEqual(self.b.get('/api/jobs?user_id='+str(self.alice['id'])).json['jobs'], [])
        self.assertFalse(self.b.get('/api/state').json['schedule_enabled'])
        self.post(self.b,'schedule',{'enabled':False,'user_id':self.alice['id']})
        self.assertTrue(a.get('schedule_enabled'))
        self.assertNotEqual(a.path, b.path)
        self.assertNotIn(b'private-login-token', a.path.read_bytes())

    def test_ordinary_users_cannot_change_or_read_platform_configuration(self):
        self.root.put('mail', MAIL)
        for path in ['/admin','/api/admin']:
            self.assertEqual(self.a.get(path).status_code, 403)
        for path,payload in [('mail',MAIL),('admin/registration',{'enabled':False})]:
            self.assertEqual(self.post(self.a,path,payload).status_code, 403)
        output = self.a.get('/api/state').data + self.a.get('/settings').data
        for secret in [b'smtp-super-secret',b'owner@qq.com',b'ADMIN_PASSWORD']:
            self.assertNotIn(secret,output)
        self.assertEqual(self.root.get('mail'),MAIL)
        self.assertNotIn('password',self.admin.get('/api/admin').json['mail'])

    def test_legacy_data_remains_owned_by_admin_after_restart(self):
        with tempfile.TemporaryDirectory() as legacy_dir:
            old = Store(legacy_dir)
            old.put('mail', MAIL); old.put('credential:app', credential()); old.put('schedule_enabled', True)
            with old.connect() as db:
                db.execute("INSERT INTO jobs(kind,day,created,status) VALUES('check','2026-10-19','2026-10-19T09:00:00+08:00','done')")
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone())
            upgraded = create_app(legacy_dir, password=ADMIN, client=self.network)
            restarted = create_app(legacy_dir, password=ADMIN, client=self.network)
            self.assertEqual(restarted.extensions['store'].get('credential:app'), credential())
            self.assertTrue(restarted.extensions['store'].get('schedule_enabled'))
            self.assertTrue(restarted.extensions['workspaces'].preferences(1)['verified'])
            self.assertEqual(len(restarted.extensions['store'].jobs()), 1)
            self.assertEqual(restarted.extensions['accounts'].ids(), [1])
            user = restarted.extensions['accounts'].register('new_user', PASSWORD)
            store, _ = restarted.extensions['workspaces'].get(user['id'])
            self.assertIsNone(store.get('credential:app'))
            self.assertEqual(store.jobs(), [])
            self.assertIsNone(restarted.extensions['workspaces'].mail_config(user['id']))

    def test_separate_daily_dedupe_and_mail_recipients(self):
        self.root.put('mail',MAIL)
        dt=datetime(2026,10,19,9,10,tzinfo=TZ)
        runners=[]
        for user,email in [(self.alice,'alice@163.com'),(self.bob,'bob@qq.com')]:
            s,r=self.spaces.get(user['id']);runners.append(r)
            s.put('credential:app',credential());s.put('notifications',{'recipient':email,'verified':True})
            r.run('morning',dt=dt);r.run('morning',dt=dt)
            self.assertEqual(len(s.jobs()),1)
            r.sender=MagicMock(return_value=(True,''));r.deliver()
            self.assertEqual(r.sender.call_args.args[0]['recipient'],email)
            self.assertEqual(r.sender.call_args.args[0]['sender'],MAIL['sender'])
        self.assertEqual(self.network.run.call_count,2)
        self.assertEqual(self.root.jobs(),[])

    def test_worker_honors_individual_schedule_and_continues_after_error(self):
        a, ar=self.spaces.get(self.alice['id']);b, br=self.spaces.get(self.bob['id'])
        a.put('schedule_enabled',True)
        with patch('app.accounts.now',return_value=datetime(2026,10,19,16,tzinfo=TZ)), patch.object(ar,'run',side_effect=RuntimeError()), patch.object(br,'run') as brun:
            self.spaces.tick()
            brun.assert_not_called()
        self.assertTrue(a.get('worker_error'))
        self.assertTrue(b.get('heartbeat'))

    def test_home_five_and_cursor_history(self):
        s,_=self.spaces.get(self.alice['id'])
        with s.connect() as db:
            for i in range(25):
                db.execute("INSERT INTO jobs(kind,day,created,status) VALUES('check','2026-10-19','2026-10-19T09:00:00+08:00','done')")
        self.assertEqual(len(self.a.get('/api/state').json['jobs']),5)
        first=self.a.get('/api/jobs').json;second=self.a.get('/api/jobs?before='+str(first['next_cursor'])).json
        self.assertEqual(len(first['jobs']),20);self.assertEqual(len(second['jobs']),5)
        self.assertIsNone(second['next_cursor'])
        self.assertFalse({j['id'] for j in first['jobs']} & {j['id'] for j in second['jobs']})
        self.assertEqual(self.a.get('/api/jobs?before=oops').status_code,400)
        self.assertNotIn(b'id="har-form"',self.a.get('/').data)
        self.assertIn(b'id="app-login-form"',self.a.get('/credentials').data)
        self.assertIn(b'id="mini-login-form"',self.a.get('/credentials').data)

    def test_user_manual_is_public_without_exposing_account_configuration(self):
        self.root.put('mail',MAIL)
        self.root.put('credential:app',credential())
        visitor=self.app.test_client()
        result=visitor.get('/help')
        self.assertEqual(result.status_code,200)
        text=result.get_data(as_text=True)
        self.assertIn('用户操作手册',text)
        self.assertIn('/static/help.js',text)
        self.assertNotIn('/static/app.js',text)
        for secret in (MAIL['sender'],MAIL['password'],'private-login-token',ADMIN,PASSWORD):
            self.assertNotIn(secret,text)
        self.assertIn('返回登录',text)
        self.assertIn('alice',self.a.get('/help').get_data(as_text=True))
        self.assertIn(b'href="/help"',visitor.get('/login').data)
        self.assertIn(b'href="/help"',self.a.get('/').data)
        self.assertEqual(visitor.get('/api/state').status_code,401)

    def test_operation_video_public_page_does_not_load_private_account_api(self):
        visitor=self.app.test_client()
        response=visitor.get('/video')
        self.assertEqual(response.status_code,200)
        text=response.get_data(as_text=True)
        self.assertIn('id="operation-video"',text)
        self.assertIn('preload="metadata"',text)
        self.assertIn('controls playsinline',text)
        self.assertNotIn('/static/app.js',text)
        self.assertNotIn('autoplay',text)
        self.assertIn(b'href="/video"',visitor.get('/login').data)
        self.assertIn(b'href="/video"',self.a.get('/').data)
        media=visitor.get('/static/tutorial/checkin-tutorial.mp4',headers={'Range':'bytes=0-31'})
        try:
            self.assertEqual(media.status_code,206)
            self.assertEqual(media.mimetype,'video/mp4')
            self.assertTrue(media.headers['Content-Range'].startswith('bytes 0-31/'))
            self.assertEqual(len(media.data),32)
        finally:
            media.close()
        self.assertEqual(visitor.get('/api/state').status_code,401)

    def test_email_verification_and_no_sender_control(self):
        self.root.put('mail',MAIL)
        with patch('app.send_mail',return_value=(True,'')) as send:
            r=self.post(self.a,'recipient/code',{'recipient':'alice@163.com','sender':'attacker@qq.com'})
            self.assertEqual(r.status_code,200)
            self.assertNotIn('code',r.json)
            self.assertEqual(send.call_args.args[0]['sender'],MAIL['sender'])
            code=re.search(r'\d{6}',send.call_args.args[2]).group()
        self.assertFalse(self.spaces.mail_config(self.alice['id']))
        self.assertEqual(self.post(self.b,'recipient/verify',{'recipient':'alice@163.com','code':code}).status_code,400)
        self.assertEqual(self.post(self.a,'recipient/verify',{'recipient':'alice@163.com','code':code}).status_code,200)
        self.assertEqual(self.spaces.mail_config(self.alice['id'])['recipient'],'alice@163.com')
        self.assertEqual(self.post(self.a,'recipient/verify',{'recipient':'alice@163.com','code':code}).status_code,400)
        self.assertEqual(self.post(self.a,'recipient/code',{'recipient':'else@163.com'}).status_code,400)
        self.assertNotIn(code,self.a.get('/api/state').get_data(as_text=True))

    def test_email_guess_limit_and_expiry(self):
        self.root.put('mail',MAIL)
        with patch('app.send_mail',return_value=(True,'')) as send:
            self.post(self.a,'recipient/code',{'recipient':'alice@163.com'})
            code=re.search(r'\d{6}',send.call_args.args[2]).group()
        for _ in range(5):
            self.assertEqual(self.post(self.a,'recipient/verify',{'recipient':'alice@163.com','code':'wrong'}).status_code,400)
        self.assertEqual(self.post(self.a,'recipient/verify',{'recipient':'alice@163.com','code':code}).status_code,400)
        s,_=self.spaces.get(self.alice['id']);pending=s.get('pending_email');pending.update(attempts=0,expires=0);s.put('pending_email',pending)
        self.assertEqual(self.post(self.a,'recipient/verify',{'recipient':'alice@163.com','code':code}).status_code,400)

    def test_email_change_keeps_previous_verified_address_until_confirmed(self):
        self.root.put('mail',MAIL)
        s,_=self.spaces.get(self.alice['id']);s.put('notifications',{'recipient':'old@163.com','verified':True})
        with patch('app.send_mail',return_value=(False,'smtp failed')):
            self.assertEqual(self.post(self.a,'recipient/code',{'recipient':'new@163.com'}).status_code,400)
        self.assertEqual(self.spaces.mail_config(self.alice['id'])['recipient'],'old@163.com')
        self.assertIsNone(s.get('pending_email'))

    def test_password_change_invalidates_all_sessions(self):
        another=self.login('alice',PASSWORD)
        self.assertEqual(self.post(self.a,'password',{'current_password':'wrong','password':'changed-password'}).status_code,400)
        self.assertEqual(self.post(self.a,'password',{'current_password':PASSWORD,'password':'changed-password'}).status_code,200)
        self.assertEqual(self.a.get('/api/state').status_code,401)
        self.assertEqual(another.get('/api/state').status_code,401)
        self.assertIsNone(self.accounts.authenticate('alice',PASSWORD))
        self.assertTrue(self.accounts.authenticate('alice','changed-password'))
        self.assertEqual(self.b.get('/api/state').status_code,200)

    def test_registration_control_and_csrf(self):
        self.assertEqual(self.post(self.admin,'admin/registration',{'enabled':False}).status_code,200)
        c=self.app.test_client();self.assertEqual(c.get('/register').status_code,403)
        self.assertEqual(self.a.get('/api/state').status_code,200)
        self.assertEqual(self.a.post('/api/recipient/code',json={'recipient':'a@qq.com'}).status_code,403)

    def test_retry_mail_is_tenant_scoped(self):
        for user in [self.alice,self.bob]:
            s,r=self.spaces.get(user['id']);r.run('manual')
            with s.connect() as db:db.execute("UPDATE outbox SET state='failed'")
        self.assertEqual(self.post(self.a,'mail/retry').status_code,200)
        self.assertEqual(self.spaces.get(self.alice['id'])[0].jobs()[0]['mail_state'],'pending')
        self.assertEqual(self.spaces.get(self.bob['id'])[0].jobs()[0]['mail_state'],'failed')

    def test_import_query_request_scoped_to_session(self):
        capture=entry();capture['request']['url']=capture['request']['url'].replace('/clock','/page-info')
        _,runner=self.spaces.get(self.alice['id'])
        with patch.object(runner,'submit',return_value=True) as submit:
            r=self.a.post('/api/import',data={'file':(io.BytesIO(json.dumps({'log':{'entries':[capture]}}).encode()),'query.har')},headers={'X-CSRF-Token':self.csrf(self.a)})
        self.assertEqual(r.status_code,200);submit.assert_called_once_with('check',target='app')
        self.assertTrue(self.spaces.get(self.alice['id'])[0].get('credential:app'))
        self.assertIsNone(self.spaces.get(self.bob['id'])[0].get('credential:app'))
        self.assertIsNone(self.root.get('credential:app'))
        self.assertEqual(self.network.run.call_count,0)

    def test_double_submission_is_rejected_before_starting_second_thread(self):
        _,runner=self.spaces.get(self.alice['id'])
        ready=threading.Event();release=threading.Event()
        original=runner.run
        def slow(*args,**kwargs):
            ready.set();release.wait(2);return original(*args,**kwargs)
        with patch.object(runner,'run',side_effect=slow):
            self.assertTrue(runner.submit('check'));self.assertTrue(ready.wait(1))
            self.assertFalse(runner.submit('manual'));release.set()
        for _ in range(100):
            if not runner.lock.locked():break
            time.sleep(.01)
        self.assertFalse(runner.lock.locked())

    def test_disabled_mail_cannot_enable_schedule(self):
        s,_=self.spaces.get(self.alice['id'])
        for k in ['app','mini']:s.put('credential:'+k,credential(k))
        self.root.put('mail',MAIL)
        self.assertEqual(self.post(self.a,'schedule',{'enabled':True}).status_code,400)
        s.put('notifications',{'recipient':'alice@qq.com','verified':True})
        self.assertEqual(self.post(self.a,'schedule',{'enabled':True}).status_code,200)
        self.assertFalse(self.spaces.get(self.bob['id'])[0].get('schedule_enabled',False))

    def test_rate_limit_survives_restart(self):
        self.assertTrue(self.accounts.allow('example',1,60))
        restarted=Accounts(self.root,ADMIN)
        self.assertFalse(restarted.allow('example',1,60))

    def test_password_reset_during_login_cannot_grant_new_session(self):
        from werkzeug.security import check_password_hash
        def racing_check(hashed, password):
            ok = check_password_hash(hashed, password)
            self.accounts.change_password(self.alice['id'], 'replacement-password')
            return ok
        with patch('app.accounts.check_password_hash', side_effect=racing_check):
            snapshot = self.accounts.authenticate('alice', PASSWORD)
        self.assertNotEqual(snapshot['version'], self.accounts.get(self.alice['id'])['version'])

    def test_damaged_workspace_does_not_stop_other_users(self):
        original = self.spaces.get
        def get(uid):
            if uid == self.alice['id']:
                raise OSError('unavailable workspace')
            return original(uid)
        with patch.object(self.spaces, 'get', side_effect=get):
            self.spaces.tick()
        self.assertTrue(original(self.bob['id'])[0].get('heartbeat'))

    def test_cli_reset_password_uses_selected_account_and_revokes_sessions(self):
        import manage
        from pathlib import Path
        with patch.object(manage, 'ROOT', Path(self.tmp.name)), patch.dict('os.environ', {'DATA_DIR':self.tmp.name,'ADMIN_PASSWORD':ADMIN}), patch('sys.argv',['manage.py','reset-password','alice']), patch('getpass.getpass',return_value='cli-reset-password'), patch('builtins.print'):
            manage.main()
        self.assertTrue(self.accounts.authenticate('alice','cli-reset-password'))
        self.assertEqual(self.a.get('/api/state').status_code,401)
        self.assertEqual(self.b.get('/api/state').status_code,200)


if __name__ == '__main__':
    unittest.main()
