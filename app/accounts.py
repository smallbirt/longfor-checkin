"""Site accounts and isolated workspaces. Legacy data belongs to admin (id=1)."""
import fcntl
import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from .store import Store, now
from .database import ACCOUNT_SCHEMA
from .mini_push import MiniPush
from .runner import Runner, due_slot


def valid_password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 128:
        raise ValueError('密码需要 10–128 个字符。')
    return value


class Accounts:
    def __init__(self, store, admin_password):
        self.store = store
        defaults = store.get('entitlement_rules', {'free': 2})
        with store.connect() as db:
            db.executescript(ACCOUNT_SCHEMA)
            row = db.execute('SELECT * FROM users WHERE id=1').fetchone()
            if not row:
                db.execute('INSERT INTO users(id,username,password_hash,created) VALUES(1,?,?,?)',
                           ('admin', generate_password_hash(admin_password, method='pbkdf2:sha256:600000'), now().isoformat()))
                db.reset_sequence('users')
            elif not check_password_hash(row['password_hash'], admin_password):
                db.execute('UPDATE users SET password_hash=?, version=version+1 WHERE id=1',
                           (generate_password_hash(admin_password, method='pbkdf2:sha256:600000'),))
            db.execute('INSERT INTO user_meta(user_id,initial_free) SELECT id,? FROM users WHERE 1=1 ON CONFLICT(user_id) DO NOTHING', (defaults['free'],))

    def get(self, user_id):
        with self.store.connect() as db:
            row = db.execute('SELECT u.id,u.username,u.version,u.created,m.status,m.nickname,m.note,m.last_active,m.initial_free FROM users u JOIN user_meta m ON m.user_id=u.id WHERE u.id=?', (user_id,)).fetchone()
        return dict(row) if row else None

    def ids(self):
        with self.store.connect() as db:
            return [r['id'] for r in db.execute('SELECT id FROM users ORDER BY id')]

    @staticmethod
    def record_event(db, user_id, kind, actor, reason):
        db.execute('INSERT INTO admin_events(id,user_id,kind,actor,reason,created) VALUES(?,?,?,?,?,?)',
                   ('A-' + uuid.uuid4().hex, user_id, kind, actor, reason, now().isoformat()))

    def register(self, username, password, nickname='', audit=None):
        username = str(username).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,31}', username):
            raise ValueError('用户名需为 3–32 位字母、数字、下划线或短横线，以字母或数字开头。')
        hashed = generate_password_hash(valid_password(password), method='pbkdf2:sha256:600000')
        initial_free = self.store.get('entitlement_rules', {'free': 2})['free']
        try:
            with self.store.connect() as db:
                db.serialize('accounts')
                if db.execute('SELECT COUNT(*) AS count FROM users').fetchone()['count'] >= 100:
                    raise ValueError('当前注册名额已满，请联系管理员。')
                uid = db.insert('INSERT INTO users(username,password_hash,created) VALUES(?,?,?)',
                                (username, hashed, now().isoformat()))
                db.execute('INSERT INTO user_meta(user_id,initial_free,nickname) VALUES(?,?,?)',
                           (uid, initial_free, nickname))
                if audit:
                    self.record_event(db, uid, *audit)
        except Exception as exc:
            if not self.store.integrity_error(exc):
                raise
            raise ValueError('这个用户名已被使用，请换一个。') from None
        return self.get(uid)

    def authenticate(self, username, password):
        if not isinstance(password, str) or len(password) > 128:
            return None
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (str(username).strip().lower(),)).fetchone()
            # Same hash cost even for unknown usernames.
            hashed = row['password_hash'] if row else db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()['password_hash']
        ok = check_password_hash(hashed, password)
        if ok and row:
            current = self.get(row['id'])
            if current and current['status'] == 'active':
                return {**current, 'version': row['version']}
        return None

    def touch(self, user_id):
        stamp = now().isoformat()
        with self.store.connect() as db:
            # At most one write per user per five minutes, including API polling.
            db.execute('UPDATE user_meta SET last_active=? WHERE user_id=? AND (last_active=? OR last_active<?)',
                       (stamp, user_id, '', (now() - timedelta(minutes=5)).isoformat()))

    def revoke(self, user_id, status=None, audit=None):
        if user_id == 1 or not self.get(user_id):
            raise ValueError('只能管理普通用户。')
        if status is not None and status not in ('active', 'disabled'):
            raise ValueError('用户状态无效。')
        with self.store.connect() as db:
            db.serialize('accounts')
            db.execute('UPDATE users SET version=version+1 WHERE id=?', (user_id,))
            if status is not None:
                db.execute('UPDATE user_meta SET status=? WHERE user_id=?', (status, user_id))
            db.execute('DELETE FROM user_resets WHERE user_id=?', (user_id,))
            if audit:
                self.record_event(db, user_id, *audit)

    def issue_reset(self, user_id, audit=None):
        if user_id == 1 or not self.get(user_id):
            raise ValueError('只能重置普通用户的本站登录。')
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.store.connect() as db:
            db.serialize('accounts')
            status = db.execute('SELECT status FROM user_meta WHERE user_id=?', (user_id,)).fetchone()
            if status['status'] != 'active':
                raise ValueError('请先恢复用户，再重置登录。')
            db.execute('UPDATE users SET version=version+1 WHERE id=?', (user_id,))
            version = db.execute('SELECT version FROM users WHERE id=?', (user_id,)).fetchone()['version']
            db.execute('DELETE FROM user_resets WHERE user_id=? OR expires<?', (user_id, time.time()))
            db.execute('INSERT INTO user_resets(digest,user_id,version,expires) VALUES(?,?,?,?)',
                       (digest, user_id, version, time.time() + 1800))
            if audit:
                self.record_event(db, user_id, *audit)
        return '/reset-password#' + token

    def reset_password(self, token, password):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', token):
            raise ValueError('重置链接无效或已过期。')
        hashed = generate_password_hash(valid_password(password), method='pbkdf2:sha256:600000')
        with self.store.connect() as db:
            db.serialize('accounts')
            row = db.execute('SELECT r.*,u.version AS current_version,m.status FROM user_resets r JOIN users u ON u.id=r.user_id JOIN user_meta m ON m.user_id=u.id WHERE r.digest=?',
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if not row or row['expires'] < time.time() or row['version'] != row['current_version'] or row['status'] != 'active':
                raise ValueError('重置链接无效或已过期。')
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?', (hashed, row['user_id']))
            db.execute('DELETE FROM user_resets WHERE user_id=?', (row['user_id'],))

    def change_password(self, user_id, password):
        if user_id == 1:
            raise ValueError('管理员密码请在服务器 .env 中修改 ADMIN_PASSWORD 后重建容器。')
        hashed = generate_password_hash(valid_password(password), method='pbkdf2:sha256:600000')
        with self.store.connect() as db:
            db.execute('UPDATE users SET password_hash=?,version=version+1 WHERE id=?', (hashed, user_id))

    def allow(self, key, limit, window):
        """Persistent fixed-window limit; serialized across concurrent requests."""
        stamp = time.time()
        with self.store.connect() as db:
            db.serialize('accounts')
            db.execute('DELETE FROM rate_limits WHERE start<?', (stamp - 86400,))
            row = db.execute('SELECT * FROM rate_limits WHERE key=?', (key,)).fetchone()
            if row and stamp - row['start'] < window:
                if row['count'] >= limit:
                    return False
                db.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
            else:
                db.execute('INSERT INTO rate_limits(key,start,count) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET start=excluded.start,count=1', (key, stamp))
        return True


class Workspaces:
    def __init__(self, root, accounts, client=None):
        self.root, self.accounts, self.client = root, accounts, client
        self.entries = {}
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.started = False
        self.push = MiniPush(root)

    def get(self, user_id):
        # user_id is resolved from a signed session and checked against users, never a request path.
        with self.guard:
            if user_id not in self.entries:
                if not self.accounts.get(user_id):
                    raise ValueError('账号不存在。')
                store = self.root if user_id == 1 else self.root.child(user_id)
                store.initial_free = self.accounts.get(user_id)['initial_free']
                runner = Runner(store, client=self.client, mail_config=lambda: self.mail_config(user_id), mini_push=self.push)
                runner.account_active = lambda uid=user_id: (self.accounts.get(uid) or {}).get('status') == 'active'
                if self.started:
                    runner.recover()
                self.entries[user_id] = (store, runner)
            return self.entries[user_id]

    def preferences(self, user_id):
        store, _ = self.get(user_id)
        pref = store.get('notifications')
        if pref is None and user_id == 1:
            old = self.root.get('mail', {})
            return {'recipient': old.get('recipient', ''), 'verified': bool(old.get('recipient'))}
        return pref or {}

    def mail_config(self, user_id):
        shared = self.root.get('mail')
        pref = self.preferences(user_id)
        if not shared or not pref.get('verified') or not pref.get('recipient'):
            return None
        return {**shared, 'recipient': pref['recipient']}

    def notification_ready(self, user_id):
        store, _ = self.get(user_id)
        return bool(self.mail_config(user_id) or self.push.ready(store))

    def tick(self):
        for uid in self.accounts.ids():
            if self.stop.is_set():
                break
            store = None
            try:
                if (self.accounts.get(uid) or {}).get('status') != 'active':
                    continue
                store, runner = self.get(uid)
                if store.get('schedule_enabled', False):
                    runner.tick(now())
                runner.deliver()
                store.put('heartbeat', now().isoformat())
                store.put('worker_error', None)
            except Exception:
                try:
                    (store or self.root).put('worker_error', '后台任务异常，请稍后查看记录。')
                except Exception:
                    pass  # A damaged user's data must not stop other accounts.

    def loop(self):
        while not self.stop.is_set():
            if self.root.is_postgres:
                try:
                    self.root.backend.check_scheduler()
                except Exception:
                    self.stop.set()
                    os._exit(1)  # Release the scheduler lease; Docker restarts this single process.
            self.tick()
            self.stop.wait(20)

    def start(self):
        self.process_lock = open(self.root.root / 'scheduler.lock', 'a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError('同一数据目录已有运行实例，不能启动第二个调度器。') from None
        if self.root.is_postgres:
            try:
                self.root.backend.acquire_scheduler()
            except Exception:
                self.process_lock.close()
                raise
        with self.guard:
            for store, runner in self.entries.values():
                runner.recover()
            self.started = True
        self.thread = threading.Thread(target=self.loop, daemon=True, name='checkin-scheduler')
        self.thread.start()
