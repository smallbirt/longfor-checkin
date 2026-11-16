"""Setup uses only the Python standard library. Other commands require requirements.txt."""
import argparse
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description='每日签到管理工具')
    p.add_argument('command', choices=['setup', 'import-har', 'check', 'sign', 'serve', 'reset-password', 'migrate-pg', 'check-db'])
    p.add_argument('files', nargs='*')
    args = p.parse_args()
    env_path = ROOT / '.env'
    if args.command == 'setup':
        if env_path.exists():
            print('已有 .env，未覆盖。管理密码在该文件中。')
            return
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write('ADMIN_PASSWORD=' + secrets.token_urlsafe(24) + '\nPORT=8091\nBIND_ADDRESS=127.0.0.1\nCOOKIE_SECURE=false\n')
        print('已生成 .env；管理密码保存在其中。运行 docker compose up -d --build。')
        return
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k, v)
    if args.command == 'serve':
        from app import create_app
        from waitress import serve
        serve(create_app(worker=True), host='127.0.0.1', port=int(os.environ.get('PORT', '8091')))
        return
    if args.command == 'migrate-pg':
        from app.migrate import migrate
        try:
            result = migrate(os.environ.get('DATA_DIR', str(ROOT / 'data')), os.environ.get('DATABASE_URL', ''))
        except Exception as exc:
            # Network/driver exceptions may contain connection details; never echo them.
            print(str(exc) if isinstance(exc, ValueError) else '迁移失败，PG 写入已回滚；请检查连通性、权限和原数据备份。')
            raise SystemExit(1)
        print(('此前已迁移，未覆盖现有 PG 数据。' if result['already_migrated'] else '迁移成功并逐行核对完成。') +
              ' 用户空间：%s，源记录：%s。' % (result['users'], result['rows']))
        return
    if args.command == 'check-db':
        from app.database import PostgreSQL
        try:
            backend = PostgreSQL(os.environ['DATABASE_URL'])
            with backend.connect('public') as db:
                row = db.execute('SELECT current_database() AS name, current_user AS role').fetchone()
            backend.close()
        except Exception:
            print('PostgreSQL 连接失败，请检查网络、账号、专用库及 DATABASE_URL。')
            raise SystemExit(1)
        print('PostgreSQL 连接正常；数据库：%s，账号：%s。' % (row['name'], row['role']))
        return
    from app.store import Store, now
    from app.importer import parse_har
    from app.runner import Runner
    store = Store(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    if args.command == 'reset-password':
        from getpass import getpass
        from app.accounts import Accounts
        if len(args.files) != 1:
            p.error('用法：python manage.py reset-password 用户名')
        admin_password = os.environ.get('ADMIN_PASSWORD', '')
        if len(admin_password) < 16:
            p.error('缺少有效的 ADMIN_PASSWORD。请在签到容器内执行。')
        accounts = Accounts(store, admin_password)
        username = args.files[0].strip().lower()
        with store.connect() as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if not row:
            p.error('账号不存在。')
        if row['id'] == 1:
            p.error('管理员密码请修改 .env 的 ADMIN_PASSWORD 后重建容器。')
        first = getpass('新密码（至少 10 位，不回显）：')
        if first != getpass('再次输入：'):
            p.error('两次密码不一致。')
        try:
            accounts.change_password(row['id'], first)
        except ValueError as exc:
            p.error(str(exc))
        print('密码已重置，旧登录会话已失效。')
        return
    if args.command == 'import-har':
        if not args.files:
            p.error('请提供 HAR 文件路径。')
        for path in args.files:
            if Path(path).stat().st_size > 50 * 1024 * 1024:
                p.error('文件超过 50 MB。')
            incoming = parse_har(Path(path).read_bytes())
            for key, cred in incoming.items():
                store.put('credential:' + key, {**cred, 'imported_at': now().isoformat()})
            print('已导入渠道：' + ', '.join(incoming) + '；没有发送网络请求。')
    else:
        # CLI must not race with a running web/scheduler process.
        print('请通过管理页执行查询／签到，以共享任务锁和运行记录。')
        raise SystemExit(2)


if __name__ == '__main__':
    main()
