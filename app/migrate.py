"""Offline, atomic, non-destructive SQLite -> PostgreSQL migration."""
import hashlib
import json
import sqlite3
from pathlib import Path
from cryptography.fernet import Fernet
from .database import ACCOUNT_SCHEMA, SCHEDULER_LOCK
from .store import Store

TABLES = ('settings', 'jobs', 'outbox', 'wecom_outbox', 'mini_push_owners', 'mini_push_subscriptions', 'mini_push_outbox', 'users', 'rate_limits', 'user_meta', 'admin_events', 'user_resets', 'wechat_links', 'wechat_tickets', 'wechat_codes', 'user_wallet', 'credit_ledger')
COLUMNS = {
    'settings': ('key', 'value'),
    'jobs': ('id', 'dedupe', 'kind', 'day', 'created', 'status', 'results'),
    'outbox': ('id', 'job_id', 'subject', 'body', 'attempts', 'due', 'state', 'error'),
    'wecom_outbox': ('id', 'job_id', 'destination', 'subject', 'body', 'attempts', 'due', 'state', 'error'),
    'mini_push_owners': ('identity', 'user_id'),
    'mini_push_subscriptions': ('id', 'nonce', 'account_id', 'destination', 'created', 'state'),
    'mini_push_outbox': ('id', 'job_id', 'subscription_id', 'destination', 'payload', 'attempts', 'due', 'state', 'error'),
    'users': ('id', 'username', 'password_hash', 'version', 'created'),
    'rate_limits': ('key', 'start', 'count'),
    'user_meta': ('user_id', 'status', 'nickname', 'note', 'last_active', 'initial_free'),
    'admin_events': ('id', 'user_id', 'kind', 'actor', 'reason', 'created'),
    'user_resets': ('digest', 'user_id', 'version', 'expires'),
    'wechat_links': ('identity', 'user_id', 'appid', 'created'),
    'wechat_tickets': ('digest', 'identity', 'appid', 'revision', 'browser', 'expires'),
    'wechat_codes': ('digest', 'expires'),
    'user_wallet': ('id', 'balance', 'free_slots', 'purchased_slots'),
    'credit_ledger': ('id', 'request_id', 'kind', 'delta', 'balance', 'free_slots', 'purchased_slots', 'actor', 'reason', 'created', 'fingerprint'),
}


def snapshot(directory):
    root = Path(directory)
    paths = [(1, root)]
    users = root / 'users'
    if users.exists():
        for child in sorted(users.iterdir()):
            if (child / 'checkin.sqlite3').exists():
                if not child.name.isdecimal() or int(child.name) <= 1:
                    raise ValueError('发现无法识别的用户数据目录，请先检查备份。')
                paths.append((int(child.name), child))
    data = {}
    digest = hashlib.sha256()
    for uid, folder in paths:
        source = folder / 'checkin.sqlite3'
        if not source.exists():
            if uid == 1:
                raise ValueError('未找到旧 SQLite 数据库，请勿对空目录执行迁移。')
            continue
        key_path = folder / 'secret.key'
        if not key_path.is_file():
            raise ValueError('旧数据库加密密钥缺失，请先恢复完整数据卷。')
        key = key_path.read_bytes()
        cipher = Fernet(key)
        db = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('旧 SQLite 完整性检查失败，请保留备份后检查。')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = {}
            for table in TABLES:
                if table not in tables:
                    continue
                if uid != 1 and table in ('users', 'rate_limits', 'user_meta', 'admin_events', 'user_resets', 'wechat_links', 'wechat_tickets', 'wechat_codes'):
                    continue
                cols = COLUMNS[table]
                rows[table] = [dict(r) for r in db.execute('SELECT ' + ','.join(cols) + ' FROM ' + table + ' ORDER BY 1')]
            for record in rows.get('settings', []):
                json.loads(cipher.decrypt(record['value'].encode()))
            ids = {r['id'] for r in rows.get('jobs', [])}
            for queue in ('outbox', 'wecom_outbox', 'mini_push_outbox'):
                if any(r['job_id'] is not None and r['job_id'] not in ids for r in rows.get(queue, [])):
                    raise ValueError('旧通知队列引用了不存在的运行记录，请先检查。')
            data[uid] = (folder, rows)
            digest.update(json.dumps([uid, hashlib.sha256(key).hexdigest(), rows], sort_keys=True, ensure_ascii=False).encode())
        finally:
            db.close()
    ids = {r['id'] for r in data[1][1].get('users', [])} or {1}
    if set(data) - ids:
        raise ValueError('用户目录与账号表不匹配，请先检查备份。')
    return data, digest.hexdigest()


def migrate(directory, url):
    import fcntl
    import psycopg
    from psycopg import sql
    if not url:
        raise ValueError('请先配置 PostgreSQL DATABASE_URL。')
    lock = open(Path(directory) / 'scheduler.lock', 'a')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('请先停止签到容器，再迁移数据。') from None
        data, fingerprint = snapshot(directory)
        root = Store(directory, database_url=url, migrating=True)
        try:
            # A session lease excludes another scheduler even on another data volume.
            root.backend.acquire_scheduler()
            stores = {1: root}
            for uid, (folder, _) in data.items():
                if uid != 1:
                    stores[uid] = Store(folder, database_url=url, user_id=uid, backend=root.backend, migrating=True)
            with root.connect() as db:
                db.serialize('sqlite-migration')
                marker = db.execute("SELECT value FROM storage_meta WHERE key='sqlite_import_complete'").fetchone()
                if marker:
                    if marker['value'] != fingerprint:
                        raise ValueError('此 PG 已迁移过不同的源数据，已拒绝覆盖。')
                    return {'already_migrated': True, 'users': len(stores), 'rows': sum(len(r) for _, tables in data.values() for r in tables.values())}
                for uid, store in stores.items():
                    db.connection.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(store.schema)))
                    current = db.connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (store.schema,)).fetchall()
                    for row in current:
                        if row['tablename'] in TABLES:
                            count = db.connection.execute(sql.SQL('SELECT COUNT(*) AS count FROM {}').format(sql.Identifier(row['tablename']))).fetchone()['count']
                            if count:
                                raise ValueError('目标 PG 已有业务数据，拒绝合并或覆盖；请使用空的签到专用库。')
                total = 0
                for uid, store in stores.items():
                    db.schema = store.schema
                    db.connection.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(store.schema)))
                    if uid == 1:
                        db.executescript(ACCOUNT_SCHEMA)
                    for table, records in data[uid][1].items():
                        cols = COLUMNS[table]
                        query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
                            sql.SQL(',').join(map(sql.Identifier, cols)), sql.SQL(',').join(sql.Placeholder() for _ in cols))
                        if records:
                            with db.connection.cursor() as cursor:
                                cursor.executemany(query, [tuple(r[c] for c in cols) for r in records])
                        # Read back every row, including ciphertext and persistent queue fields.
                        actual = db.connection.execute(sql.SQL('SELECT {} FROM {} ORDER BY 1').format(
                            sql.SQL(',').join(map(sql.Identifier, cols)), sql.Identifier(table))).fetchall()
                        if actual != records:
                            raise ValueError('迁移逐行核对失败，已回滚 PG 写入。')
                        if table in ('users', 'jobs', 'outbox', 'wecom_outbox', 'mini_push_subscriptions', 'mini_push_outbox'):
                            db.reset_sequence(table)
                        total += len(records)
                if snapshot(directory)[1] != fingerprint:
                    raise ValueError('源 SQLite 在迁移中发生变化，已回滚 PG 写入；请停止其他写入后再试。')
                db.connection.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(root.schema)))
                db.execute("INSERT INTO storage_meta(key,value) VALUES('sqlite_import_complete',?)", (fingerprint,))
            return {'already_migrated': False, 'users': len(stores), 'rows': total}
        finally:
            root.backend.close()
    finally:
        lock.close()
