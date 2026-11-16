"""One user consent, one future failed review across the user's accounts."""
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import requests
from flask import g, jsonify, request
from urllib.parse import parse_qs, urlsplit
from .store import now
from .program_accounts import ProgramAccounts
from .constants import CHANNELS

PLATFORM = 'mini_push_platform'
BINDING = 'mini_push_binding'
ALL_ACCOUNTS = '*'
API = 'https://api.weixin.qq.com/'


def validate_config(incoming, old=None):
    old = old or {}
    if set(incoming) - {'appid', 'secret', 'template_id', 'fields', 'enabled', 'environment', 'scenario'}:
        raise ValueError('小程序推送配置字段无效。')
    appid = incoming.get('appid', '')
    secret = incoming.get('secret', '')
    template = incoming.get('template_id', '')
    fields = incoming.get('fields')
    if not isinstance(appid, str) or not re.fullmatch(r'wx[0-9a-f]{16}', appid):
        raise ValueError('请填写本签到小程序的 AppID。')
    if not isinstance(secret, str):
        raise ValueError('请填写有效的 AppSecret。')
    if not secret and old.get('appid') == appid:
        secret = old.get('secret', '')
    if (secret or incoming.get('enabled')) and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', secret):
        raise ValueError('请填写小程序 AppSecret；同一 AppID 留空可保留原密钥。')
    if not isinstance(template, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,128}', template):
        raise ValueError('请填写已在微信后台选用的订阅消息模板 ID。')
    if (not isinstance(fields, dict) or set(fields) != {'account', 'result', 'time'}
            or not all(isinstance(v, str) for v in fields.values())
            or len(set(fields.values())) != 3
            or not re.fullmatch(r'thing0*[1-9][0-9]*', fields['account'])
            or not re.fullmatch(r'(thing|phrase)0*[1-9][0-9]*', fields['result'])
            or not re.fullmatch(r'(time|date)0*[1-9][0-9]*', fields['time'])):
        raise ValueError('请选择包含账号、结果、时间三项的模板，并填写对应的 thing、thing/phrase、time/date 字段名。')
    if type(incoming.get('enabled')) is not bool or incoming.get('environment') not in ('developer', 'trial', 'formal'):
        raise ValueError('请选择有效的推送状态和小程序版本。')
    scenario = incoming.get('scenario', 'failure')
    if scenario not in ('review', 'failure'):
        raise ValueError('请选择有效的通知用途。')
    # Accept the legacy value from older admin clients, but only send failures.
    return {**incoming, 'scenario': 'failure', 'secret': secret, 'revision': secrets.token_urlsafe(24)}


class WechatPushClient:
    def __init__(self):
        self.tokens = {}
        self.lock = threading.Lock()

    @staticmethod
    def request(method, path, **kwargs):
        with requests.Session() as transport:
            response = transport.request(method, API + path, timeout=(5, 15), allow_redirects=False, **kwargs)
        if response.status_code != 200 or len(response.content) > 1024 * 1024:
            raise ValueError('invalid_response')
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError('invalid_response')
        return value

    def exchange(self, config, code):
        try:
            result = self.request('GET', 'sns/jscode2session', params={
                'appid': config['appid'], 'secret': config['secret'], 'js_code': code,
                'grant_type': 'authorization_code'})
            if result.get('errcode', 0) != 0 or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', str(result.get('openid', ''))):
                raise ValueError()
            # session_key and unionid are intentionally discarded.
            return result['openid']
        except (requests.RequestException, ValueError):
            raise ValueError('微信身份校验失败，请重新准备订阅；管理员可检查 AppID、AppSecret 和服务器网络。') from None

    def token(self, config, refresh=False):
        with self.lock:
            cached = self.tokens.get(config['revision'])
            if not refresh and cached and cached[1] > time.time():
                return cached[0]
            result = self.request('GET', 'cgi-bin/token', params={
                'grant_type': 'client_credential', 'appid': config['appid'], 'secret': config['secret']})
            if result.get('errcode', 0) != 0 or not isinstance(result.get('access_token'), str) or not result['access_token']:
                raise ValueError()
            duration = result.get('expires_in')
            if type(duration) is not int or duration < 120:
                raise ValueError()
            self.tokens = {config['revision']: (result['access_token'], time.time() + duration - 60)}
            return result['access_token']

    def send(self, config, binding, payload):
        try:
            token = self.token(config)
        except (requests.RequestException, ValueError):
            return 'pending', '获取微信推送凭证失败，请检查平台配置或网络。'
        for attempt in range(2):
            try:
                result = self.request('POST', 'cgi-bin/message/subscribe/send',
                    params={'access_token': token}, json={**payload, 'touser': binding['openid']})
            except requests.ConnectTimeout:
                return 'pending', '连接微信超时，将稍后重试。'
            except (requests.RequestException, ValueError):
                # The one-time permission might have been consumed. Never blindly resend.
                return 'uncertain', '发送结果未确认，为避免重复消耗订阅次数，不自动重发。'
            code = result.get('errcode')
            if type(code) is int and code == 0:
                return 'sent', ''
            if code in (40001, 40014, 42001) and attempt == 0:
                try:
                    token = self.token(config, refresh=True)
                    continue
                except (requests.RequestException, ValueError):
                    return 'pending', '微信推送凭证更新失败，将稍后重试。'
            if code in (45009, -1):
                return 'pending', '微信服务繁忙或发送受限，将稍后重试。'
            if code == 43101:
                return 'failed', '微信未授予此次发送权限或订阅已取消，请在小程序重新订阅。'
            if code in (40037, 47003):
                return 'failed', '订阅模板或字段不匹配，请管理员核对平台推送配置。'
            if type(code) is not int:
                return 'uncertain', '微信返回无效结果，为避免重复通知，不自动重发。'
            return 'failed', '微信拒绝推送，请检查模板、接收权限及平台配置。'


class MiniPush:
    def __init__(self, root, client=None):
        self.root = root
        self.client = client or WechatPushClient()
        self.config_lock = threading.RLock()

    def config(self):
        value = self.root.get(PLATFORM)
        return value if value and value.get('enabled') and value.get('secret') else None

    def platform_summary(self):
        value = self.root.get(PLATFORM) or {}
        return {k: v for k, v in value.items() if k not in ('secret', 'revision')} | {
            'scenario': 'failure',
            'configured': bool(value.get('secret')), 'draft': bool(value and not value.get('secret'))}

    def binding(self, store):
        config = self.config()
        value = store.get(BINDING)
        return value if config and value and value.get('config_revision') == config['revision'] else None

    def public(self, store):
        value = self.binding(store)
        subscriptions = []
        if value:
            with store.connect() as db:
                remaining = db.execute(
                    "SELECT COUNT(*) AS remaining FROM mini_push_subscriptions WHERE destination=? AND state='available'",
                    (value['version'],)).fetchone()['remaining']
                if remaining:
                    subscriptions = [{'account_id': ALL_ACCOUNTS, 'remaining': remaining}]
        return {'configured': bool(self.config()), 'bound': bool(value),
                'enabled': bool(value and value.get('enabled')), 'subscriptions': subscriptions,
                'scope': 'user', 'scenario': 'failure'}

    def ready(self, store):
        return self.public(store)['enabled']

    def prepare(self, store, account_id=None, code=None):
        # Old clients may still send an account. Validate ownership, but grant
        # the same user-wide subscription as the new account-free flow.
        if account_id is not None:
            ProgramAccounts(store).require(account_id)
        config = self.config()
        if not config:
            raise ValueError('管理员尚未配置并开启小程序推送，请先使用邮箱通知。')
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', code):
            raise ValueError('请在微信小程序中重新获取登录凭证。')
        openid = self.client.exchange(config, code)
        identity = hmac.new(self.root.key, (config['appid'] + ':' + openid).encode(), hashlib.sha256).hexdigest()
        old = store.get(BINDING) or {}
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            for link in db.execute('SELECT identity,user_id FROM wechat_links WHERE identity=? OR user_id=?', (identity, store.user_id)).fetchall():
                if link['identity'] != identity or link['user_id'] != store.user_id:
                    raise ValueError('当前微信与微信登录绑定不一致，请先在“我的”中确认绑定账号。')
            db.execute('INSERT INTO mini_push_owners(identity,user_id) VALUES (?,?) ON CONFLICT(identity) DO NOTHING', (identity, store.user_id))
            owner = db.execute('SELECT user_id FROM mini_push_owners WHERE identity=?', (identity,)).fetchone()
            if owner['user_id'] != store.user_id:
                raise ValueError('当前微信已绑定其他本站账号，请使用原账号订阅或先解除绑定。')
        same = old.get('openid') == openid and old.get('config_revision') == config['revision']
        value = {**old, 'openid': openid, 'identity': identity, 'appid': config['appid'],
                 'config_revision': config['revision'], 'version': old['version'] if same else secrets.token_urlsafe(24),
                 'enabled': bool(same and old.get('enabled')),
                 'intent': {'nonce': secrets.token_urlsafe(24), 'account_id': ALL_ACCOUNTS, 'expires': time.time() + 600}}
        store.put(BINDING, value)
        if not same:
            self.cancel(store)
        if old.get('identity') and old['identity'] != identity:
            with self.root.connect() as db:
                db.execute('DELETE FROM mini_push_owners WHERE identity=? AND user_id=?', (old['identity'], store.user_id))
        return {'nonce': value['intent']['nonce'], 'template_id': config['template_id'],
                'expires_at': value['intent']['expires'], 'appid': config['appid'], 'account_id': account_id or ALL_ACCOUNTS, **self.public(store)}

    def accept(self, store, nonce):
        binding = self.binding(store)
        intent = (binding or {}).get('intent', {})
        with store.connect() as db:
            existing = db.execute('SELECT destination,state FROM mini_push_subscriptions WHERE nonce=?', (nonce if isinstance(nonce, str) else '',)).fetchone()
        if (existing and binding and binding.get('enabled') and existing['destination'] == binding['version']
                and existing['state'] != 'cancelled'):
            return  # A lost acknowledgement can be retried even after another page prepared.
        if not isinstance(nonce, str) or nonce != intent.get('nonce'):
            raise ValueError('订阅准备已失效，请重新准备。')
        if intent.get('expires', 0) < time.time():
            raise ValueError('订阅准备已过期，请重新准备。')
        binding['enabled'] = True
        store.put(BINDING, binding)
        with store.connect() as db:
            db.execute('INSERT INTO mini_push_subscriptions(nonce,account_id,destination,created) VALUES (?,?,?,?) ON CONFLICT(nonce) DO NOTHING',
                       (nonce, ALL_ACCOUNTS, binding['version'], now().isoformat()))

    @staticmethod
    def cancel(store):
        with store.connect() as db:
            db.execute("UPDATE mini_push_subscriptions SET state='cancelled' WHERE state='available'")
            db.execute("UPDATE mini_push_outbox SET state='cancelled',error='' WHERE state IN ('pending','failed')")

    def enqueue(self, store, db, job_id, kind, results, dt):
        if kind != 'afternoon':
            return
        prefs = store.get('notification_events', {})
        config, binding = self.config(), self.binding(store)
        if not binding or not binding.get('enabled'):
            return
        if not prefs.get('failure', True):
            return
        # Retrying enqueue for a completed job must not consume a second grant.
        if db.execute('SELECT id FROM mini_push_outbox WHERE job_id=?', (job_id,)).fetchone():
            return
        job_created = db.execute('SELECT created FROM jobs WHERE id=?', (job_id,)).fetchone()['created']
        for result in results:
            if result['state'] == 'signed':
                continue  # Keep this permission for the next abnormal scheduled review.
            # Includes existing unspent grants from the old account selector.
            # The workspace connection already isolates the current site user.
            grant = db.execute("SELECT * FROM mini_push_subscriptions WHERE state='available' AND destination=? AND created<=? ORDER BY id LIMIT 1",
                               (binding['version'], job_created)).fetchone()
            if not grant:
                continue
            fields = config['fields']
            short = '已签到' if result['state'] == 'signed' else '未签到' if result['state'] == 'unsigned' else '需处理'
            content = short if fields['result'].startswith('phrase') else re.sub(r'[\x00-\x1f\x7f]', ' ', short + '，' + result['message'])[:20]
            receipt = secrets.token_urlsafe(24)
            payload = {'template_id': config['template_id'], 'page': 'pages/result/index?job_id=' + str(job_id) + '&account_id=' + result['account_id'] + '&receipt=' + receipt,
                       'miniprogram_state': config['environment'], 'lang': 'zh_CN', 'data': {
                           fields['account']: {'value': (CHANNELS[result['channel']]['name'] + ' · ' + result['account_alias'])[:20]},
                           fields['result']: {'value': content}, fields['time']: {'value': dt.strftime('%Y-%m-%d %H:%M')}}}
            db.execute('INSERT INTO mini_push_outbox(job_id,subscription_id,destination,payload) VALUES (?,?,?,?) ON CONFLICT(job_id) DO NOTHING',
                       (job_id, grant['id'], binding['version'], json.dumps(payload, ensure_ascii=False)))
            db.execute("UPDATE mini_push_subscriptions SET state='queued' WHERE id=?", (grant['id'],))
            break  # A legacy combined job still consumes only one one-time permission.

    def deliver(self, store):
        config, binding = self.config(), self.binding(store)
        if not binding or not binding.get('enabled'):
            self.cancel(store)
            return
        with store.connect() as db:
            db.execute("UPDATE mini_push_outbox SET state='cancelled',error='' WHERE state IN ('pending','failed') AND destination<>?", (binding['version'],))
            row = db.execute("SELECT * FROM mini_push_outbox WHERE state='pending' AND due<=? ORDER BY id LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return
            db.execute("UPDATE mini_push_outbox SET state='sending' WHERE id=?", (row['id'],))
        try:
            state, error = self.client.send(config, binding, json.loads(row['payload']))
        except Exception:
            state, error = 'uncertain', '发送结果未确认，为避免重复消耗订阅次数，不自动重发。'
        attempts = row['attempts'] + 1
        if state == 'pending' and attempts >= 4:
            state = 'failed'
        with store.connect() as db:
            db.execute('UPDATE mini_push_outbox SET state=?,error=?,attempts=?,due=? WHERE id=?',
                       (state, error, attempts, time.time() + [60, 300, 1800, 3600][min(attempts - 1, 3)], row['id']))


def install_routes(app, root, spaces, auth, admin, data, rate):
    push = spaces.push

    @app.post('/api/admin/mini-push')
    @admin
    def mini_push_config():
        config = validate_config(data(), root.get(PLATFORM))
        with push.config_lock:
            root.put(PLATFORM, config)
        message = ('模板配置已保存，填写 AppSecret 并开启通道后才可订阅。' if not config['secret']
                   else '小程序推送配置已保存，请用户在小程序重新订阅下一次复核通知。')
        return jsonify(message=message)

    @app.get('/api/mini-push')
    @auth
    def mini_push_state():
        return jsonify(push.public(g.store))

    @app.post('/api/mini-push/prepare')
    @auth
    def mini_push_prepare():
        incoming = data()
        if set(incoming) not in ({'code'}, {'account_id', 'code'}):
            raise ValueError('请在小程序中为本人账号准备订阅。')
        rate('push-prepare:' + str(g.user['id']), 20, 300)
        with g.runner.mail_lock, push.config_lock, g.store.lock:
            return jsonify(push.prepare(g.store, incoming.get('account_id'), incoming['code']))

    @app.post('/api/mini-push/subscribe')
    @auth
    def mini_push_subscribe():
        incoming = data()
        if set(incoming) != {'nonce', 'accepted'} or incoming['accepted'] is not True:
            raise ValueError('请先在微信订阅弹窗中允许接收通知。')
        with g.runner.mail_lock, push.config_lock, g.store.lock:
            push.accept(g.store, incoming['nonce'])
        state = push.public(g.store)
        message = '已订阅全部账号的下一次复核异常提醒；任一账号复核失败时发送一条，全部成功不消耗订阅。'
        return jsonify(message=message, **state)

    @app.post('/api/mini-push/pause')
    @auth
    def mini_push_pause():
        with g.runner.mail_lock, push.config_lock, g.store.lock:
            binding = g.store.get(BINDING)
            if binding:
                binding['enabled'] = False
                binding.pop('intent', None)
                g.store.put(BINDING, binding)
            push.cancel(g.store)
        return jsonify(message='小程序提醒已暂停，未发送的提醒已取消。重新开启请在小程序订阅。')

    @app.post('/api/mini-push/unbind')
    @auth
    def mini_push_unbind():
        with g.runner.mail_lock, push.config_lock, g.store.lock:
            binding = g.store.get(BINDING) or {}
            g.store.put(BINDING, None)
            push.cancel(g.store)
            with root.connect() as db:
                db.execute('DELETE FROM mini_push_owners WHERE identity=? AND user_id=?', (binding.get('identity', ''), g.user['id']))
        return jsonify(message='已解除当前微信推送绑定。')

    @app.get('/api/jobs/<int:job_id>')
    @auth
    def mini_push_result(job_id):
        receipt = request.args.get('receipt')
        if receipt:
            with g.store.connect() as db:
                row = db.execute('SELECT payload FROM mini_push_outbox WHERE job_id=?', (job_id,)).fetchone()
            expected = parse_qs(urlsplit(json.loads(row['payload'])['page']).query).get('receipt', [''])[0] if row else ''
            if not expected or not hmac.compare_digest(receipt, expected):
                return jsonify(error='此通知不属于当前登录账号，请切换到接收通知时使用的本站账号。'), 404
        rows = g.store.jobs(limit=1, before=job_id + 1)
        if not rows or rows[0]['id'] != job_id:
            return jsonify(error='此运行记录不存在。'), 404
        return jsonify(job=rows[0])
