"""Isolated WeChat login preview. Never exchanges codes or contacts third parties."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL']=''
os.environ['COOKIE_SECURE']='false'
from app import create_app
from app.entitlements import Wallet
from waitress import serve

directory=tempfile.TemporaryDirectory(prefix='longfor-wechat-preview-')
app=create_app(directory.name,password='preview-admin-not-a-real-account',client=MagicMock())
app.config['WEB_PREVIEW']=True
service=app.extensions['wechat_auth']
service.save({'appid':'wx5442ffd1e656a143','secret':'preview-only-secret-20261027','enabled':True},'admin')
service.client=MagicMock()
service.client.exchange.return_value='preview-only-wechat-identity'
user=app.extensions['accounts'].register('demo','demo-password-2026')
store,_=app.extensions['workspaces'].get(user['id'])
Wallet(store).change('grant',20,'isolated preview','admin','wechat-preview-20261027')
print('Isolated WeChat preview: http://127.0.0.1:18098; demo / demo-password-2026',flush=True)
print('Fake WeChat exchange; no production data or external transports.',flush=True)
serve(app,host='127.0.0.1',port=18098,threads=4)
