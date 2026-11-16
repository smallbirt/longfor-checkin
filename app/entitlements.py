"""Wallet, quota and durable idempotency share the program-registry transaction."""
import hashlib
import json
import re
import uuid
from .store import now

DEFAULT_RULES = {'free': 2, 'price': 10}


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s需为 %s–%s 的整数。' % (label, low, high))
    return value


def reason(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        raise ValueError('请填写 1–200 字的操作原因。')
    return value.strip()


def request_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', value):
        raise ValueError('操作标识无效，请刷新页面重试。')
    return value


def wallet_row(store, db, items):
    row = db.execute('SELECT * FROM user_wallet WHERE id=1').fetchone()
    if not row:
        # Existing enabled plans are grandfathered, without charging users.
        used = sum(a['enabled'] and not a.get('archived') for a in items)
        free = 30 if store.user_id == 1 else max(getattr(store, 'initial_free', 2), used)
        db.execute('INSERT INTO user_wallet(id,balance,free_slots,purchased_slots) VALUES(1,0,?,0)', (free,))
        row = db.execute('SELECT * FROM user_wallet WHERE id=1').fetchone()
    return dict(row)


class Wallet:
    def __init__(self, store):
        self.store = store

    def transaction(self, callback):
        from .program_accounts import ProgramAccounts
        def run(items, db):
            return callback(wallet_row(self.store, db, items), items, db)
        return ProgramAccounts(self.store)._transaction(run)

    def public(self, price=10):
        def read(w, items, db):
            used = sum(a['enabled'] and not a.get('archived') for a in items)
            return {'points': w['balance'], 'free': w['free_slots'], 'purchased': w['purchased_slots'],
                    'used': used, 'total': w['free_slots'] + w['purchased_slots'], 'price': price}
        return self.transaction(read)

    def change(self, kind, value, why, actor, key):
        if kind not in ('grant', 'deduct', 'purchase', 'quota'):
            raise ValueError('积分操作无效。')
        integer(value, 0 if kind == 'quota' else 1, 30 if kind == 'quota' else 100000, '调整数量')
        why, key = reason(why), request_key(key)
        fingerprint = hashlib.sha256(json.dumps([kind, value, why, actor], ensure_ascii=False).encode()).hexdigest()
        def change(w, items, db):
            old = db.execute('SELECT * FROM credit_ledger WHERE request_id=?', (key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('此操作标识已用于其他请求，请刷新后重试。')
                return {**dict(old), 'replayed': True}
            delta = 0
            if kind in ('grant', 'deduct', 'purchase'):
                delta = value if kind == 'grant' else -value
                if w['balance'] + delta < 0:
                    raise ValueError('积分余额不足，尚差 %s 积分。' % (-w['balance'] - delta))
                if w['balance'] + delta > 1000000000:
                    raise ValueError('积分余额超出上限。')
                w['balance'] += delta
            if kind == 'purchase':
                if w['free_slots'] + w['purchased_slots'] >= 30:
                    raise ValueError('每个用户最多支持 30 个自动打卡名额。')
                w['purchased_slots'] += 1
            if kind == 'quota':
                used = sum(a['enabled'] and not a.get('archived') for a in items)
                if value + w['purchased_slots'] < used:
                    raise ValueError('总名额不能低于当前占用的 %s 个。' % used)
                if value + w['purchased_slots'] > 30:
                    raise ValueError('总名额不能超过 30 个。')
                w['free_slots'] = value
            db.execute('UPDATE user_wallet SET balance=?,free_slots=?,purchased_slots=? WHERE id=1',
                       (w['balance'], w['free_slots'], w['purchased_slots']))
            row = {'id': 'P-' + uuid.uuid4().hex, 'request_id': key, 'kind': kind, 'delta': delta,
                   'balance': w['balance'], 'free_slots': w['free_slots'], 'purchased_slots': w['purchased_slots'],
                   'actor': actor, 'reason': why, 'created': now().isoformat(), 'fingerprint': fingerprint}
            cols = tuple(row)
            db.execute('INSERT INTO credit_ledger(' + ','.join(cols) + ') VALUES(' + ','.join('?' for _ in cols) + ')', tuple(row.values()))
            return row
        return self.transaction(change)

    def ledger(self, limit=200):
        with self.store.connect() as db:
            return [{k: r[k] for k in ('id', 'kind', 'delta', 'balance', 'free_slots', 'purchased_slots', 'actor', 'reason', 'created')}
                    for r in db.execute('SELECT * FROM credit_ledger ORDER BY created DESC,id DESC LIMIT ?', (limit,)).fetchall()]
