"""Isolated functional Web preview. Temporary SQLite; all transports are local fakes."""
import json
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import MagicMock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'tests'))
os.environ['DATABASE_URL'] = ''
os.environ['COOKIE_SECURE'] = 'false'
os.environ['MINI_SMS_EXPERIMENTAL'] = 'false'
from app import create_app
from app.program_accounts import ProgramAccounts
from app.entitlements import Wallet
from app.store import now
from test_core import credential
from test_login import PROFILE
from waitress import serve


class PreviewClient:
    def __init__(self):
        self.signed = set()

    def run(self, cred, day, allow_sign=False):
        token = cred['headers']['token']
        if 'expired' in token:
            state = 'auth_expired'
        else:
            if allow_sign:
                self.signed.add((token, day))
            state = 'signed' if (token, day) in self.signed else 'unsigned'
        time.sleep(.08)
        return {'state': state, 'message': {'signed': '已向服务端确认今日已签到。', 'unsigned': '服务端显示今日未签到。', 'auth_expired': '登录失效，请更新此账号的登录文件。'}[state], 'checked_at': now().isoformat()}


directory = tempfile.TemporaryDirectory(prefix='longfor-web-preview-')
network = PreviewClient()
sms = MagicMock()
sms.verify.return_value = 'a' * 32
app = create_app(directory.name, password='preview-admin-not-a-real-account', worker=False, client=network, login_client=sms)
app.jinja_env.auto_reload = True
app.config['WEB_PREVIEW'] = True
# Patches affect this preview process only: no SMTP or third-party requests.
import app as app_module
app_module.send_mail = lambda *a, **kw: (True, '')
root = app.extensions['store']
root.put('mail', {'provider': 'qq', 'sender': 'demo@qq.com', 'password': 'preview-only', 'recipient': 'demo@qq.com'})
user = app.extensions['accounts'].register('demo', 'demo-password-2026')
store, runner = app.extensions['workspaces'].get(user['id'])
store.put('notifications', {'recipient': 'demo@qq.com', 'verified': True})
store.put('schedule_enabled', True)
store.put('heartbeat', now().isoformat())
registry = ProgramAccounts(store)
Wallet(store).change('quota', 6, '预览用户的额外名额', 'admin', 'preview-quota-20261027')
Wallet(store).change('grant', 30, '预览积分', 'admin', 'preview-grant-20261027')
for i, (channel, alias, signed, enabled) in enumerate([
    ('app', '主号', True, True), ('app', '家人号', False, True), ('app', '备用号', False, False),
    ('mini', '主号', True, True), ('mini', '家人号', False, True)
]):
    item = registry.create(channel, alias)
    scoped = registry.scoped(item['id'])
    cred = credential(channel)
    cred['headers']['token'] = 'preview-expired' if i == 1 else 'preview-account-' + str(i)
    cred['imported_at'] = now().isoformat()
    scoped.put('credential:' + channel, cred)
    scoped.put('login-profile:' + channel, {**PROFILE, 'phone_hint': ['138****0826', '139****1658', '136****0932'][i % 3]})
    registry.update(item['id'], {'enabled': enabled})
    if signed:
        network.signed.add((cred['headers']['token'], now().date().isoformat()))
    result = network.run(cred, now().date().isoformat())
    scoped.put('status:' + channel, {**result, 'day': now().date().isoformat()})
    runner.run('check', target=item['id'])

for username, nickname, balance in [('xiaoyu', '小雨', 20), ('xiaolu', '小鹿', 5)]:
    sample = app.extensions['accounts'].register(username, 'demo-password-2026')
    child, _ = app.extensions['workspaces'].get(sample['id'])
    with root.connect() as db:
        db.execute('UPDATE user_meta SET nickname=? WHERE user_id=?', (nickname, sample['id']))
    Wallet(child).change('grant', balance, '预览积分', 'admin', 'preview-grant-' + username)

parser = argparse.ArgumentParser(description='启动隔离网页预览，不使用真实数据或传输')
parser.add_argument('--port', type=int, default=18091)
args = parser.parse_args()
print('Web preview: http://127.0.0.1:%s / user demo / password demo-password-2026' % args.port, flush=True)
print('Temporary data; scheduler disabled; SMS/check-in/mail use preview transports.', flush=True)
serve(app, host='127.0.0.1', port=args.port, threads=4)
