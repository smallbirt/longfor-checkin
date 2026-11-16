import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from app import create_app
from app.constants import TZ
from app.program_accounts import ProgramAccounts, due_account, filtered_jobs, schedule_times
from app.runner import Runner
from app.store import Store
from test_core import credential, entry


class ProgramAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.network = MagicMock()
        self.network.run.side_effect = lambda *a, **kw: {'state': 'unsigned', 'message': '未签到'}
        self.app = create_app(self.tmp.name, password='test-admin-password-long', client=self.network)
        self.root = self.app.extensions['store']
        users = self.app.extensions['accounts']
        self.alice = users.register('alice', 'own-test-password')
        self.bob = users.register('bob', 'own-test-password')
        self.store, self.runner = self.app.extensions['workspaces'].get(self.alice['id'])
        self.registry = ProgramAccounts(self.store)
        self.a = self.login('alice')
        self.b = self.login('bob')

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, name):
        c = self.app.test_client(); c.get('/login')
        c.post('/login', data={'username': name, 'password': 'own-test-password', 'csrf': self.csrf(c)})
        return c

    def csrf(self, c):
        with c.session_transaction() as s: return s['csrf']

    def post(self, path, payload=None, c=None):
        c = c or self.a
        return c.post('/api/' + path, json=payload or {}, headers={'X-CSRF-Token': self.csrf(c)})

    def create(self, channel='app', alias='家人号'):
        r = self.post('accounts', {'channel': channel, 'alias': alias})
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['account']

    def imported(self, a):
        scoped = self.registry.scoped(a['id'])
        cred = credential(a['channel']); cred['headers']['token'] = 'token-' + a['id']
        scoped.put('credential:' + a['channel'], cred)
        return scoped

    def mail(self):
        self.root.put('mail', {'provider': 'qq', 'sender': 'demo@qq.com', 'password': 'fake', 'recipient': 'demo@qq.com'})
        self.store.put('notifications', {'recipient': 'alice@qq.com', 'verified': True})

    def test_legacy_upgrade_idempotent_without_credential_copy(self):
        old = credential(); self.store.put('credential:app', old); self.store.put('schedule_enabled', True)
        self.assertEqual(self.registry.list()[0]['id'], 'app')
        self.assertTrue(self.registry.list()[0]['enabled'])
        self.assertEqual(len(self.registry.list()), 1)
        self.assertEqual(self.registry.scoped('app').get('credential:app'), old)
        self.assertIsNone(self.registry.scoped('app').get('credential:mini'))
        self.assertEqual(self.store.get('credential:app'), old)

    def test_same_type_instances_independent_and_encrypted(self):
        x, y = self.create(), self.create(alias='另一个')
        sx, sy = self.imported(x), self.imported(y)
        self.assertNotEqual(sx.get('credential:app'), sy.get('credential:app'))
        self.assertIsNone(self.store.get('credential:app'))
        for a in (x, y): self.assertNotIn(('token-' + a['id']).encode(), self.store.path.read_bytes())
        sx.put('pending_sms', {'phone': 'private-phone', 'attempts': 1})
        self.assertIsNone(sy.get('pending_sms'))

    def test_concurrent_creates_do_not_lose_accounts(self):
        errors = []
        def create(i):
            try: self.registry.create('app', str(i))
            except Exception as exc: errors.append(str(exc))
        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.registry.list()), 8)

    def test_foreign_ids_denied_on_all_entrypoints(self):
        a = self.create(); self.imported(a)
        for path in ('state?account_id=' + a['id'], 'jobs?account_id=' + a['id']):
            self.assertEqual(self.b.get('/api/' + path).status_code, 400)
        self.assertEqual(self.b.get('/accounts/' + a['id']).status_code, 400)
        self.assertEqual(self.b.get('/credentials?account_id=' + a['id']).status_code, 400)
        for path, payload in [('accounts/' + a['id'], {'alias': 'hijack'}), ('accounts/' + a['id'] + '/remove', {}), ('run', {'kind': 'manual', 'account_id': a['id']}), ('login/app/send?account_id=' + a['id'], {'phone': '13800000000'})]:
            self.assertEqual(self.post(path, payload, self.b).status_code, 400)
        self.assertEqual(self.registry.require(a['id'])['alias'], '家人号')

    def test_update_validates_times_and_requires_import_and_mail(self):
        a = self.create(); aid = a['id']
        self.assertEqual(self.post('accounts/' + aid, {'enabled': True}).status_code, 400)
        self.imported(a)
        self.assertEqual(self.post('accounts/' + aid, {'enabled': True}).status_code, 400)
        self.mail()
        for payload in ({'sign_time': '25:00'}, {'check_time': '08:00'}, {'enabled': 'true'}, {'channel': 'mini'}, {'alias': ''}):
            self.assertEqual(self.post('accounts/' + aid, payload).status_code, 400)
        r = self.post('accounts/' + aid, {'enabled': True, 'sign_time': '10:00', 'check_time': '17:00'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.store.get('schedule_enabled'))
        self.assertEqual(self.registry.require(aid)['sign_time'], '10:00')

    def test_scope_rejects_other_channel(self):
        a = self.create('mini'); self.imported(a)
        scoped = self.registry.scoped(a['id'])
        with self.assertRaises(ValueError): scoped.put('credential:app', credential())
        self.assertEqual(self.post('login/app/send?account_id=' + a['id'], {'phone': '13800000000'}).status_code, 400)

    def test_import_validates_before_overwriting_and_uses_correct_instance(self):
        x, y = self.create(), self.create(alias='其他账号')
        sx, sy = self.imported(x), self.imported(y); old_x, old_y = sx.get('credential:app'), sy.get('credential:app')
        capture = entry(); raw = json.dumps({'log': {'entries': [capture]}}).encode()
        def upload(): return self.a.post('/api/import?account_id=' + x['id'], data={'channel': 'app', 'file': (io.BytesIO(raw), 'login.har')}, headers={'X-CSRF-Token': self.csrf(self.a)})
        self.network.run.side_effect = None
        self.network.run.return_value = {'state': 'auth_expired', 'message': '失效'}
        self.assertEqual(upload().status_code, 400)
        self.assertEqual(sx.get('credential:app'), old_x)
        self.network.run.return_value = {'state': 'unsigned', 'message': '未签到'}
        self.assertEqual(upload().status_code, 200)
        self.assertFalse(self.network.run.call_args.kwargs['allow_sign'])
        self.assertEqual(sy.get('credential:app'), old_y)
        self.assertIsNone(self.store.get('credential:app'))

    def test_runner_separates_statuses_and_labels_mail(self):
        x, y = self.create(alias='主号'), self.create(alias='家人号')
        sx, sy = self.imported(x), self.imported(y)
        self.runner.run('manual', target=x['id'])
        self.assertIsNotNone(sx.get('status:app')); self.assertIsNone(sy.get('status:app'))
        result = self.store.jobs()[0]['results'][0]
        self.assertEqual(result['account_id'], x['id']); self.assertEqual(result['account_alias'], '主号')
        with self.store.connect() as db: body = db.execute('SELECT body FROM outbox').fetchone()['body']
        self.assertIn('龙湖 App · 主号', body)

    def test_per_instance_schedule_dedupe_restart_and_disabled_accounts(self):
        x, y, off = self.create(), self.create(alias='二号'), self.create(alias='暂停')
        for a in (x, y, off): self.imported(a)
        self.registry.update(x['id'], {'enabled': True})
        self.registry.update(y['id'], {'enabled': True, 'sign_time': '10:00', 'check_time': '17:00'})
        dt = datetime(2026, 10, 23, 8, 39, tzinfo=TZ)
        self.runner.tick(dt)
        self.assertEqual(len(self.store.jobs()), 0)
        dt = dt.replace(hour=10, minute=31)
        self.runner.tick(dt); Runner(self.store, self.network).tick(dt)
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertEqual({j['results'][0]['account_id'] for j in self.store.jobs()}, {x['id'], y['id']})
        self.runner.tick(dt.replace(hour=17, minute=31)); self.runner.tick(dt.replace(hour=18))
        self.assertEqual(len(self.store.jobs()), 4)

    def test_random_daily_windows_stay_in_day_order_and_survive_restart(self):
        account = {'id': 'app', 'sign_time': '09:10', 'check_time': '16:00'}
        start = datetime(2026, 10, 1, tzinfo=TZ)
        selections = []
        for i in range(100):
            day = (start + timedelta(days=i)).date()
            times = schedule_times(account, day, self.store.key)
            self.assertEqual(times, schedule_times(account, day, self.store.key))
            self.assertTrue('08:40' <= times['morning'] <= '09:40')
            self.assertTrue('15:30' <= times['afternoon'] <= '16:30')
            selections.append(times['morning'])
        self.assertGreater(len(set(selections)), 1)
        self.assertTrue(any(schedule_times(account, (start + timedelta(days=i)).date(), self.store.key) !=
                            schedule_times({**account, 'id': 'another'}, (start + timedelta(days=i)).date(), self.store.key)
                            for i in range(10)))
        for sign, check in [('00:00', '00:01'), ('23:58', '23:59'), ('09:10', '09:11')]:
            times = schedule_times({**account, 'sign_time': sign, 'check_time': check}, start.date(), self.store.key)
            self.assertLess(times['morning'], times['afternoon'])
            for kind, base in [('morning', sign), ('afternoon', check)]:
                h, m = map(int, times[kind].split(':')); bh, bm = map(int, base.split(':'))
                self.assertLessEqual(abs((h * 60 + m) - (bh * 60 + bm)), 30)
        times = schedule_times(account, start.date(), self.store.key)
        sign = datetime.combine(start.date(), datetime.strptime(times['morning'], '%H:%M').time(), TZ)
        self.assertIsNone(due_account(account, sign - timedelta(minutes=1), self.store.key))
        self.assertEqual(due_account(account, sign, self.store.key), 'morning')

    def test_runner_waits_for_sampled_time_and_does_not_repeat_after_restart(self):
        account = self.create(); self.imported(account)
        self.registry.update(account['id'], {'enabled': True})
        day = datetime(2026, 10, 23, tzinfo=TZ)
        scheduled = schedule_times(account, day.date(), self.store.key)['morning']
        hour, minute = map(int, scheduled.split(':'))
        due = day.replace(hour=hour, minute=minute)
        self.runner.tick(due - timedelta(minutes=1))
        self.assertEqual(self.store.jobs(), [])
        self.runner.tick(due)
        self.assertEqual(len(self.store.jobs()), 1)
        Runner(self.store, self.network).tick(due + timedelta(minutes=1))
        self.assertEqual(len(self.store.jobs()), 1)
        public = self.registry.public()[0]
        self.assertEqual(public['scheduled_today'], schedule_times(account, datetime.now(TZ).date(), self.store.key))

    def test_legacy_completed_daily_job_not_repeated_by_new_scheduler(self):
        self.store.put('credential:app', credential()); self.store.put('schedule_enabled', True)
        dt = datetime(2026, 10, 23, 9, 10, tzinfo=TZ)
        self.runner.run('morning', dt=dt)
        self.runner.tick(dt)
        self.assertEqual(len(self.store.jobs()), 1)

    def test_archive_clears_only_selected_secrets_and_keeps_history(self):
        x, y = self.create(), self.create(alias='二号'); sx, sy = self.imported(x), self.imported(y)
        self.runner.run('check', target=x['id'])
        self.assertEqual(self.post('accounts/' + x['id'] + '/remove').status_code, 200)
        self.assertIsNone(sx.get('credential:app')); self.assertTrue(sy.get('credential:app'))
        self.assertIsNone(self.registry.get(x['id']))
        rows = self.a.get('/api/jobs?account_id=' + x['id']).json['jobs']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['results'][0]['account_id'], x['id'])

    def test_legacy_archive_does_not_resurrect(self):
        self.store.put('credential:app', credential()); self.registry.require('app')
        self.registry.archive('app')
        self.assertFalse(self.registry.list()); self.assertIsNone(self.store.get('credential:app'))

    def test_filtering_projects_results_and_paginates_without_overlap(self):
        x, y = self.create(), self.create(alias='二号'); self.imported(x); self.imported(y)
        for _ in range(23): self.runner.run('check', target=x['id']); self.runner.run('check', target=y['id'])
        first = self.a.get('/api/jobs?account_id=' + x['id']).json
        second = self.a.get('/api/jobs?account_id=' + x['id'] + '&before=' + str(first['next_cursor'])).json
        self.assertEqual(len(first['jobs']), 20); self.assertEqual(len(second['jobs']), 3)
        self.assertFalse({r['id'] for r in first['jobs']} & {r['id'] for r in second['jobs']})
        for j in first['jobs'] + second['jobs']: self.assertEqual(j['results'][0]['account_id'], x['id'])

    def test_api_does_not_return_tokens_or_pending_sms_phone(self):
        a = self.create(); s = self.imported(a); s.put('pending_sms', {'phone': '13812345678', 'phone_hint': '138****5678', 'expires': 9999999999})
        for path in ('/api/state', '/api/state?account_id=' + a['id']):
            text = self.a.get(path).get_data(as_text=True)
            self.assertNotIn('token-' + a['id'], text); self.assertNotIn('13812345678', text)

    def test_archiving_last_instance_stops_scheduling_and_clears_challenges(self):
        a = self.create(); scoped = self.imported(a)
        scoped.put('pending_sms', {'phone': 'private'})
        self.registry.update(a['id'], {'enabled': True})
        self.registry.archive(a['id'])
        self.assertIsNone(scoped.get('pending_sms'))
        self.runner.tick(datetime(2026, 10, 23, 17, tzinfo=TZ))
        self.assertEqual(self.store.jobs(), [])

    def test_notification_preferences_are_isolated_and_never_repeat_task(self):
        a = self.create(); self.imported(a)
        self.assertEqual(self.post('notifications', {'success': False, 'failure': False}).status_code, 200)
        self.runner.run('manual', target=a['id'])
        self.assertIsNone(self.store.jobs()[0]['mail_state'])
        self.runner.run('afternoon', target=a['id'])
        self.assertIsNone(self.store.jobs()[0]['mail_state'])
        self.assertEqual(self.network.run.call_count, 2)
        self.assertNotIn('notification_events', self.b.get('/api/state').json.get('mail', {}))
        self.assertTrue(self.b.get('/api/state').json['notification_events']['failure'])

    def test_retired_review_preference_is_ignored_and_not_exposed(self):
        self.store.put('notification_events', {'success': False, 'failure': True, 'afternoon': False})
        self.assertEqual(self.a.get('/api/state').json['notification_events'], {'success': False, 'failure': True})
        self.assertEqual(self.post('notifications', {'success': True, 'failure': False, 'afternoon': True}).status_code, 200)
        self.assertEqual(self.store.get('notification_events'), {'success': True, 'failure': False})
        for payload in ({'success': True}, {'success': 'yes', 'failure': True},
                        {'success': True, 'failure': True, 'other': True}):
            self.assertEqual(self.post('notifications', payload).status_code, 400)

    def test_mail_preferences_follow_outcome_for_all_task_kinds(self):
        a = self.create(); self.imported(a)
        self.mail()
        day = 0
        for kind in ('morning', 'afternoon', 'manual', 'check'):
            for state in ('signed', 'unsigned', 'auth_expired', 'request_error', 'unknown', 'captcha_required'):
                for enabled in (False, True):
                    with self.subTest(kind=kind, state=state, enabled=enabled):
                        self.network.run.side_effect = None
                        self.network.run.return_value = {'state': state, 'message': state}
                        event = 'success' if state == 'signed' else 'failure'
                        prefs = {'success': not enabled, 'failure': not enabled, 'afternoon': not enabled}
                        prefs[event] = enabled
                        self.store.put('notification_events', prefs)
                        self.runner.run(kind, target=a['id'], dt=datetime(2026, 10, 23, 16, tzinfo=TZ) + timedelta(days=day))
                        day += 1
                        self.assertEqual(self.store.jobs()[0]['mail_state'], 'pending' if enabled and kind != 'check' else None)

    def test_production_client_has_fresh_transport_per_instance(self):
        self.imported(self.create()); self.imported(self.create(alias='第二个'))
        with patch('app.runner.LongforClient') as factory:
            factory.return_value.run.return_value = {'state': 'unsigned', 'message': '未签到'}
            runner = Runner(self.store)
            runner.run('check')
            self.assertEqual(factory.call_count, 3)
            self.assertEqual(factory.return_value.transport.close.call_count, 2)

    def test_legacy_global_switch_only_enables_connected_instances(self):
        connected, draft = self.create(), self.create(alias='未导入')
        self.imported(connected); self.mail()
        self.assertEqual(self.post('schedule', {'enabled': True}).status_code, 200)
        self.assertTrue(self.registry.require(connected['id'])['enabled'])
        self.assertFalse(self.registry.require(draft['id'])['enabled'])
        self.assertEqual(self.post('schedule', {'enabled': False}).status_code, 200)
        self.assertFalse(any(a['enabled'] for a in self.registry.list()))


if __name__ == '__main__': unittest.main()
