"""Real local backend, existing local SQLite and .env; no scheduled work.

Unlike preview_web.py this does not inject fake transports or create demo users.
Local data never points at the cloud PostgreSQL instance.
"""
import argparse
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def main():
    p = argparse.ArgumentParser(description='启动真实签到接口（本地数据、无定时调度）')
    p.add_argument('--port', type=int, default=18091)
    args = p.parse_args()
    settings = {}
    env = PROJECT / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                settings[k.strip()] = v.strip()
    password = settings.get('ADMIN_PASSWORD')
    if not password or len(password) < 16:
        raise SystemExit('缺少本地 ADMIN_PASSWORD；请先运行 manage.py setup。')
    # Do not inherit a production DB URL or launch another scheduler locally.
    os.environ['DATABASE_URL'] = ''
    os.environ['COOKIE_SECURE'] = 'false'
    from app import create_app
    from waitress import serve
    app = create_app(data_dir=str(PROJECT / 'data'), password=password, worker=False)
    app.config['LOCAL_DEBUG'] = True
    print('本地真实后端：http://127.0.0.1:%s' % args.port, flush=True)
    print('数据：本地现有 SQLite；账号与密码沿用本地配置。', flush=True)
    print('定时调度与通知后台投递未启动；手动签到、HAR 校验、短信和邮箱使用真实接口，小程序推送需先配置模板。', flush=True)
    serve(app, host='127.0.0.1', port=args.port, threads=6,
          max_request_body_size=50 * 1024 * 1024)


if __name__ == '__main__':
    main()
