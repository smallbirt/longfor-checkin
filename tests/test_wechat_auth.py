import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from app import create_app
from app.accounts import Accounts
from app.store import Store
from app.wechat_auth import CONFIG, WechatAuth
from app.mini_push import PLATFORM
from app.migrate import snapshot

ADMIN='wechat-test-admin-password'
PASSWORD='wechat-test-user-password'
OPENID='test-private-wechat-openid'
FORM={'appid':'wx5442ffd1e656a143','secret':'test-private-app-secret-123456','enabled':True}

class WechatAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,password=ADMIN,client=MagicMock())
        self.root=self.app.extensions['store']; self.accounts=self.app.extensions['accounts']
        self.service=self.app.extensions['wechat_auth'];self.service.client=MagicMock()
        self.service.client.exchange.return_value=OPENID
        self.alice=self.accounts.register('alice',PASSWORD);self.bob=self.accounts.register('bob',PASSWORD)
        self.service.save(FORM,'admin')
        self.client=self.app.test_client();self.client.get('/api/session')
        self.counter=0

    def tearDown(self):self.tmp.cleanup()

    def post(self,path,data,client=None):
        c=client or self.client
        csrf=c.get('/api/session').json['csrf']
        return c.post(path,json=data,headers={'X-CSRF-Token':csrf})

    def login(self,name='alice',client=None):
        return self.post('/api/session/login',{'username':name,'password':PASSWORD if name!='admin' else ADMIN},client)

    def wechat(self,client=None):
        self.counter+=1
        return self.post('/api/session/wechat',{'code':'fresh-code-'+str(self.counter)},client)

    def bind(self,name='alice',ticket=None,client=None):
        ticket=ticket or self.wechat(client).json['ticket']
        return self.post('/api/session/wechat/bind',{'ticket':ticket,'username':name,'password':PASSWORD},client)

    def test_first_login_requires_explicit_verified_account_and_rotates_session(self):
        before=self.client.get('/api/session').json['csrf']
        r=self.wechat();self.assertTrue(r.json['bind_required'])
        self.assertIsNone(self.client.get('/api/session').json['user'])
        self.assertEqual(self.client.get('/api/state').status_code,401)
        r=self.bind(ticket=r.json['ticket']);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['user']['username'],'alice');self.assertNotEqual(r.json['csrf'],before)
        self.assertTrue(self.client.get('/api/wechat/binding').json['bound'])
        self.assertEqual(self.client.get('/api/state').json['user']['username'],'alice')

    def test_bound_wechat_login_survives_restart_and_respects_disabled_user(self):
        self.bind();self.post('/api/session/logout',{})
        restarted=create_app(self.tmp.name,password=ADMIN,client=MagicMock())
        restarted.extensions['wechat_auth'].client=self.service.client
        c=restarted.test_client();r=self.wechat(c)
        self.assertEqual(r.status_code,200,r.json);self.assertFalse(r.json['bind_required'])
        self.assertEqual(r.json['user']['username'],'alice')
        self.accounts.revoke(self.alice['id'],'disabled')
        r=self.wechat(c);self.assertEqual(r.status_code,400)
        self.assertIsNone(c.get('/api/session').json['user'])

    def test_code_cannot_be_replayed_or_spoofed(self):
        r=self.post('/api/session/wechat',{'code':'one-code'});self.assertEqual(r.status_code,200)
        self.assertEqual(self.post('/api/session/wechat',{'code':'one-code'}).status_code,400)
        self.assertEqual(self.service.client.exchange.call_count,1)
        for payload in [{'code':'new','openid':OPENID},{'code':'new','user_id':self.alice['id']},{'code':None}]:
            self.assertEqual(self.post('/api/session/wechat',payload).status_code,400)
        self.assertEqual(self.service.client.exchange.call_count,1)

    def test_ticket_is_browser_bound_expiring_single_use_and_contains_no_openid(self):
        r=self.wechat();ticket=r.json['ticket']
        c=self.app.test_client()
        self.assertEqual(self.bind(ticket=ticket,client=c).status_code,400)
        with self.root.connect() as db:db.execute('UPDATE wechat_tickets SET expires=0')
        self.assertEqual(self.bind(ticket=ticket).status_code,400)
        ticket=self.wechat().json['ticket'];self.assertEqual(self.bind(ticket=ticket).status_code,200)
        self.assertEqual(self.bind(ticket=ticket).status_code,400)
        data=self.root.path.read_bytes()
        for value in [OPENID,ticket,FORM['secret'],'fresh-code-1']:self.assertNotIn(value.encode(),data)
        self.assertNotIn(OPENID,json.dumps(r.json))

    def test_one_to_one_in_both_directions_and_no_silent_reassignment(self):
        self.bind();other=self.app.test_client()
        self.login('bob',other)
        r=self.post('/api/wechat/binding',{'code':'bob-code','password':PASSWORD},other)
        self.assertEqual(r.status_code,400)
        self.service.client.exchange.return_value='another-private-openid'
        r=self.post('/api/wechat/binding',{'code':'alice-other-code','password':PASSWORD})
        self.assertEqual(r.status_code,400)
        with self.root.connect() as db:
            links=db.execute('SELECT user_id FROM wechat_links').fetchall()
        self.assertEqual([r['user_id'] for r in links],[self.alice['id']])

    def test_bad_password_csrf_and_cross_user_session_do_not_bind(self):
        ticket=self.wechat().json['ticket']
        self.assertEqual(self.post('/api/session/wechat/bind',{'ticket':ticket,'username':'alice','password':'wrong'}).status_code,400)
        self.login('bob')
        self.assertEqual(self.bind(ticket=ticket).status_code,400)
        self.assertEqual(self.client.post('/api/wechat/binding',json={'code':'fresh','password':PASSWORD}).status_code,403)
        with self.root.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM wechat_links').fetchone()['n'],0)

    def test_unbind_requires_password_revokes_other_sessions_and_audits(self):
        self.bind();other=self.app.test_client();self.wechat(other)
        self.assertEqual(self.post('/api/wechat/unbind',{'password':'wrong'}).status_code,400)
        r=self.post('/api/wechat/unbind',{'password':PASSWORD});self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.client.get('/api/state').status_code,200)
        self.assertEqual(other.get('/api/state').status_code,401)
        self.assertFalse(self.client.get('/api/wechat/binding').json['bound'])
        r=self.wechat(other);self.assertTrue(r.json['bind_required'])
        with self.root.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM admin_events WHERE kind='解除微信登录'").fetchone()['n'],1)

    def test_config_independent_of_push_and_secret_never_returned(self):
        self.root.put(PLATFORM,{'appid':FORM['appid'],'secret':FORM['secret'],'enabled':False})
        self.service.save({**FORM,'secret':''},'admin')
        self.login('admin')
        state=self.client.get('/api/admin/wechat-login')
        self.assertTrue(state.json['enabled']);self.assertNotIn('secret',state.json)
        self.assertNotIn(FORM['secret'],state.text)
        self.service.save({**FORM,'enabled':False},'admin')
        self.assertFalse(self.client.get('/api/session').json['wechat_login_enabled'])
        self.assertEqual(self.wechat().status_code,400)
        self.assertTrue(self.accounts.authenticate('alice',PASSWORD))

    def test_config_changes_invalidate_tickets_and_cannot_switch_linked_appid(self):
        ticket=self.wechat().json['ticket']
        self.service.save(FORM,'admin')
        self.assertEqual(self.bind(ticket=ticket).status_code,400)
        self.bind()
        with self.assertRaises(ValueError):self.service.save({**FORM,'appid':'wx1111111111111111'},'admin')

    def test_notification_binding_does_not_implicitly_grant_login(self):
        identity=self.service.digest(FORM['appid']+':'+OPENID)
        with self.root.connect() as db:db.execute('INSERT INTO mini_push_owners(identity,user_id) VALUES(?,?)',(identity,self.bob['id']))
        r=self.wechat();self.assertTrue(r.json['bind_required'])
        self.assertEqual(self.bind(ticket=r.json['ticket']).status_code,400)
        self.assertEqual(self.bind('bob').status_code,200)

    def test_audit_failure_rolls_back_binding_and_ticket_consumption(self):
        ticket=self.wechat().json['ticket']
        with patch.object(self.accounts,'record_event',side_effect=RuntimeError('audit failed')):
            self.assertEqual(self.bind(ticket=ticket).status_code,500)
        with self.root.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM wechat_links').fetchone()['n'],0)
        self.assertEqual(self.bind(ticket=ticket).status_code,200)
        rows=snapshot(self.tmp.name)[0][1][1]
        self.assertEqual(len(rows['wechat_links']),1)

    def test_concurrent_claims_have_one_owner_across_store_instances(self):
        other_root=Store(self.tmp.name,database_url='')
        other=WechatAuth(other_root,Accounts(other_root,ADMIN));config=self.service.config()
        identity=self.service.digest(FORM['appid']+':'+OPENID)
        barrier=threading.Barrier(2);results=[]
        def claim(service,user):
            barrier.wait()
            try:service.bind(identity,config,user);results.append('ok')
            except ValueError:results.append('conflict')
        threads=[threading.Thread(target=claim,args=(self.service,self.alice)),threading.Thread(target=claim,args=(other,self.bob))]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertCountEqual(results,['ok','conflict'])

    def test_admin_configuration_is_protected_from_guests_and_ordinary_users(self):
        self.assertEqual(self.client.get('/api/admin/wechat-login').status_code,401)
        self.login()
        self.assertEqual(self.client.get('/api/admin/wechat-login').status_code,403)
        self.assertEqual(self.post('/api/admin/wechat-login',FORM).status_code,403)
        self.login('admin')
        self.assertEqual(self.client.post('/api/admin/wechat-login',json=FORM).status_code,403)
        self.assertEqual(self.post('/api/admin/wechat-login',{**FORM,'openid':OPENID}).status_code,400)

    def test_push_cannot_claim_a_different_identity_after_login_binding(self):
        from app.program_accounts import ProgramAccounts
        from test_mini_push import FORM as PUSH_FORM
        self.bind()
        self.root.put(PLATFORM,PUSH_FORM)
        store,_=self.app.extensions['workspaces'].get(self.alice['id'])
        account=ProgramAccounts(store).create('mini','test account')
        push=self.app.extensions['workspaces'].push;push.client=MagicMock()
        push.client.exchange.return_value='another-private-wechat-openid'
        with self.assertRaises(ValueError):push.prepare(store,account['id'],'different-code')
        with self.root.connect() as db:self.assertEqual(db.execute('SELECT count(*) n FROM mini_push_owners').fetchone()['n'],0)

if __name__=='__main__':unittest.main()
