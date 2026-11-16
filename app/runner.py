import json
import threading
import time
import fcntl
from .constants import CHANNELS
from .store import now
from .client import LongforClient
from .mailer import send_mail
from .mini_push import MiniPush

LABELS = {'morning': '09:10 签到', 'afternoon': '16:00 复核与补签',
          'manual': '手动签到', 'check': '状态查询'}


def due_slot(dt):
    hm = (dt.hour, dt.minute)
    if hm >= (16, 0):
        return 'afternoon'
    if hm >= (9, 10):
        return 'morning'
    return None


class Runner:
    def __init__(self, store, client=None, sender=send_mail, mail_config=None, mini_push=None):
        self.store = store
        self.client = client or LongforClient()
        self.injected_client = client is not None
        self.sender = sender
        self.mini_push = mini_push or MiniPush(store)
        self.mail_config = mail_config or (lambda: self.store.get('mail'))
        self.lock = threading.Lock()
        self.mail_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.account_active = lambda: True

    def recover(self):
        # Single process deployment. Any previous active task was interrupted.
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")
            db.execute("UPDATE mini_push_outbox SET state='uncertain',error='发送过程中服务重启，结果未确认，不自动重发。' WHERE state='sending'")

    def submit(self, kind, target=None):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            threading.Thread(target=self.run, args=(kind, target), kwargs={'_acquired': True}, daemon=True).start()
        except Exception:
            self.lock.release()
            raise
        return True

    def run(self, kind, target=None, dt=None, _acquired=False):
        dt = dt or now()
        if not _acquired and not self.lock.acquire(blocking=False):
            return None
        try:
            if not self.account_active():
                return None
            from .program_accounts import ProgramAccounts, InstanceStore
            registry = ProgramAccounts(self.store)
            instances = registry.list()
            if target:
                selected = registry.get(target)
                if not selected and target not in CHANNELS:
                    raise ValueError('签到账号已移除。')
                targets = [selected or {'id': target, 'channel': target, 'alias': '主号',
                                        'sign_time': '09:10', 'check_time': '16:00'}]
            else:
                targets = instances or [{'id': key, 'channel': key, 'alias': '主号'} for key in CHANNELS]
            day = dt.date().isoformat()
            dedupe = day + ':' + kind + (':' + target if target else '') if kind in ('morning', 'afternoon') else None
            with self.store.connect() as db:
                if dedupe:
                    row = db.execute('SELECT id, status FROM jobs WHERE dedupe=?', (dedupe,)).fetchone()
                    if row and row['status'] != 'interrupted':
                        return None
                    if row:
                        job_id = row['id']
                        db.execute("UPDATE jobs SET status='running', results='[]' WHERE id=?", (job_id,))
                    else:
                        job_id = db.insert('INSERT INTO jobs(dedupe,kind,day,created,status) VALUES (?,?,?,?,?)',
                             (dedupe, kind, day, dt.isoformat(), 'running'))
                else:
                    job_id = db.insert('INSERT INTO jobs(kind,day,created,status) VALUES (?,?,?,?)',
                            (kind, day, dt.isoformat(), 'running'))
            results = []
            task_label = LABELS[kind]
            if target and kind in ('morning', 'afternoon'):
                from .program_accounts import schedule_times
                scheduled = schedule_times(targets[0], dt.date(), self.store.key)[kind]
                task_label = scheduled + (' 签到' if kind == 'morning' else ' 复核与补签')
            for instance in targets:
                key = instance['channel']
                scoped = InstanceStore(self.store, instance)
                cred = scoped.get('credential:' + key)
                if not cred:
                    result = {'state': 'credentials_missing', 'message': '尚未导入此渠道凭证。', 'checked_at': now().isoformat()}
                else:
                    try:
                        # No HTTP cookie/session state crosses native account instances.
                        transport = self.client if self.injected_client else LongforClient()
                        try:
                            result = dict(transport.run(cred, day, allow_sign=kind != 'check'))
                        finally:
                            if not self.injected_client:
                                transport.transport.close()
                    except Exception:
                        # Never log raw HTTP exceptions/headers/tokens.
                        result = {'state': 'unknown', 'message': '处理异常，请稍后查询状态。', 'checked_at': now().isoformat()}
                    if result['state'] in ('auth_expired', 'captcha_required'):
                        # Do not overwrite credentials imported while this network call ran.
                        with self.store.lock:
                            latest = scoped.get('credential:' + key)
                            if latest == cred:
                                cred['blocked'] = result['state']
                                scoped.put('credential:' + key, cred)
                    elif result['state'] == 'signed' and cred.get('blocked') == 'captcha_required':
                        with self.store.lock:
                            latest = scoped.get('credential:' + key)
                            if latest == cred:
                                cred.pop('blocked', None)
                                scoped.put('credential:' + key, cred)
                result['channel'] = key
                result['account_id'] = instance['id']
                result['account_alias'] = instance['alias']
                results.append(result)
                scoped.put('status:' + key, {**result, 'day': day})
            subject = '[每日签到] %s · %s · %s' % (day, task_label,
                '全部已签到' if all(x['state'] == 'signed' for x in results) else '需要关注')
            body = ['北京时间：' + dt.strftime('%Y-%m-%d %H:%M'), '任务：' + task_label, '']
            for r in results:
                body.append(CHANNELS[r['channel']]['name'] + ' · ' + r['account_alias'] + '：' + r['message'])
                if r.get('rewards'):
                    body.append('本次奖励：' + '、'.join(r['rewards']))
            body.extend(['', '登录失效：手机重新登录后，在管理页导入 HAR 或粘贴新请求。',
                         '自动滑块未通过：在对应手机签到页完成验证，再查询状态；下轮任务仍可尝试自动验证。'])
            with self.mail_lock, self.mini_push.config_lock, self.store.lock, self.store.connect() as db:
                prefs = self.store.get('notification_events', {})
                mail_ready = bool(self.mail_config())
                push_ready = self.mini_push.ready(self.store)
                self.mini_push.enqueue(self.store, db, job_id, kind, results, dt)
                db.execute('UPDATE jobs SET status=?, results=? WHERE id=?',
                           ('done', json.dumps(results, ensure_ascii=False), job_id))
                # Queries used for credential validation do not generate notifications.
                event = 'success' if all(x['state'] == 'signed' for x in results) else 'failure'
                if kind != 'check' and prefs.get(event, True):
                    if mail_ready or not push_ready:
                        db.execute('INSERT INTO outbox(job_id,subject,body) VALUES (?,?,?) ON CONFLICT(job_id) DO NOTHING',
                                   (job_id, subject, '\n'.join(body)))
            return job_id
        finally:
            self.lock.release()

    def deliver(self):
        if not self.account_active():
            return
        if not self.mail_lock.acquire(blocking=False):
            return
        try:
            for table, config, sender in [('outbox', self.mail_config(), self.sender)]:
                if not config:
                    continue
                with self.store.connect() as db:
                    rows = db.execute("SELECT * FROM " + table + " WHERE state='pending' AND due<=? ORDER BY id LIMIT 1", (time.time(),)).fetchall()
                for row in rows:
                    try:
                        ok, error = sender(config, row['subject'], row['body'])
                    except Exception:
                        ok, error = False, '通知发送异常，请稍后重试。'
                    attempts = row['attempts'] + 1
                    state = 'sent' if ok else ('failed' if attempts >= 4 else 'pending')
                    delay = [60, 300, 1800, 3600][min(attempts - 1, 3)]
                    with self.store.connect() as db:
                        db.execute('UPDATE ' + table + ' SET state=?,attempts=?,due=?,error=? WHERE id=?',
                                   (state, attempts, time.time() + delay, error, row['id']))
            with self.mini_push.config_lock:
                self.mini_push.deliver(self.store)
        finally:
            self.mail_lock.release()

    def tick(self, dt=None):
        """One durable daily slot per instance; existing combined jobs stay valid."""
        from .program_accounts import ProgramAccounts, due_account
        dt = dt or now()
        instances = ProgramAccounts(self.store).list()
        if not instances:
            if ProgramAccounts(self.store).list(archived=True):
                return
            slot = due_account({'id': 'legacy', 'sign_time': '09:10', 'check_time': '16:00'}, dt, self.store.key)
            if slot:
                self.run(slot, dt=dt)
            return
        failed = False
        for a in instances:
            if not a['enabled']:
                continue
            slot = due_account(a, dt, self.store.key)
            if not slot:
                continue
            if a['id'] in CHANNELS:
                with self.store.connect() as db:
                    old = db.execute('SELECT status,results FROM jobs WHERE dedupe=?', (dt.date().isoformat() + ':' + slot,)).fetchone()
                if old and old['status'] != 'interrupted' and (old['status'] == 'running' or any(r.get('channel') == a['id'] for r in json.loads(old['results']))):
                    continue
            try:
                self.run(slot, target=a['id'], dt=dt)
            except Exception:
                failed = True
        if failed:
            raise RuntimeError('部分账号任务异常，请查看记录。')

    def loop(self):
        while not self.stop.is_set():
            try:
                if self.store.get('schedule_enabled', False):
                    self.tick()
                self.deliver()
                self.store.put('heartbeat', now().isoformat())
                self.store.put('worker_error', None)
            except Exception:
                self.store.put('worker_error', '后台任务异常，请检查运行记录。')
            self.stop.wait(20)

    def start(self):
        # Hold for the lifetime of the process; prevents two schedulers on one volume.
        self.process_lock = open(self.store.root / 'scheduler.lock', 'a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError('同一数据目录已有运行实例，不能启动第二个调度器。') from None
        self.recover()
        self.thread = threading.Thread(target=self.loop, daemon=True, name='checkin-scheduler')
        self.thread.start()
