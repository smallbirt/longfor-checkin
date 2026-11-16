"""Per-user program instances. Additive encrypted settings; no credential copies.

Legacy ids app/mini keep their existing keys. New UUIDs use isolated key prefixes.
The registry and scoped keys live in the owner's existing SQLite/PG workspace,
so the existing encryption, backup and SQLite-to-PG migration still apply.
"""
import json
import hashlib
import hmac
import re
import uuid
from datetime import timedelta
from .constants import CHANNELS
from .store import now

KEY = 'program_accounts_v1'


class ProgramAccounts:
    def __init__(self, store):
        self.store = store

    def _transaction(self, callback):
        with self.store.lock, self.store.connect() as db:
            db.serialize('program-accounts')
            row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
            items = json.loads(self.store.cipher.decrypt(row['value'].encode())) if row else []
            def read_setting(key, default=None):
                setting = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                return json.loads(self.store.cipher.decrypt(setting['value'].encode())) if setting else default
            # Upgrade lazily without copying or replacing any credential/status.
            ids = {a['id'] for a in items}
            for channel in CHANNELS:
                if channel not in ids and read_setting('credential:' + channel):
                    items.append({'id': channel, 'channel': channel, 'alias': '主号',
                        'enabled': bool(read_setting('schedule_enabled', False)),
                        'sign_time': '09:10', 'check_time': '16:00',
                        'created': now().isoformat(), 'archived': False})
            result = callback(items, db)
            raw = json.dumps(items, ensure_ascii=False).encode()
            if not row or self.store.cipher.decrypt(row['value'].encode()) != raw:
                token = self.store.cipher.encrypt(raw).decode()
                db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (KEY, token))
            return result

    def list(self, archived=False):
        return self._transaction(lambda items, db: [dict(a) for a in items if archived or not a.get('archived')])

    def get(self, account_id, archived=False):
        return next((a for a in self.list(archived) if a['id'] == account_id), None)

    def require(self, account_id):
        item = self.get(account_id)
        if not item:
            raise ValueError('签到账号不存在或不属于当前用户。')
        return item

    def create(self, channel, alias):
        if not isinstance(channel, str) or channel not in CHANNELS:
            raise ValueError('请选择已接入的签到程序。')
        alias = self.valid_alias(alias)
        def create(items, db):
            if sum(not a.get('archived') for a in items) >= 30:
                raise ValueError('每个用户最多添加 30 个签到账号。')
            item = {'id': uuid.uuid4().hex, 'channel': channel, 'alias': alias,
                'enabled': False, 'sign_time': '09:10', 'check_time': '16:00',
                'created': now().isoformat(), 'archived': False}
            items.append(item)
            return dict(item)
        return self._transaction(create)

    @staticmethod
    def valid_alias(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 30 or any(ord(c) < 32 for c in value):
            raise ValueError('账号昵称需要 1–30 个字符，不能包含控制字符。')
        return value.strip()

    def update(self, account_id, incoming):
        allowed = {'alias', 'enabled', 'sign_time', 'check_time'}
        if not set(incoming) <= allowed:
            raise ValueError('只能修改昵称、开关和签到时间。')
        def update(items, db):
            a = next((a for a in items if a['id'] == account_id and not a.get('archived')), None)
            if not a:
                raise ValueError('签到账号不存在或不属于当前用户。')
            candidate = {**a, **incoming}
            candidate['alias'] = self.valid_alias(candidate['alias'])
            if not isinstance(candidate['enabled'], bool):
                raise ValueError('自动签到开关无效。')
            for key in ('sign_time', 'check_time'):
                if not isinstance(candidate[key], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', candidate[key]):
                    raise ValueError('时间格式需为 HH:MM（北京时间）。')
            if candidate['check_time'] <= candidate['sign_time']:
                raise ValueError('复核时间应晚于签到时间。')
            if candidate['enabled'] and not a['enabled']:
                from .entitlements import wallet_row
                wallet = wallet_row(self.store, db, items)
                used = sum(x['enabled'] and not x.get('archived') for x in items)
                if used >= wallet['free_slots'] + wallet['purchased_slots']:
                    raise ValueError('自动打卡名额已用满，请在 PC 端“积分与名额”中扩容。')
            a.update(candidate)
            return dict(a)
        return self._transaction(update)

    def set_schedule(self, enabled, configured):
        """Validate every target before changing any plan, including legacy switch."""
        def update(items, db):
            from .entitlements import wallet_row
            wallet = wallet_row(self.store, db, items)
            targets = [a for a in items if not a.get('archived') and a['id'] in configured]
            if enabled and len(targets) > wallet['free_slots'] + wallet['purchased_slots']:
                raise ValueError('自动打卡名额不足，请逐个开启计划或先在 PC 端扩容。')
            for a in items:
                if not a.get('archived'):
                    a['enabled'] = enabled and a['id'] in configured
            token = self.store.cipher.encrypt(json.dumps(enabled).encode()).decode()
            db.execute("INSERT INTO settings(key,value) VALUES('schedule_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (token,))
        self._transaction(update)

    def archive(self, account_id):
        def archive(items, db):
            a = next((a for a in items if a['id'] == account_id and not a.get('archived')), None)
            if not a:
                raise ValueError('签到账号不存在或不属于当前用户。')
            a.update(enabled=False, archived=True)
            # Push permissions belong to the site user, so removing one signing
            # account must not cancel reminders for the user's other accounts.
            if account_id in CHANNELS:
                keys = ['credential:' + account_id, 'status:' + account_id, 'login-profile:' + account_id]
                if account_id == 'app':
                    keys += ['pending_sms', 'automatic_device', 'sms_retry_at']
                else:
                    keys += ['pending_mini_sms', 'mini_sms_retry_at']
                for key in keys:
                    db.execute('DELETE FROM settings WHERE key=?', (key,))
            else:
                db.execute('DELETE FROM settings WHERE key LIKE ?', ('instance:' + account_id + ':%',))
        self._transaction(archive)

    def scoped(self, account_id):
        return InstanceStore(self.store, self.require(account_id))

    def public(self):
        result = []
        today = now().date()
        for a in self.list():
            scoped = InstanceStore(self.store, a)
            cred = scoped.get('credential:' + a['channel']) or {}
            profile = scoped.get('login-profile:' + a['channel']) or {}
            result.append({**a, 'program_type_id': 'longfor_' + a['channel'],
                'program_name': '龙湖小程序' if a['channel'] == 'mini' else '龙湖 App',
                'configured': bool(cred), 'imported_at': cred.get('imported_at'),
                'blocked': cred.get('blocked'), 'phone_hint': profile.get('phone_hint'),
                'status': scoped.get('status:' + a['channel']),
                'scheduled_today': schedule_times(a, today, self.store.key),
                'scheduled_tomorrow': schedule_times(a, today + timedelta(days=1), self.store.key)})
        return result


class InstanceStore:
    def __init__(self, parent, account):
        self.parent, self.account = parent, account
        self.prefix = '' if account['id'] in CHANNELS else 'instance:' + account['id'] + ':'

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def get(self, key, default=None):
        if key in ('heartbeat', 'worker_error', 'schedule_enabled'):
            return self.parent.get(key, default)
        # Even legacy views cannot read the other channel's login state.
        if key.startswith(('credential:', 'status:', 'login-profile:')) and key.split(':', 1)[1] != self.account['channel']:
            return default
        if self.account['channel'] == 'mini' and key in ('pending_sms', 'sms_retry_at', 'automatic_device'):
            return default
        if self.account['channel'] == 'app' and key in ('pending_mini_sms', 'mini_sms_retry_at'):
            return default
        return self.parent.get(self.prefix + key, default)

    def put(self, key, value):
        if key.startswith(('credential:', 'status:', 'login-profile:')) and key.split(':', 1)[1] != self.account['channel']:
            raise ValueError('登录文件与所选账号的程序不一致。')
        return self.parent.put(self.prefix + key, value)


def schedule_times(account, day, key):
    """Stable independent daily offsets, bounded to this day and slot order."""
    def minutes(value):
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute
    sign, check = minutes(account['sign_time']), minutes(account['check_time'])
    midpoint = (sign + check - 1) // 2
    def choose(kind, low, high):
        payload = ('schedule-v1|%s|%s|%s' % (day.isoformat(), account['id'], kind)).encode()
        digest = hmac.new(key, payload, hashlib.sha256).digest()
        selected = low + int.from_bytes(digest, 'big') % (high - low + 1)
        return '%02d:%02d' % divmod(selected, 60)
    return {
        'morning': choose('morning', max(0, sign - 30), min(1439, sign + 30, midpoint)),
        'afternoon': choose('afternoon', max(0, check - 30, midpoint + 1), min(1439, check + 30)),
    }


def due_account(account, dt, key):
    times = schedule_times(account, dt.date(), key)
    hm = dt.strftime('%H:%M')
    if hm >= times['afternoon']:
        return 'afternoon'
    if hm >= times['morning']:
        return 'morning'
    return None


def filtered_jobs(store, limit=21, before=None, account_id=None, channel=None, day=None, state=None):
    """Cursor over matching jobs, using portable SQL plus bounded read batches.

    Preserve the original job cursor/queue identity and annotate legacy results
    at read time. Never rewrite historical jobs merely to change the UI.
    """
    matches = []
    cursor = before
    names = {a['id']: a['alias'] for a in ProgramAccounts(store).list(archived=True)}
    while len(matches) < limit:
        batch = store.jobs(limit=100, before=cursor)
        if not batch:
            break
        for job in batch:
            results = [{**r, 'account_id': r.get('account_id', r.get('channel')),
                'account_alias': r.get('account_alias') or names.get(r.get('account_id', r.get('channel')), '主号')} for r in job['results']]
            results = [r for r in results if (not account_id or r['account_id'] == account_id)
                and (not channel or r.get('channel') == channel) and (not state or r.get('state') == state)]
            if (not day or job['day'] == day) and (results or not any((account_id, channel, state))):
                matches.append({**job, 'results': results})
                if len(matches) == limit:
                    break
        cursor = batch[-1]['id']
        if len(batch) < 100:
            break
    return matches
