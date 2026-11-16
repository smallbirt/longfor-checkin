import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet
from .constants import TZ
from .database import Session, PostgreSQL, SCHEMA


def now():
    return datetime.now(TZ)


class Store:
    def __init__(self, directory, database_url=None, user_id=1, backend=None, migrating=False):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.path = self.root / 'checkin.sqlite3'
        self.database_url = os.environ.get('DATABASE_URL', '') if database_url is None else database_url
        if not isinstance(user_id, int) or user_id < 1:
            raise ValueError('用户空间无效。')
        self.user_id, self.schema = user_id, 'checkin_u' + str(user_id)
        key_path = self.root / 'secret.key'
        if not key_path.exists():
            if self.path.exists():
                raise RuntimeError('已有签到数据库但加密密钥缺失，请恢复原 secret.key。')
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.key = key_path.read_bytes()
        self.cipher = Fernet(self.key)
        self.backend = backend or (PostgreSQL(self.database_url) if self.database_url else None)
        self.is_postgres = self.backend is not None
        try:
            self._initialize(migrating)
        except Exception:
            if backend is None and self.backend:
                self.backend.close()
            raise

    def _initialize(self, migrating):
        with self.connect() as db:
            if self.is_postgres:
                from psycopg import sql
                db.execute('SELECT pg_advisory_xact_lock(hashtext(?))', ('initialize:' + self.schema,))
                db.connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.schema)))
                db.execute('CREATE TABLE IF NOT EXISTS storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                fingerprint = hashlib.sha256(self.key).hexdigest()
                row = db.execute("SELECT value FROM storage_meta WHERE key='key_sha256'").fetchone()
                if row and row['value'] != fingerprint:
                    raise RuntimeError('数据卷加密密钥与 PostgreSQL 不匹配，请挂载原数据卷。')
                db.execute("INSERT INTO storage_meta(key,value) VALUES('key_sha256',?) ON CONFLICT(key) DO NOTHING", (fingerprint,))
            db.executescript(SCHEMA)
            # Retired transport: preserve historical rows, permanently stop old deliveries.
            db.execute("UPDATE wecom_outbox SET state='cancelled',error='' WHERE state IN ('pending','failed')")
            if self.is_postgres and not migrating and self.user_id == 1 and self.path.exists():
                ready = db.execute("SELECT value FROM storage_meta WHERE key='sqlite_import_complete'").fetchone()
                if not ready:
                    raise RuntimeError('检测到旧 SQLite 数据，请先运行 manage.py migrate-pg，避免启动空白账号。')
        if not self.is_postgres:
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        if self.is_postgres:
            with self.backend.connect(self.schema) as db:
                yield db
        else:
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield Session(db)
            finally:
                db.close()

    def child(self, user_id):
        return Store(self.root / 'users' / str(user_id), self.database_url, user_id, self.backend)

    def integrity_error(self, exc):
        if self.is_postgres:
            import psycopg
            return isinstance(exc, psycopg.IntegrityError)
        return isinstance(exc, sqlite3.IntegrityError)

    def ping(self):
        try:
            with self.connect() as db:
                db.execute('SELECT 1').fetchone()
            return True
        except Exception:
            return False

    def get(self, key, default=None):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(self.cipher.decrypt(row['value'].encode())) if row else default

    def put(self, key, value):
        token = self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO settings(key,value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, token))

    def jobs(self, limit=30, before=None):
        with self.connect() as db:
            query = ('SELECT j.*, o.state AS mail_state, o.error AS mail_error, '
                     'p.state AS mini_push_state, p.error AS mini_push_error '
                     'FROM jobs j LEFT JOIN outbox o ON o.job_id=j.id '
                     'LEFT JOIN mini_push_outbox p ON p.job_id=j.id ')
            params = []
            if before is not None:
                query += 'WHERE j.id < ? '
                params.append(before)
            rows = db.execute(query + 'ORDER BY j.id DESC LIMIT ?', (*params, limit)).fetchall()
        return [{**dict(r), 'results': json.loads(r['results'])} for r in rows]
