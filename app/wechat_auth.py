"""Explicit one-to-one WeChat login links, separate from notification consent."""
import hashlib
import hmac
import json
import re
import secrets
import time
from flask import g, jsonify, request, session
from .mini_push import PLATFORM, WechatPushClient
from .store import now

CONFIG = 'wechat_login_platform'


class WechatAuth:
    def __init__(self, root, accounts, client=None):
        self.root, self.accounts = root, accounts
        self.client = client or WechatPushClient()

    def stored(self, db=None):
        if db is None:
            return self.root.get(CONFIG, {})
        row = db.execute('SELECT value FROM settings WHERE key=?', (CONFIG,)).fetchone()
        return json.loads(self.root.cipher.decrypt(row['value'].encode())) if row else {}

    def config(self):
        value = self.stored()
        return value if value.get('enabled') and value.get('secret') else None

    def summary(self):
        value = self.stored()
        push = self.root.get(PLATFORM, {})
        return {'appid': value.get('appid', push.get('appid', 'wx5442ffd1e656a143')),
                'enabled': bool(value.get('enabled')), 'configured': bool(value.get('secret')),
                'can_reuse_push_secret': bool(push.get('secret') and
                    push.get('appid') == value.get('appid', push.get('appid')))}

    def save(self, incoming, actor):
        if set(incoming) != {'appid', 'secret', 'enabled'}:
            raise ValueError('微信登录配置字段无效。')
        appid, secret, enabled = incoming['appid'], incoming['secret'], incoming['enabled']
        if not isinstance(appid, str) or not re.fullmatch(r'wx[0-9a-f]{16}', appid) or type(enabled) is not bool or not isinstance(secret, str):
            raise ValueError('请填写有效的 AppID、AppSecret 和登录开关。')
        push = self.root.get(PLATFORM, {})
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            old = self.stored(db)
            if old.get('appid') != appid and db.execute('SELECT 1 FROM wechat_links LIMIT 1').fetchone():
                raise ValueError('已有微信绑定，请先解除绑定后再更换小程序 AppID。')
            if not secret:
                secret = old.get('secret', '') if old.get('appid') == appid else ''
                if not secret and push.get('appid') == appid:
                    secret = push.get('secret', '')
            if (secret or enabled) and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', secret):
                raise ValueError('开启微信登录前，请填写 AppSecret。')
            value = {'appid': appid, 'secret': secret, 'enabled': enabled, 'revision': secrets.token_urlsafe(24)}
            token = self.root.cipher.encrypt(json.dumps(value).encode()).decode()
            db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (CONFIG, token))
            db.execute('DELETE FROM wechat_tickets')
            self.accounts.record_event(db, 0, '更新微信登录配置', actor, '开启微信登录。' if enabled else '关闭微信登录。')

    def digest(self, value):
        return hmac.new(self.root.key, value.encode(), hashlib.sha256).hexdigest()

    def exchange(self, code):
        config = self.config()
        if not config:
            raise ValueError('管理员尚未开启微信登录，请先使用账号密码登录。')
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', code):
            raise ValueError('微信登录凭证无效，请重新点击微信登录。')
        # Reserve each one-time code before the external call, including concurrent requests.
        digest = self.digest('wechat-code:' + config['appid'] + ':' + code)
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            db.execute('DELETE FROM wechat_codes WHERE expires<?', (time.time(),))
            if db.execute('SELECT 1 FROM wechat_codes WHERE digest=?', (digest,)).fetchone():
                raise ValueError('此微信登录凭证已使用，请重新点击微信登录。')
            db.execute('INSERT INTO wechat_codes(digest,expires) VALUES(?,?)', (digest, time.time() + 600))
        try:
            openid = self.client.exchange(config, code)
        except ValueError:
            raise ValueError('微信登录校验失败，请重试；管理员可检查 AppID、AppSecret 和服务器网络。') from None
        return self.digest(config['appid'] + ':' + openid), config

    def current(self, db, config):
        value = self.stored(db)
        if not value.get('enabled') or value.get('revision') != config['revision']:
            raise ValueError('微信登录配置已更新，请重新点击微信登录。')

    def owner_or_ticket(self, identity, config, browser):
        token = secrets.token_urlsafe(32)
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            self.current(db, config)
            row = db.execute('SELECT u.id,u.username,u.version,m.status FROM wechat_links l JOIN users u ON u.id=l.user_id JOIN user_meta m ON m.user_id=u.id WHERE l.identity=? AND l.appid=?', (identity, config['appid'])).fetchone()
            if row:
                if row['status'] != 'active':
                    raise ValueError('此账号已停用，请联系管理员。')
                return dict(row), None
            db.execute('DELETE FROM wechat_tickets WHERE expires<? OR browser=?', (time.time(), browser))
            db.execute('INSERT INTO wechat_tickets(digest,identity,appid,revision,browser,expires) VALUES(?,?,?,?,?,?)',
                       (self.digest(token), identity, config['appid'], config['revision'], browser, time.time() + 600))
        return None, token

    def claim(self, db, identity, config, user):
        self.current(db, config)
        current = db.execute('SELECT u.id,u.username,u.version,m.status FROM users u JOIN user_meta m ON m.user_id=u.id WHERE u.id=?', (user['id'],)).fetchone()
        if not current or current['status'] != 'active' or current['version'] != user['version']:
            raise ValueError('账号状态已变化，请重新验证账号密码。')
        for row in db.execute('SELECT identity,user_id FROM wechat_links WHERE identity=? OR user_id=?', (identity, user['id'])).fetchall():
            if row['identity'] != identity or row['user_id'] != user['id']:
                raise ValueError('微信或本站账号已绑定其他对象，请先使用原账号解绑。')
        for row in db.execute('SELECT identity,user_id FROM mini_push_owners WHERE identity=? OR user_id=?', (identity, user['id'])).fetchall():
            if row['identity'] != identity or row['user_id'] != user['id']:
                raise ValueError('已有其他微信推送绑定，请先在原账号的通知设置解除后重试。')
        existing = db.execute('SELECT 1 FROM wechat_links WHERE identity=?', (identity,)).fetchone()
        if not existing:
            db.execute('INSERT INTO wechat_links(identity,user_id,appid,created) VALUES(?,?,?,?)', (identity, user['id'], config['appid'], now().isoformat()))
            self.accounts.record_event(db, user['id'], '绑定微信登录', user['username'], '本人验证本站账号密码后绑定微信。')
        db.execute('DELETE FROM wechat_tickets WHERE identity=?', (identity,))
        return dict(current)

    def bind_ticket(self, token, browser, user):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', token):
            raise ValueError('绑定已过期，请重新点击微信登录。')
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            ticket = db.execute('SELECT * FROM wechat_tickets WHERE digest=? AND browser=?', (self.digest(token), browser)).fetchone()
            if not ticket or ticket['expires'] < time.time():
                raise ValueError('绑定已过期或不属于当前会话，请重新点击微信登录。')
            config = self.stored(db)
            if config.get('revision') != ticket['revision']:
                raise ValueError('微信登录配置已更新，请重新点击微信登录。')
            return self.claim(db, ticket['identity'], config, user)

    def bind(self, identity, config, user):
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            return self.claim(db, identity, config, user)

    def status(self, uid):
        with self.root.connect() as db:
            row = db.execute('SELECT created FROM wechat_links WHERE user_id=?', (uid,)).fetchone()
        return {'bound': bool(row), 'bound_at': row['created'] if row else '', 'enabled': bool(self.config())}

    def unbind(self, user):
        with self.root.lock, self.root.connect() as db:
            db.serialize('accounts')
            row = db.execute('SELECT version FROM users WHERE id=?', (user['id'],)).fetchone()
            if not row or row['version'] != user['version']:
                raise ValueError('账号状态已变化，请重新登录。')
            link = db.execute('SELECT identity FROM wechat_links WHERE user_id=?', (user['id'],)).fetchone()
            if link:
                db.execute('DELETE FROM wechat_links WHERE user_id=?', (user['id'],))
                db.execute('DELETE FROM wechat_tickets WHERE identity=?', (link['identity'],))
                db.execute('UPDATE users SET version=version+1 WHERE id=?', (user['id'],))
                self.accounts.record_event(db, user['id'], '解除微信登录', user['username'], '本人验证密码后解绑，其他设备登录已失效。')
        return self.accounts.get(user['id'])


def install_routes(app, root, accounts, auth, admin, data, rate, establish, payload):
    service = WechatAuth(root, accounts)
    app.extensions['wechat_auth'] = service

    def limited():
        rate('wechat-login-ip:' + (request.remote_addr or ''), 30, 300)

    def verified(username, password):
        if not accounts.allow('login-ip:' + (request.remote_addr or ''), 30, 300):
            raise ValueError('尝试过多，请 5 分钟后重试。')
        user = accounts.authenticate(username, password)
        if not user:
            raise ValueError('账号密码不正确或账号已停用。')
        return user

    @app.get('/api/admin/wechat-login')
    @admin
    def wechat_admin_state():
        return jsonify(service.summary())

    @app.post('/api/admin/wechat-login')
    @admin
    def wechat_admin_save():
        service.save(data(), g.user['username'])
        return jsonify(message='微信登录配置已保存。', **service.summary())

    @app.post('/api/session/wechat')
    def wechat_login():
        incoming = data()
        if set(incoming) != {'code'}:
            raise ValueError('请在微信小程序中重新发起登录。')
        limited()
        identity, config = service.exchange(incoming['code'])
        browser = service.digest(session['csrf'])
        user, ticket = service.owner_or_ticket(identity, config, browser)
        if user:
            establish(user)
            return jsonify(bind_required=False, **payload())
        return jsonify(bind_required=True, ticket=ticket, expires_in=600)

    @app.post('/api/session/wechat/bind')
    def wechat_bind_login():
        incoming = data()
        if set(incoming) != {'ticket', 'username', 'password'}:
            raise ValueError('请验证要绑定的本站账号。')
        limited()
        user = verified(incoming['username'], incoming['password'])
        current = accounts.get(session.get('user_id', 0))
        if current and current['id'] != user['id']:
            raise ValueError('请先退出当前账号，再绑定其他账号。')
        user = service.bind_ticket(incoming['ticket'], service.digest(session['csrf']), user)
        establish(user)
        return jsonify(message='微信已绑定，下次可直接使用微信登录。', **payload())

    @app.get('/api/wechat/binding')
    @auth
    def wechat_binding_state():
        return jsonify(service.status(g.user['id']))

    @app.post('/api/wechat/binding')
    @auth
    def wechat_binding_save():
        incoming = data()
        if set(incoming) != {'code', 'password'}:
            raise ValueError('请使用本人微信并验证本站密码。')
        limited()
        user = verified(g.user['username'], incoming['password'])
        identity, config = service.exchange(incoming['code'])
        service.bind(identity, config, user)
        return jsonify(message='微信登录已绑定。', **service.status(user['id']))

    @app.post('/api/wechat/unbind')
    @auth
    def wechat_binding_remove():
        incoming = data()
        if set(incoming) != {'password'}:
            raise ValueError('请验证本站密码后解绑。')
        user = verified(g.user['username'], incoming['password'])
        establish(service.unbind(user))
        return jsonify(message='已解除微信登录，其他设备需重新登录。', **payload())
