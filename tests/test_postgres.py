"""Real PostgreSQL tests. Set PG_TEST_DSN to an isolated, disposable dedicated database."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from app.entitlements import Wallet
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from app import create_app
from app.accounts import Accounts, Workspaces
from app.constants import TZ
from app.database import PostgreSQL
from app.migrate import migrate, snapshot
from app.runner import Runner
from app.store import Store, now
from app.mini_push import MiniPush, PLATFORM, validate_config
from app.program_accounts import ProgramAccounts, filtered_jobs
from test_core import credential

URL=os.environ.get('PG_TEST_DSN')
ADMIN='pg-test-admin-password-2026'
MAIL={'provider':'qq','sender':'fixture@qq.com','recipient':'admin@163.com','password':'fixture-smtp-password'}


@unittest.skipUnless(URL,'PG_TEST_DSN not configured')
class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(URL,autocommit=True) as db:
            marker=db.execute('SELECT value FROM public.longfor_test_marker WHERE id=1').fetchone()
            if not marker or marker[0]!='isolated-test-only':
                raise RuntimeError('Refusing to reset a database without the disposable test marker')
            for (name,) in db.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'checkin_u%'"):
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
        self.tmp=tempfile.TemporaryDirectory();self.open=[]
        self.client=MagicMock();self.client.run.side_effect=lambda *a,**k:{'state':'signed','message':'confirmed'}

    def tearDown(self):
        for root in self.open:
            root.backend.close()
        self.tmp.cleanup()

    def root(self):
        store=Store(self.tmp.name,database_url=URL);self.open.append(store);return store

    def legacy(self):
        root=Store(self.tmp.name,database_url='');accounts=Accounts(root,ADMIN)
        alice=accounts.register('alice','alice-password-2026')
        root.put('mail',MAIL);root.put('credential:app',credential());root.put('schedule_enabled',True)
        child=root.child(alice['id']);child.put('credential:mini',credential('mini'))
        child.put('notifications',{'recipient':'alice@qq.com','verified':True})
        runner=Runner(root,client=self.client);runner.run('morning',dt=datetime(2026,10,19,9,10,tzinfo=TZ))
        return root,accounts,alice

    def test_sql_operations_sequences_and_search_path_isolation(self):
        root=self.root();a=Accounts(root,ADMIN);user=a.register('alice','alice-password-2026')
        self.assertEqual(user['id'],2)
        child=root.child(user['id']);root.put('example','admin');child.put('example','alice')
        self.assertEqual(root.get('example'),'admin');self.assertEqual(child.get('example'),'alice')
        root.put('example','updated');self.assertEqual(root.get('example'),'updated')
        self.assertTrue(a.allow('key',1,60));self.assertFalse(a.allow('key',1,60))
        with self.assertRaises(ValueError):a.register('alice','alice-password-2026')
        self.assertTrue(a.authenticate('alice','alice-password-2026'))

    def test_multi_account_registry_schedule_and_archive_on_postgres(self):
        root = self.root(); Accounts(root, ADMIN)
        registry = ProgramAccounts(root)
        first, second = registry.create('app', '主号'), registry.create('app', '家人号')
        for a in (first, second):
            scoped = registry.scoped(a['id'])
            cred = credential(); cred['headers']['token'] = 'private-' + a['id']
            scoped.put('credential:app', cred)
            registry.update(a['id'], {'enabled': True})
        runner = Runner(root, self.client)
        dt = datetime(2026, 10, 23, 9, 10, tzinfo=TZ)
        runner.tick(dt); runner.tick(dt)
        self.assertEqual(len(root.jobs()), 2)
        rows = filtered_jobs(root, account_id=second['id'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['results'][0]['account_id'], second['id'])
        registry.archive(first['id'])
        self.assertIsNone(registry.get(first['id']))
        self.assertTrue(registry.scoped(second['id']).get('credential:app'))
        with root.connect() as db:
            text = ''.join(r['value'] for r in db.execute('SELECT value FROM settings'))
        self.assertNotIn('private-' + second['id'], text)

    def test_user_wide_failure_push_preserves_legacy_grant_and_tenant_isolation(self):
        from test_mini_push import FORM, OPENID
        root = self.root(); users = Accounts(root, ADMIN)
        alice = users.register('alice', 'alice-password-2026')
        bob = users.register('bob', 'bob-password-2026')
        root.put(PLATFORM, validate_config(FORM))
        store = root.child(alice['id']); other_store = root.child(bob['id'])
        registry = ProgramAccounts(store)
        first = registry.create('app', '主号'); second = registry.create('app', '第二账号')
        for account in (first, second):
            registry.scoped(account['id']).put('credential:app', credential())
        transport = MagicMock(); transport.exchange.return_value = OPENID
        transport.send.return_value = ('sent', '')
        push = MiniPush(root, transport)
        intent = push.prepare(store, code='fresh-code'); push.accept(store, intent['nonce'])
        with store.connect() as db:
            db.execute('UPDATE mini_push_subscriptions SET account_id=?', (first['id'],))
        runner = Runner(store, self.client, mini_push=push)
        runner.run('afternoon', target=first['id'], dt=now() + timedelta(seconds=1))
        runner.deliver(); transport.send.assert_not_called()
        registry.archive(first['id'])
        self.assertEqual(push.public(store)['subscriptions'], [{'account_id': '*', 'remaining': 1}])
        self.client.run.side_effect = lambda *a, **k: {'state': 'auth_expired', 'message': '登录失效'}
        runner.run('afternoon', target=second['id'], dt=now() + timedelta(seconds=1))
        runner.deliver(); runner.deliver(); transport.send.assert_called_once()
        self.assertEqual(push.public(store)['subscriptions'], [])
        self.assertEqual(push.public(other_store)['subscriptions'], [])

    def test_concurrent_instance_updates_use_database_serialization(self):
        root = self.root(); Accounts(root, ADMIN)
        second = Store(self.tmp.name, database_url=URL, backend=root.backend)
        registries = [ProgramAccounts(root), ProgramAccounts(second)]
        errors = []
        def create(i):
            try: registries[i % 2].create('mini', '账号' + str(i))
            except Exception as exc: errors.append(type(exc).__name__)
        threads = [threading.Thread(target=create, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(registries[0].list()), 10)

    def test_multi_account_keys_survive_sqlite_to_pg_migration(self):
        old, _, alice = self.legacy()
        owner = old.child(alice['id'])
        registry = ProgramAccounts(owner)
        a = registry.create('app', '新增 App')
        scoped = registry.scoped(a['id']); scoped.put('credential:app', credential())
        registry.update(a['id'], {'sign_time': '10:15', 'check_time': '17:30'})
        migrate(self.tmp.name, URL)
        pg = self.root(); current = ProgramAccounts(pg.child(alice['id']))
        self.assertEqual(current.require(a['id'])['sign_time'], '10:15')
        self.assertEqual(current.scoped(a['id']).get('credential:app'), credential())
        self.assertTrue(current.scoped('mini').get('credential:mini'))

    def test_runner_dedupe_outbox_and_restart(self):
        root=self.root();Accounts(root,ADMIN);root.put('credential:app',credential());root.put('mail',MAIL)
        runner=Runner(root,client=self.client,sender=MagicMock(return_value=(True,'')))
        dt=datetime(2026,10,19,9,10,tzinfo=TZ)
        runner.run('morning',dt=dt);runner.run('morning',dt=dt);runner.deliver()
        self.assertEqual(len(root.jobs()),1);self.assertEqual(root.jobs()[0]['mail_state'],'sent')
        self.assertEqual(self.client.run.call_count,1)
        runner.run('afternoon',dt=dt.replace(hour=16));self.assertEqual(len(root.jobs()),2)
        self.assertEqual(len(root.jobs(before=root.jobs()[0]['id'])),1)

    def test_atomic_migration_verifies_all_tables_and_preserves_keys(self):
        old,accounts,alice=self.legacy();before,fingerprint=snapshot(self.tmp.name)
        bytes_before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(self.tmp.name).rglob('*') if p.is_file() and p.name!='scheduler.lock'}
        result=migrate(self.tmp.name,URL);self.assertFalse(result['already_migrated'])
        root=self.root();new=Accounts(root,ADMIN)
        self.assertEqual(root.get('credential:app'),credential())
        self.assertEqual(root.get('mail'),MAIL);self.assertTrue(root.get('schedule_enabled'))
        self.assertTrue(new.authenticate('alice','alice-password-2026'))
        self.assertEqual(root.child(alice['id']).get('credential:mini'),credential('mini'))
        self.assertEqual(root.jobs()[0]['mail_state'],'pending')
        self.assertEqual(root.jobs()[0]['id'],old.jobs()[0]['id'])
        later=new.register('later_user','later-password-2026');self.assertGreater(later['id'],alice['id'])
        runner=Runner(root,client=self.client);runner.run('check');self.assertGreater(root.jobs()[0]['id'],old.jobs()[0]['id'])
        for p,digest in bytes_before.items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),digest)
        self.assertEqual(snapshot(self.tmp.name)[1],fingerprint)

    def test_migration_is_idempotent_without_overwriting_new_pg_writes(self):
        self.legacy();migrate(self.tmp.name,URL);root=self.root();root.put('only_in_pg','keep')
        self.assertTrue(migrate(self.tmp.name,URL)['already_migrated'])
        self.assertEqual(root.get('only_in_pg'),'keep')

    def test_single_user_legacy_without_accounts_table_upgrades_directly(self):
        old=Store(self.tmp.name,database_url='')
        old.put('credential:app',credential());old.put('mail',MAIL)
        migrate(self.tmp.name,URL)
        root=self.root();accounts=Accounts(root,ADMIN)
        self.assertEqual(accounts.ids(),[1])
        self.assertTrue(accounts.authenticate('admin',ADMIN))
        self.assertEqual(root.get('credential:app'),credential())
        self.assertEqual(accounts.register('first_user','first-user-password')['id'],2)

    def test_changed_source_is_rejected_after_migration(self):
        old,_,_=self.legacy();migrate(self.tmp.name,URL);old.put('new_source_setting','changed')
        with self.assertRaisesRegex(ValueError,'不同'):migrate(self.tmp.name,URL)

    def test_no_implicit_empty_start_and_no_overwrite_of_used_target(self):
        self.legacy()
        with self.assertRaisesRegex(RuntimeError,'migrate-pg'):self.root()
        root=Store(self.tmp.name,database_url=URL,migrating=True);self.open.append(root);root.put('existing','do-not-overwrite')
        with self.assertRaisesRegex(ValueError,'已有业务数据'):migrate(self.tmp.name,URL)
        self.assertEqual(root.get('existing'),'do-not-overwrite')

    def test_migration_rolls_back_all_inserted_rows_on_readback_failure(self):
        self.legacy()
        from app.database import Session
        original=Session.reset_sequence
        def fail(db,table):
            if table=='jobs':raise ValueError('intentional readback failure')
            return original(db,table)
        with patch.object(Session,'reset_sequence',fail):
            with self.assertRaises(ValueError):migrate(self.tmp.name,URL)
        root=Store(self.tmp.name,database_url=URL,migrating=True);self.open.append(root)
        with root.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM settings').fetchone()['n'],0)
            self.assertIsNone(db.execute("SELECT value FROM storage_meta WHERE key='sqlite_import_complete'").fetchone())
        self.assertFalse(migrate(self.tmp.name,URL)['already_migrated'])

    def test_wrong_encryption_key_fails_closed(self):
        root=self.root();root.put('credential:app',credential())
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaisesRegex(RuntimeError,'密钥'):Store(other,database_url=URL)
        self.assertEqual(root.get('credential:app'),credential())

    def test_scheduler_database_lease_blocks_different_volume_instances(self):
        root=self.root();root.backend.acquire_scheduler()
        with tempfile.TemporaryDirectory() as other:
            shutil.copyfile(root.root/'secret.key',Path(other)/'secret.key')
            second=Store(other,database_url=URL)
            try:
                with self.assertRaisesRegex(RuntimeError,'调度器'):second.backend.acquire_scheduler()
            finally:second.backend.close()
        root.backend.check_scheduler()

    def test_concurrent_pool_reuse_never_crosses_tenants(self):
        root=self.root();Accounts(root,ADMIN);one=root.child(2);two=root.child(3)
        one.put('tenant','one');two.put('tenant','two');errors=[]
        def read(store,value):
            try:
                for _ in range(25):
                    if store.get('tenant')!=value:errors.append('crossed')
            except Exception:errors.append('query failed')
        threads=[threading.Thread(target=read,args=(one,'one')),threading.Thread(target=read,args=(two,'two'))]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(errors,[])
        self.assertLessEqual(root.backend.pool.get_stats()['pool_size'],5)

    def test_postgres_web_registration_permissions_and_isolated_state(self):
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client)
        root=app.extensions['store'];self.open.append(root);c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        response=c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        self.assertEqual(response.status_code,302)
        self.assertEqual(c.get('/api/state').json['user']['username'],'alice')
        self.assertEqual(c.get('/api/admin').status_code,403)
        self.assertEqual(c.get('/health').json['storage'],'postgresql')
        self.assertEqual(c.get('/api/jobs').json['jobs'],[])

    def test_sms_profile_and_login_renewal_in_postgres_user_workspace(self):
        import io
        from test_login import login_entry, har, PROFILE, PHONE, TOKEN
        sms=MagicMock();sms.verify.return_value=TOKEN
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client,login_client=sms)
        root=app.extensions['store'];self.open.append(root);c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']};uid=s['user_id']
        r=c.post('/api/import',data={'file':(io.BytesIO(har(login_entry(),login_entry('mini'))),'own.har')},headers=headers)
        self.assertEqual(r.status_code,200,r.json)
        child,_=app.extensions['workspaces'].get(uid)
        self.assertEqual(child.get('login-profile:app'),PROFILE)
        self.assertIsNone(root.get('login-profile:app'))
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app'},headers=headers).status_code,200)
        self.assertEqual(c.post('/api/login/sms/verify',json={'code':'012345'},headers=headers).status_code,200)
        self.assertIsNone(child.get('pending_sms'))
        self.assertEqual(child.get('credential:app')['headers']['token'],TOKEN)
        self.assertEqual(child.get('credential:mini')['headers']['token'],TOKEN)
        self.assertIsNone(root.get('credential:app'))
        with child.connect() as db:
            rows=db.execute('SELECT value FROM settings').fetchall()
        ciphertext=''.join(row['value'] for row in rows)
        for value in [PHONE,TOKEN,PROFILE['fingerprint'],'012345']:
            self.assertNotIn(value,ciphertext)

    def test_automatic_device_after_first_import_and_isolated_encrypted_storage(self):
        import io
        from test_login import har, login_entry
        from test_device import DEVICE,PAYLOAD,UA
        from test_login import PHONE,TOKEN
        device=MagicMock();device.collect.return_value={'status':2,'data':DEVICE,'msg':'success'}
        sms=MagicMock();sms.verify.return_value=TOKEN
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client,login_client=sms,device_client=device)
        root=app.extensions['store'];self.open.append(root);c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']};uid=s['user_id']
        setup=c.post('/api/device/start',json={},headers=headers).json
        route=setup['frame_url'].replace('/device/frame/','/device/collect/')+'/udid/c1'
        self.assertEqual(c.get(route,headers={'Param':PAYLOAD,'User-Agent':UA}).status_code,200)
        self.assertEqual(c.post('/api/device/finish',json={'ticket':setup['ticket'],'token':DEVICE},headers=headers).status_code,200)
        child,_=app.extensions['workspaces'].get(uid)
        self.assertIsNone(root.get('automatic_device'));self.assertIsNone(child.get('login-profile:app'))
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app','device_mode':'automatic'},headers=headers).status_code,400)
        response=c.post('/api/import',data={'channel':'app','file':(io.BytesIO(har(login_entry())),'app-login.har')},headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app','device_mode':'automatic'},headers=headers).status_code,200)
        self.assertEqual(c.post('/api/login/sms/verify',json={'code':'012345'},headers=headers).status_code,200)
        self.assertIsNone(child.get('credential:mini'))
        self.assertEqual(child.get('credential:app')['headers']['x-lf-dxrisk-source'],'3')
        self.assertIsNone(root.get('credential:app'))
        with child.connect() as db:values=''.join(row['value'] for row in db.execute('SELECT value FROM settings').fetchall())
        for private in (DEVICE,PHONE,TOKEN):self.assertNotIn(private,values)

    def test_shared_default_device_pg_keeps_owner_auth_out_of_user_workspace(self):
        import io
        from test_login import PHONE, TOKEN, har, login_entry
        from test_shared_device import seed_owner
        from app import shared_device
        sms=MagicMock();sms.verify.return_value=TOKEN
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client,login_client=sms)
        root=app.extensions['store'];self.open.append(root)
        seed_owner(root);shared_device.configure(root,True)
        c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']};uid=s['user_id']
        child,_=app.extensions['workspaces'].get(uid)
        self.assertTrue(c.get('/api/state').json['login_methods']['app']['shared_available'])
        self.assertIsNone(child.get('login-profile:app'))
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app','device_mode':'shared'},headers=headers).status_code,400)
        response=c.post('/api/import',data={'channel':'app','file':(io.BytesIO(har(login_entry())),'app-login.har')},headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app','device_mode':'shared'},headers=headers).status_code,200)
        r=c.post('/api/login/sms/verify',json={'code':'012345'},headers=headers)
        self.assertEqual(r.status_code,200,r.json)
        for channel in ('app',):
            cred=child.get('credential:'+channel)
            self.assertEqual(cred['headers']['token'],TOKEN)
            self.assertEqual(cred['headers']['x-lf-dxrisk-token'],channel+'-owner-device')
            self.assertNotIn('owner-auth-secret',json.dumps(cred))
            self.assertEqual(root.get('credential:'+channel)['headers']['token'],'owner-auth-secret')
        with child.connect() as db:values=''.join(row['value'] for row in db.execute('SELECT value FROM settings').fetchall())
        for private in (PHONE,TOKEN,'app-owner-device'):self.assertNotIn(private,values)

    def test_preferred_devices_pg_import_fills_native_gap_and_keeps_personal_override(self):
        import io
        from test_login import PHONE, TOKEN, PROFILE
        from test_shared_device import seed_owner
        from app import shared_device
        sms=MagicMock();sms.verify.return_value=TOKEN
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client,login_client=sms)
        root=app.extensions['store'];self.open.append(root)
        seed_owner(root);root.put('login-profile:app',None);shared_device.sync(root)
        admin=app.test_client();admin.get('/login')
        with admin.session_transaction() as s:csrf=s['csrf']
        admin.post('/login',data={'username':'admin','password':ADMIN,'csrf':csrf})
        with admin.session_transaction() as s:admin_headers={'X-CSRF-Token':s['csrf']}
        c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']};uid=s['user_id']
        child,_=app.extensions['workspaces'].get(uid);child.put('credential:app',credential())
        self.assertFalse(c.get('/api/state').json['login_methods']['app']['effective_sms_available'])
        snapshot=root.get(shared_device.KEY)
        bundle=json.dumps({'version':1,'profile':PROFILE,'devices':snapshot['devices']}).encode()
        r=admin.post('/api/admin/shared-device/import',data={'file':(io.BytesIO(bundle),'device.json')},headers=admin_headers)
        self.assertEqual(r.status_code,200,r.json)
        methods=c.get('/api/state').json['login_methods']['app']
        self.assertTrue(methods['effective_sms_available'])
        self.assertEqual(methods['device_sources'],{'app':'personal','mini':'platform'})
        self.assertEqual(c.post('/api/login/sms/send',json={'phone':PHONE,'target':'app','device_mode':'preferred'},headers=headers).status_code,200)
        self.assertEqual(c.post('/api/login/sms/verify',json={'code':'012345'},headers=headers).status_code,200)
        self.assertEqual(child.get('credential:app')['headers']['x-lf-dxrisk-token'],'private-device-token')
        self.assertIsNone(child.get('credential:mini'))
        self.assertIsNone(child.get('login-profile:app'))
        self.assertEqual(root.get('credential:app')['headers']['token'],'owner-auth-secret')
        with root.connect() as db:values=''.join(row['value'] for row in db.execute('SELECT value FROM settings').fetchall())
        self.assertNotIn(PROFILE['fingerprint'],values)

    def test_native_mini_sms_experiment_pg_preserves_app_and_encrypts_challenge(self):
        from test_login import PROFILE, PHONE, TOKEN
        from test_shared_device import seed_owner
        from app import shared_device
        mini=MagicMock();mini.verify.return_value=TOKEN
        with patch.dict(os.environ,{'DATABASE_URL':URL}):
            app=create_app(self.tmp.name,password=ADMIN,client=self.client,mini_login_client=mini)
        app.config['MINI_SMS_EXPERIMENTAL']=True
        root=app.extensions['store'];self.open.append(root)
        seed_owner(root);root.put('login-profile:mini',PROFILE);shared_device.sync(root)
        c=app.test_client();c.get('/register')
        with c.session_transaction() as s:csrf=s['csrf']
        c.post('/register',data={'username':'alice','password':'alice-password-2026','confirm_password':'alice-password-2026','csrf':csrf})
        with c.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']};uid=s['user_id']
        child,_=app.extensions['workspaces'].get(uid);before=credential();child.put('credential:app',before)
        response=c.post('/api/login/mini/send',json={'phone':PHONE},headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(child.get('pending_mini_sms')['phone'],PHONE)
        with child.connect() as db:values=''.join(row['value'] for row in db.execute('SELECT value FROM settings').fetchall())
        for private in (PHONE,PROFILE['fingerprint']):self.assertNotIn(private,values)
        response=c.post('/api/login/mini/verify',json={'code':'012345'},headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(child.get('credential:app'),before)
        self.assertEqual(child.get('credential:mini')['headers']['token'],TOKEN)
        self.assertEqual(child.get('credential:mini')['headers']['x-lf-dxrisk-token'],'mini-owner-device')
        self.assertIsNone(child.get('pending_mini_sms'))
        self.assertEqual(root.get('credential:mini')['headers']['token'],'owner-auth-secret')
        with child.connect() as db:values=''.join(row['value'] for row in db.execute('SELECT value FROM settings').fetchall())
        for private in (PHONE,TOKEN,'012345'):self.assertNotIn(private,values)
        self.assertTrue(all(not call.kwargs['allow_sign'] for call in self.client.run.call_args_list))

    def test_credits_atomic_purchase_quota_and_restart(self):
        from app.accounts import Accounts, Workspaces
        root=self.root(); accounts=Accounts(root, 'isolated-test-admin-password')
        alice=accounts.register('wallet_user', 'isolated-test-password')
        store=Workspaces(root,accounts,self.client).get(alice['id'])[0]
        wallet=Wallet(store)
        self.assertEqual(wallet.public()['total'],2)
        wallet.change('grant',10,'test credit','admin',uuid.uuid4().hex)
        roots=[Store(store.root,database_url=os.environ['PG_TEST_DSN'],user_id=alice['id']) for _ in range(2)]
        self.open.extend(roots)
        barrier=threading.Barrier(2); results=[]
        def buy(i):
            barrier.wait()
            try: Wallet(roots[i]).change('purchase',10,'test purchase','alice',uuid.uuid4().hex); results.append('ok')
            except ValueError: results.append('insufficient')
        ts=[threading.Thread(target=buy,args=(i,)) for i in range(2)]
        for t in ts:t.start()
        for t in ts:t.join(15); self.assertFalse(t.is_alive())
        self.assertCountEqual(results,['ok','insufficient'])
        self.assertEqual(wallet.public(),{'points':0,'free':2,'purchased':1,'total':3,'used':0,'price':10})
        registry=ProgramAccounts(store)
        for i in range(3):
            a=registry.create('app','test '+str(i)); registry.update(a['id'],{'enabled':True})
        extra=registry.create('app','over quota')
        with self.assertRaises(ValueError):registry.update(extra['id'],{'enabled':True})
        self.assertEqual(Wallet(roots[0]).public()['used'],3)
        self.assertEqual(len(Wallet(roots[1]).ledger()),2)

    def test_credit_retry_and_ledger_failure_are_atomic(self):
        from app.accounts import Accounts, Workspaces
        from app.database import Session
        from unittest.mock import patch
        root=self.root(); accounts=Accounts(root,'isolated-test-admin-password')
        alice=accounts.register('retry_user','isolated-test-password')
        store=Workspaces(root,accounts,self.client).get(alice['id'])[0]; wallet=Wallet(store)
        key=uuid.uuid4().hex
        for i in range(3):wallet.change('grant',10,'same request','admin',key)
        self.assertEqual(wallet.public()['points'],10)
        original=Session.execute
        def fail(db,sql,params=()):
            if sql.startswith('INSERT INTO credit_ledger'):raise RuntimeError('simulated failure')
            return original(db,sql,params)
        with patch.object(Session,'execute',fail),self.assertRaises(RuntimeError):
            wallet.change('purchase',10,'test rollback','alice',uuid.uuid4().hex)
        self.assertEqual(wallet.public()['points'],10)
        self.assertEqual(wallet.public()['purchased'],0)
        self.assertEqual(len(wallet.ledger()),1)

    def test_wechat_one_to_one_claim_is_serialized_and_unbind_revokes_sessions(self):
        from app.wechat_auth import WechatAuth
        root=self.root(); accounts=Accounts(root,ADMIN)
        alice=accounts.register('wx_alice','wechat-test-password')
        bob=accounts.register('wx_bob','wechat-test-password')
        auth=WechatAuth(root,accounts)
        auth.save({'appid':'wx5442ffd1e656a143','secret':'isolated-test-secret-20261027','enabled':True},'admin')
        config=auth.config();identity=auth.digest(config['appid']+':isolated-wechat-identity')
        second=Store(self.tmp.name,database_url=URL);self.open.append(second)
        other=WechatAuth(second,Accounts(second,ADMIN))
        barrier=threading.Barrier(2);results=[]
        def bind(service,user):
            barrier.wait()
            try:service.bind(identity,config,user);results.append(('ok',user['id']))
            except ValueError:results.append(('conflict',user['id']))
        ts=[threading.Thread(target=bind,args=(auth,alice)),threading.Thread(target=bind,args=(other,bob))]
        for t in ts:t.start()
        for t in ts:t.join(15);self.assertFalse(t.is_alive())
        self.assertCountEqual([r[0] for r in results],['ok','conflict'])
        owner=next(uid for status,uid in results if status=='ok')
        before=accounts.get(owner);after=auth.unbind(before)
        self.assertEqual(after['version'],before['version']+1)
        self.assertFalse(auth.status(owner)['bound'])

    def test_wechat_links_and_pending_tickets_survive_sqlite_migration(self):
        from app.wechat_auth import WechatAuth
        old,accounts,alice=self.legacy();auth=WechatAuth(old,accounts)
        auth.save({'appid':'wx5442ffd1e656a143','secret':'isolated-test-secret-20261027','enabled':True},'admin')
        config=auth.config();identity=auth.digest(config['appid']+':isolated-wechat-identity')
        auth.bind(identity,config,alice)
        browser=auth.digest('test-browser')
        _,token=auth.owner_or_ticket(auth.digest('different-wechat-identity'),config,browser)
        migrate(self.tmp.name,URL)
        pg=self.root();loaded=WechatAuth(pg,Accounts(pg,ADMIN))
        self.assertTrue(loaded.status(alice['id'])['bound'])
        with pg.connect() as db:
            self.assertEqual(db.execute('SELECT digest FROM wechat_tickets').fetchone()['digest'],loaded.digest(token))
            self.assertEqual(db.execute('SELECT user_id FROM wechat_links').fetchone()['user_id'],alice['id'])
