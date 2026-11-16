import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from flask import Flask, request, session, jsonify, render_template, redirect, url_for, g
from werkzeug.exceptions import HTTPException
from .constants import CHANNELS, PROVIDERS
from .store import Store, now
from .accounts import Accounts, Workspaces
from .importer import parse_text, parse_capture
from .import_diagnostics import validation_failure_hint
from .login_client import LoginClient, LoginError, phone_number, mask_phone, checkin_credential
from .client import LongforClient
from .program_accounts import ProgramAccounts, filtered_jobs
from .device import install_device_routes
from . import shared_device
from . import mini_login
from .mailer import ADDRESS, validate_mail, send_mail
from .mini_push import install_routes as install_mini_push_routes
from .user_management import install_routes as install_user_management_routes


def create_app(data_dir=None, password=None, worker=False, client=None, login_client=None, device_client=None, mini_login_client=None):
    app = Flask(__name__)
    from .proxy import TrustedProxy
    app.wsgi_app = TrustedProxy(app.wsgi_app, os.environ.get('TRUSTED_PROXY_IPS', ''))
    password = password or os.environ.get('ADMIN_PASSWORD')
    if not password or len(password) < 16:
        raise RuntimeError('ADMIN_PASSWORD 至少 16 位，请先运行 manage.py setup。')
    root = Store(data_dir or os.environ.get('DATA_DIR', 'data'))
    accounts = Accounts(root, password)
    shared_device.sync(root)
    spaces = Workspaces(root, accounts, client)
    sms_client = login_client or LoginClient()
    app.secret_key = hashlib.sha256(root.key + password.encode()).digest()
    app.config.update(MAX_CONTENT_LENGTH=50 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
        MINI_SMS_EXPERIMENTAL=os.environ.get('MINI_SMS_EXPERIMENTAL') == 'true',
        SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == 'true',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    app.extensions.update(store=root, accounts=accounts, workspaces=spaces, runner=spaces.get(1)[1])

    @app.before_request
    def csrf_guard():
        if request.endpoint in ('device_frame', 'device_collect', 'static'):
            return
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method == 'POST':
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not hmac.compare_digest(str(token), session['csrf']):
                return jsonify(error='页面已过期，请刷新后重试。'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN' if request.endpoint == 'device_frame' else 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.endpoint != 'device_frame':
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.endpoint == 'device_collect':
            response.headers['Access-Control-Allow-Origin'] = 'null'
            response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Param, Content-Type'
            response.headers['Vary'] = 'Origin'
        return response

    def auth(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = accounts.get(session.get('user_id', 0))
            if not user or user['status'] != 'active' or session.get('version') != user['version']:
                if request.path.startswith('/api/'):
                    return jsonify(error='请先登录。'), 401
                return redirect(url_for('login'))
            g.user = user
            if not user['last_active'] or user['last_active'] < (now() - timedelta(minutes=5)).isoformat():
                accounts.touch(user['id'])
            g.store, g.runner = spaces.get(user['id'])
            g.workspace_store = g.store
            g.selected_account = None
            scoped_endpoints = {'state', 'credentials', 'import_credentials', 'sms_send', 'sms_verify', 'mini_sms_send', 'mini_sms_verify'}
            account_id = request.args.get('account_id')
            if account_id and request.endpoint in scoped_endpoints:
                registry = ProgramAccounts(g.workspace_store)
                g.selected_account = registry.require(account_id)
                g.store = registry.scoped(account_id)
                if request.endpoint in ('sms_send', 'sms_verify') and g.selected_account['channel'] != 'app':
                    raise ValueError('小程序请导入对应端登录文件，不能使用 App 短信更新。')
                if request.endpoint in ('mini_sms_send', 'mini_sms_verify') and g.selected_account['channel'] != 'mini':
                    raise ValueError('登录渠道与所选账号不一致。')
            return f(*args, **kwargs)
        return wrapped

    def admin(f):
        @wraps(f)
        @auth
        def wrapped(*args, **kwargs):
            if g.user['id'] != 1:
                return jsonify(error='仅管理员可操作。'), 403
            return f(*args, **kwargs)
        return wrapped

    def establish(user):
        session.clear()
        session.update(user_id=user['id'], version=user['version'], csrf=secrets.token_urlsafe(32))
        session.permanent = True

    def data():
        incoming = request.get_json(silent=True)
        if not isinstance(incoming, dict):
            raise ValueError('请求内容需为 JSON 对象。')
        return incoming

    def rate(key, limit, seconds):
        if not accounts.allow(key, limit, seconds):
            raise ValueError('操作较频繁，请稍后再试。')

    install_device_routes(app, root, accounts, spaces, auth, rate, device_client)
    install_mini_push_routes(app, root, spaces, auth, admin, data, rate)
    install_user_management_routes(app, root, accounts, spaces, auth, admin, data, rate)

    # Native mini-program requests keep their own cookie jar. Reuse the exact
    # Web session, CSRF, account version and rate limits rather than a second
    # identity system or a client-supplied user id.
    def session_payload():
        user = accounts.get(session.get('user_id', 0))
        valid = user and user['status'] == 'active' and user['version'] == session.get('version')
        return dict(csrf=session['csrf'], registration_enabled=root.get('registration_enabled', True),
                    user={'username': user['username'], 'is_admin': user['id'] == 1} if valid else None,
                    wechat_login_enabled=bool(app.extensions['wechat_auth'].config()),
                    preview=bool(app.config.get('WEB_PREVIEW')), local_debug=bool(app.config.get('LOCAL_DEBUG')))

    from .wechat_auth import install_routes as install_wechat_auth_routes
    install_wechat_auth_routes(app, root, accounts, auth, admin, data, rate, establish, session_payload)

    @app.get('/api/session')
    def native_session():
        return jsonify(**session_payload())

    @app.post('/api/session/login')
    def native_login():
        incoming = data()
        if not accounts.allow('login-ip:' + (request.remote_addr or ''), 30, 300):
            return jsonify(error='尝试过多，请 5 分钟后重试。'), 429
        user = accounts.authenticate(incoming.get('username', ''), incoming.get('password', ''))
        if not user:
            return jsonify(error='用户名或密码不正确。'), 401
        establish(user)
        return jsonify(**session_payload())

    @app.post('/api/session/register')
    def native_register():
        if not root.get('registration_enabled', True):
            return jsonify(error='注册暂未开放，请联系管理员。'), 403
        incoming = data()
        rate('register-ip:' + (request.remote_addr or ''), 5, 3600)
        if incoming.get('password') != incoming.get('confirm_password'):
            raise ValueError('两次输入的密码不一致。')
        user = accounts.register(incoming.get('username', ''), incoming.get('password', ''))
        establish(user)
        return jsonify(**session_payload()), 201

    @app.post('/api/session/logout')
    @auth
    def native_logout():
        session.clear()
        session['csrf'] = secrets.token_urlsafe(32)
        return jsonify(**session_payload())

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            if not accounts.allow('login-ip:' + (request.remote_addr or ''), 30, 300):
                return render_template('login.html', error='尝试过多，请 5 分钟后重试。'), 429
            user = accounts.authenticate(request.form.get('username', 'admin'), request.form.get('password', ''))
            if user:
                establish(user)
                return redirect(url_for('index'))
            error = '用户名或密码不正确。'
        return render_template('login.html', error=error, registration=root.get('registration_enabled', True))

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        error = None
        if not root.get('registration_enabled', True):
            return render_template('login.html', error='注册暂未开放，请联系管理员。', registration=False), 403
        if request.method == 'POST':
            try:
                rate('register-ip:' + (request.remote_addr or ''), 5, 3600)
                if len(accounts.ids()) >= 100:
                    raise ValueError('当前注册名额已满，请联系管理员。')
                if request.form.get('password') != request.form.get('confirm_password'):
                    raise ValueError('两次输入的密码不一致。')
                user = accounts.register(request.form.get('username', ''), request.form.get('password', ''))
                establish(user)
                return redirect(url_for('index'))
            except ValueError as exc:
                error = str(exc)
        return render_template('register.html', error=error)

    @app.post('/logout')
    @auth
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/health')
    def health():
        healthy = not worker or bool(spaces.thread and spaces.thread.is_alive())
        healthy = healthy and root.ping()
        return jsonify(status='ok' if healthy else 'unhealthy', storage='postgresql' if root.is_postgres else 'sqlite'), 200 if healthy else 503

    @app.get('/')
    @auth
    def index():
        return render_template('index.html', page='home')

    @app.get('/credentials')
    @auth
    def credentials():
        return render_template('credentials.html', page='credentials', selected_account=g.selected_account)

    @app.get('/accounts')
    @auth
    def program_accounts():
        return render_template('accounts.html', page='accounts')

    @app.get('/accounts/new')
    @auth
    def program_account_new():
        return render_template('account_new.html', page='account-new')

    @app.get('/accounts/<account_id>')
    @auth
    def program_account_detail(account_id):
        selected = ProgramAccounts(g.store).require(account_id)
        return render_template('account.html', page='account', account=selected)

    @app.post('/api/accounts')
    @auth
    def program_account_create():
        rate('new-account:' + str(g.user['id']), 15, 300)
        incoming = data()
        item = ProgramAccounts(g.store).create(incoming.get('channel'), incoming.get('alias'))
        return jsonify(account=item, message='账号已创建，请导入此账号的登录文件。'), 201

    @app.post('/api/accounts/<account_id>')
    @auth
    def program_account_update(account_id):
        incoming = data()
        registry = ProgramAccounts(g.store)
        a = registry.require(account_id)
        if incoming.get('enabled') is True:
            if not registry.scoped(account_id).get('credential:' + a['channel']):
                raise ValueError('请先导入并验证此账号的登录文件，再开启自动签到。')
            if not spaces.notification_ready(g.user['id']):
                raise ValueError('请先在小程序订阅复核通知，或验证接收邮箱并确认平台已配置发信服务。')
        updated = registry.update(account_id, incoming)
        if updated['enabled']:
            g.store.put('schedule_enabled', True)
        return jsonify(account=updated, message='此账号的配置已保存，其他账号不受影响。')

    @app.post('/api/accounts/<account_id>/remove')
    @auth
    def program_account_remove(account_id):
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='任务正在运行，请完成后再移除账号。'), 409
        try:
            ProgramAccounts(g.store).archive(account_id)
        finally:
            g.runner.lock.release()
        return jsonify(message='账号及登录凭证已移除，历史记录保留。')

    @app.get('/settings')
    @auth
    def settings():
        return render_template('settings.html', page='settings')

    @app.get('/history')
    @auth
    def history():
        return render_template('history.html', page='history')

    @app.get('/help')
    def user_help():
        user = accounts.get(session.get('user_id', 0))
        if user and session.get('version') == user['version']:
            g.user = user
        return render_template('help.html', page='help')

    @app.get('/video')
    def operation_video():
        user = accounts.get(session.get('user_id', 0))
        if user and session.get('version') == user['version']:
            g.user = user
        return render_template('video.html', page='video')

    @app.get('/admin')
    @admin
    def administration():
        return render_template('admin.html', page='admin')

    @app.get('/api/state')
    @auth
    def state():
        store, runner = g.store, g.runner
        effective = shared_device.resolve(root, store, list(CHANNELS))
        channels = []
        for key, c in CHANNELS.items():
            cred = store.get('credential:' + key) or {}
            channels.append({'id': key, 'name': c['name'], 'configured': bool(cred),
                'first_import_done': bool(cred),
                'imported_at': cred.get('imported_at'), 'blocked': cred.get('blocked'),
                'has_fingerprint': bool(cred.get('headers', {}).get('x-lf-dxrisk-token')),
                'device_ready': bool(effective['devices'].get(key)),
                'effective_device_source': effective['device_sources'].get(key,'missing'),
                'device_source': 'platform' if cred.get('device_kind') == 'shared_default' else 'personal' if cred.get('headers', {}).get('x-lf-dxrisk-token') else 'missing',
                'status': store.get('status:' + key)})
        pending = store.get('pending_sms')
        if pending and pending.get('expires', 0) < time.time():
            pending = None
            if runner.lock.acquire(blocking=False):
                try:
                    current = store.get('pending_sms')
                    if current and current.get('expires', 0) < time.time():
                        store.put('pending_sms', None)
                finally:
                    runner.lock.release()
        profile = store.get('login-profile:app') or {}
        automatic = store.get('automatic_device') or {}
        automatic_ready = bool(automatic.get('fingerprint')) and automatic.get('expires', 0) > time.time()
        shared = shared_device.summary(root)
        pref = spaces.preferences(g.user['id'])
        mail = root.get('mail', {})
        return jsonify(user={'username': g.user['username'], 'is_admin': g.user['id'] == 1}, local_debug=bool(app.config.get('LOCAL_DEBUG')),
            channels=channels, mail={'recipient': pref.get('recipient', ''), 'verified': bool(pref.get('verified'))},
            mail_configured=bool(spaces.mail_config(g.user['id'])), sender_configured=bool(mail),
            schedule_enabled=store.get('schedule_enabled', False),
            accounts=ProgramAccounts(g.workspace_store).public(),
            selected_account=g.selected_account,
            notification_events={key: g.workspace_store.get('notification_events', {}).get(key, True)
                                 for key in ('success', 'failure')},
            mini_push=spaces.push.public(g.workspace_store),
            notification_configured=spaces.notification_ready(g.user['id']),
            jobs=filtered_jobs(g.workspace_store, limit=5, account_id=g.selected_account['id'] if g.selected_account else None),
            today=now().date().isoformat(), running=runner.lock.locked(), heartbeat=store.get('heartbeat'),
            worker_error=store.get('worker_error'),
            login_methods={'app': {'sms_available': bool(profile), 'phone_hint': profile.get('phone_hint'),
                'pending': bool(pending), 'pending_phone': pending.get('phone_hint') if pending else None,
                'pending_target': pending.get('target', 'app') if pending else None,
                'pending_device_mode': pending.get('device_mode', 'imported') if pending else None,
                'retry_after': max(0, int(max(store.get('sms_retry_at', 0), store.get('mini_sms_retry_at', 0)) - time.time()) + 1),
                'automatic_available': False, 'automatic_ready': automatic_ready,
                'shared_available': shared['enabled'], 'shared_channels': shared['channels'],
                'effective_sms_available': bool(effective['profile']) and bool(store.get('credential:app')),
                'first_import_required': not bool(store.get('credential:app')),
                'profile_source': effective['profile_source'], 'device_sources': effective['device_sources'],
                'automatic_expires': automatic.get('expires') if automatic_ready else None},
                'mini': mini_login.state(app, root, store)})

    @app.get('/api/jobs')
    @auth
    def jobs():
        try:
            before = int(request.args['before']) if 'before' in request.args else None
            if before is not None and before <= 0:
                raise ValueError()
        except ValueError:
            raise ValueError('分页参数无效。') from None
        account_id = request.args.get('account_id') or None
        if account_id and not ProgramAccounts(g.store).get(account_id, archived=True):
            raise ValueError('签到账号不存在或不属于当前用户。')
        channel = request.args.get('channel') or None
        if channel and channel not in CHANNELS:
            raise ValueError('程序筛选无效。')
        day = request.args.get('day') or None
        if day:
            from datetime import date
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError()
            except ValueError:
                raise ValueError('日期筛选无效。') from None
        result_state = request.args.get('state') or None
        if result_state and result_state not in {'signed', 'unsigned', 'unknown', 'auth_expired', 'captcha_required', 'credentials_missing', 'inactive', 'request_error'}:
            raise ValueError('结果筛选无效。')
        rows = filtered_jobs(g.store, limit=21, before=before, account_id=account_id, channel=channel, day=day, state=result_state)
        return jsonify(jobs=rows[:20], next_cursor=rows[19]['id'] if len(rows) > 20 else None)

    @app.post('/api/import')
    @auth
    def import_credentials():
        rate('import:' + str(g.user['id']), 10, 300)
        if 'file' in request.files:
            selected = request.form.get('channel')
            if selected and selected not in CHANNELS:
                raise ValueError('请选择要更新的渠道。')
            bundle = parse_capture(request.files['file'].read())
            if selected:
                if selected not in bundle:
                    found='、'.join(CHANNELS[key]['name'] for key in bundle)
                    raise ValueError('文件识别到'+found+'，当前选择的是'+CHANNELS[selected]['name']+'。请切换到对应渠道入口，或选择正确文件。')
                bundle = {selected: bundle[selected]}
        else:
            incoming_data = data()
            selected = incoming_data.get('channel')
            if selected not in CHANNELS:
                raise ValueError('请选择要更新的渠道。')
            if not isinstance(incoming_data.get('text'), str):
                raise ValueError('请粘贴完整请求。')
            bundle = {k: {'credential': v, 'login': False} for k, v in parse_text(incoming_data['text'], selected).items()}
        store, runner = g.store, g.runner
        if g.selected_account:
            expected = g.selected_account['channel']
            if selected and selected != expected:
                raise ValueError('登录文件与所选账号的程序不一致。')
            if expected not in bundle:
                raise ValueError('文件中没有所选程序的登录信息，请选择对应端的 HAR。')
            bundle = {expected: bundle[expected]}
        else:
            archived = {a['id'] for a in ProgramAccounts(g.workspace_store).list(archived=True) if a.get('archived')}
            if any(key in archived for key in bundle):
                raise ValueError('原有主账号已移除，请从“我的签到”添加新账号后导入，不会恢复已移除的凭证。')
        if not runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后再导入。'), 409
        try:
            prepared = {}
            previous_configured = {key:bool(store.get('credential:'+key)) for key in bundle}
            for key, item in bundle.items():
                cred = item['credential']
                if item['login']:
                    cred = checkin_credential(key, cred['headers']['token'], item.get('device') or store.get('credential:' + key))
                if not cred['headers'].get('x-lf-dxrisk-token'):
                    selection = shared_device.resolve(root, store, [key])
                    device = selection['devices'].get(key)
                    if device:
                        cred['headers'].update({'x-lf-dxrisk-token': device['risk_token'], 'x-lf-dxrisk-source': device['source']})
                        if device['user_agent']:
                            cred['headers']['user-agent'] = device['user_agent']
                        if selection['device_sources'][key] in ('platform', 'saved'):
                            cred['device_kind'] = 'shared_default'
                if item['login'] or g.selected_account:
                    result = validate_login(cred)
                else:
                    result = {'day': now().date().isoformat(), 'state': 'unknown',
                        'message': '新凭证已保存，正在查询登录状态。', 'checked_at': now().isoformat()}
                prepared[key] = (cred, result)
            # Validate every login candidate first, so a failed login import keeps old credentials.
            for key, (cred, result) in prepared.items():
                cred['imported_at'] = now().isoformat()
                store.put('credential:' + key, cred)
                store.put('status:' + key, result)
                if bundle[key].get('profile'):
                    store.put('login-profile:' + key, bundle[key]['profile'])
            # A mini import must not consume an unrelated App SMS challenge.
            if 'app' in bundle or (store.get('pending_sms') or {}).get('target', 'app') != 'app':
                store.put('pending_sms', None)
            if 'mini' in bundle:
                store.put('pending_mini_sms', None)
            if g.user['id'] == 1 and (not g.selected_account or g.selected_account['id'] in CHANNELS):
                shared_device.sync(root)
        finally:
            runner.lock.release()
        if not g.selected_account and any(not x['login'] for x in bundle.values()):
            runner.submit('check', target=next(iter(bundle)) if len(bundle) == 1 else None)
        missing = [CHANNELS[k]['name'] for k, (cred, _) in prepared.items() if not cred['headers'].get('x-lf-dxrisk-token')]
        message = '；'.join(CHANNELS[key]['name']+('登录已更新' if previous_configured[key] else '已连接') for key in prepared)+'。登录与设备信息已自动合并保存。'
        if g.user['id'] == 1 and (not g.selected_account or g.selected_account['id'] in CHANNELS):
            message += '管理员设备信息已自动更新到平台配置。'
        if 'app' in prepared and not shared_device.resolve(root, store, ['app'])['profile']:
            message += '当前 HAR 不含 App 短信登录设备，之后可继续用 HAR 更新；若要使用短信更新，请在同一份 HAR 中包含 App 成功登录响应。'
        if missing:
            message += '、'.join(missing) + '尚缺签到设备，原配置和平台也没有可用设备。请在该渠道的同一份 HAR 中包含正常签到请求后更新，无需单独导入设备凭证。'
        return jsonify(message=message,updated_channels=[{'id':key,'action':'updated' if previous_configured[key] else 'created',
            'device_ready':bool(cred['headers'].get('x-lf-dxrisk-token'))} for key,(cred,_) in prepared.items()])

    def validate_login(cred):
        # Do not let a runner's old HTTP cookie jar make a new token appear valid.
        verifier = client if client is not None else LongforClient()
        try:
            result = verifier.run(cred, now().date().isoformat(), allow_sign=False)
        finally:
            if client is None:
                verifier.transport.close()
        if not isinstance(result, dict) or result.get('state') not in ('signed', 'unsigned'):
            raise ValueError(validation_failure_hint(cred,result))
        return {**result, 'day': now().date().isoformat()}

    @app.post('/api/login/app/send')
    @app.post('/api/login/sms/send')
    @auth
    def sms_send():
        incoming = data()
        phone = phone_number(incoming.get('phone'))
        target = incoming.get('target', 'app')
        if target != 'app':
            raise ValueError('App 短信登录只更新 App。小程序请单独导入原生登录或签到页凭证。')
        if not g.store.get('credential:app'):
            raise ValueError('首次使用请先导入本人 App 登录成功的 HAR（包含响应），无需打开签到页。导入后才能用短信更新 App。')
        mode = incoming.get('device_mode', 'preferred' if request.path == '/api/login/sms/send' else 'imported')
        if mode not in ('imported', 'automatic', 'shared', 'preferred'):
            raise ValueError('请选择有效的设备配置方式。')
        shared = shared_device.active(root) if mode == 'shared' else None
        if mode == 'shared' and not shared:
            raise ValueError('管理员尚未启用平台默认设备，请选择其他方式。')
        resolved = shared_device.resolve(root, g.store, ['app']) if mode == 'preferred' else None
        profile = resolved['profile'] if resolved else shared.get('profile') if shared else g.store.get('automatic_device' if mode == 'automatic' else 'login-profile:app')
        if mode == 'automatic' and profile and profile.get('expires', 0) <= time.time():
            profile = None
        if not profile:
            raise ValueError('缺少 App 短信登录设备信息。请管理员补充设备专用配置，或导入本人 App 成功登录记录；签到设备不能代替短信登录设备。')
        uid = str(g.user['id'])
        digest = hmac.new(root.key, phone.encode(), hashlib.sha256).hexdigest()
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后获取验证码。'), 409
        try:
            rate('sms-user:' + uid, 1, 60)
            rate('sms-user-hour:' + uid, 5, 3600)
            rate('sms-phone:' + digest, 5, 3600)
            rate('sms-ip:' + (request.remote_addr or ''), 15, 3600)
            g.store.put('pending_sms', None)
            g.store.put('sms_retry_at', time.time() + 60)
            challenge = {'phone': phone, 'phone_hint': mask_phone(phone), 'expires': time.time() + 600,
                         'retry_at': time.time() + 60, 'attempts': 0, 'profile': profile, 'target': target, 'device_mode': mode}
            if shared:
                challenge.update(shared_generation=shared['generation'], devices=shared['devices'])
            if resolved:
                challenge.update(resolved_devices=resolved['devices'], device_sources=resolved['device_sources'],
                    profile_source=resolved['profile_source'], shared_generation=resolved['shared_generation'])
            try:
                sms_client.send(phone, profile)
            except LoginError as exc:
                if not exc.uncertain:
                    raise
                g.store.put('pending_sms', challenge)
                return jsonify(message='短信发送结果未确认。如果手机已收到，可直接填写验证码；请勿连续重发。', uncertain=True)
            g.store.put('pending_sms', challenge)
            return jsonify(message='已请求发送短信到 ' + mask_phone(phone) + '，请填写验证码。')
        finally:
            g.runner.lock.release()

    @app.post('/api/login/app/verify')
    @app.post('/api/login/sms/verify')
    @auth
    def sms_verify():
        code = data().get('code')
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValueError('请填写 6 位短信验证码。')
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后验证。'), 409
        try:
            if not g.store.get('credential:app'):
                g.store.put('pending_sms', None)
                raise ValueError('首次使用请先导入本人 App 成功登录记录，再获取新的短信验证码。')
            pending = g.store.get('pending_sms')
            if not pending or pending.get('expires', 0) < time.time() or pending.get('attempts', 5) >= 5:
                g.store.put('pending_sms', None)
                raise ValueError('短信验证已过期或尝试次数已用完，请重新获取。')
            if pending.get('target', 'app') != 'app':
                g.store.put('pending_sms', None)
                raise ValueError('登录方式已分开，请重新获取 App 验证码；小程序需单独更新。')
            try:
                shared_device.ensure_current(root, pending)
            except ValueError:
                g.store.put('pending_sms', None)
                raise
            pending['attempts'] += 1
            g.store.put('pending_sms', pending)
            try:
                token = sms_client.verify(pending['phone'], code, pending['profile'])
            except LoginError as exc:
                if exc.uncertain or pending['attempts'] >= 5:
                    g.store.put('pending_sms', None)
                raise
            # OTP may have been consumed: never automatically retry its verification.
            g.store.put('pending_sms', None)
            target = pending.get('target', 'app')
            targets = ['app']
            prepared = {}
            for key in targets:
                previous = g.store.get('credential:' + key) or {}
                cred = checkin_credential(key, token, previous)
                if pending.get('device_mode') == 'preferred':
                    device = pending.get('resolved_devices', {}).get(key)
                    if device:
                        cred['headers'].update({'x-lf-dxrisk-token': device['risk_token'], 'x-lf-dxrisk-source': device['source']})
                        if device['user_agent']:
                            cred['headers']['user-agent'] = device['user_agent']
                        if pending['device_sources'][key] in ('platform', 'saved'):
                            cred['device_kind'] = 'shared_default'
                if pending.get('device_mode') == 'shared' and pending['devices'].get(key) and (not cred['headers'].get('x-lf-dxrisk-token') or previous.get('device_kind') == 'shared_default'):
                    device = pending['devices'][key]
                    cred['headers'].update({'x-lf-dxrisk-token': device['risk_token'],
                        'x-lf-dxrisk-source': device['source'], 'user-agent': device['user_agent']})
                    cred['device_kind'] = 'shared_default'
                if pending['profile'].get('kind') == 'browser_sdk' and not cred['headers'].get('x-lf-dxrisk-token'):
                    # This is a freshly issued WEB SDK token, not a fabricated
                    # native fingerprint. Preserve actual browser source metadata.
                    cred['headers']['x-lf-dxrisk-token'] = pending['profile']['risk_token']
                    cred['headers']['x-lf-dxrisk-source'] = '3'
                    cred['headers']['user-agent'] = pending['profile']['user_agent']
                    cred['device_kind'] = 'browser_sdk'
                prepared[key] = (cred, validate_login(cred))
            shared_device.ensure_current(root, pending)
            # App SMS tokens are only persisted as App credentials.
            for key, (cred, result) in prepared.items():
                cred['imported_at'] = now().isoformat()
                g.store.put('credential:' + key, cred)
                g.store.put('status:' + key, result)
            missing = any(not cred['headers'].get('x-lf-dxrisk-token') for cred, _ in prepared.values())
            return jsonify(message='App 短信更新成功，已查询确认并保存 App 凭证。小程序凭证保持原样。' +
                ('首次自动签到还需导入对应签到页设备信息。' if missing else '') +
                ('自动设备模式为试用，实际签到仍需确认；不兼容时可导入手机登录文件。' if pending['profile'].get('kind') == 'browser_sdk' else '') +
                ('使用平台默认设备，实际签到仍需确认；不兼容时请导入自己的设备配置。' if pending.get('device_mode') == 'shared' else ''))
        finally:
            g.runner.lock.release()

    @app.get('/api/admin')
    @admin
    def admin_state():
        mail = root.get('mail', {})
        return jsonify(mail={k: mail.get(k, '') for k in ('provider', 'sender')}, configured=bool(mail),
                       registration_enabled=root.get('registration_enabled', True), user_count=len(accounts.ids()),
                       shared_device=shared_device.summary(root), mini_push=spaces.push.platform_summary())

    @app.post('/api/admin/shared-device')
    @admin
    def shared_device_save():
        enabled = data().get('enabled')
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='管理员的任务正在运行，请稍后更新默认设备。'), 409
        try:
            shared_device.configure(root, enabled)
        finally:
            g.runner.lock.release()
        return jsonify(message='已从管理员当前设备配置启用 / 更新平台默认设备。用户仍需用自己的手机号和短信验证码登录。' if enabled else '已关闭平台默认设备；已保存的用户登录凭证保留。')

    @app.post('/api/admin/shared-device/import')
    @admin
    def shared_device_import():
        if 'file' not in request.files:
            raise ValueError('请选择设备专用配置文件。')
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='管理员任务正在运行，请稍后导入设备配置。'), 409
        try:
            shared_device.import_bundle(root, request.files['file'].read(65537))
        finally:
            g.runner.lock.release()
        return jsonify(message='设备专用配置已导入并启用。用户使用自己的手机号登录；管理员已导入的设备优先保留。')

    @app.post('/api/admin/registration')
    @admin
    def registration():
        enabled = data().get('enabled')
        if not isinstance(enabled, bool):
            raise ValueError('注册状态无效。')
        with root.lock, root.connect() as db:
            db.serialize('accounts')
            token = root.cipher.encrypt(b'true' if enabled else b'false').decode()
            db.execute("INSERT INTO settings(key,value) VALUES('registration_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (token,))
            accounts.record_event(db, 0, '开放注册' if enabled else '关闭注册', g.user['username'], '调整平台新用户注册入口。')
        return jsonify(message='已开放注册。' if enabled else '已关闭注册；已有用户仍可登录。')

    @app.post('/api/mail')
    @admin
    def mail_save():
        incoming = data()
        # Legacy API accepts the administrator's recipient, but ordinary users cannot call it.
        incoming.setdefault('recipient', root.get('mail', {}).get('recipient') or incoming.get('sender', ''))
        config = validate_mail(incoming, root.get('mail'))
        root.put('mail', config)
        return jsonify(message='平台发信配置已保存。用户的通知仍发送到各自验证过的邮箱。')

    @app.post('/api/recipient/code')
    @auth
    def recipient_code():
        recipient = str(data().get('recipient', '')).strip()
        if len(recipient) > 254 or not ADDRESS.fullmatch(recipient):
            raise ValueError('请填写有效的收件邮箱。')
        config = root.get('mail')
        if not config:
            raise ValueError('管理员尚未配置发信邮箱，请联系管理员。')
        uid = str(g.user['id'])
        rate('email-user:' + uid, 1, 60)
        rate('email-ip:' + (request.remote_addr or ''), 10, 3600)
        rate('email-address:' + hashlib.sha256(recipient.lower().encode()).hexdigest(), 5, 3600)
        code = '%06d' % secrets.randbelow(1000000)
        digest = hmac.new(root.key, (uid + ':' + recipient + ':' + code).encode(), hashlib.sha256).hexdigest()
        g.store.put('pending_email', {'recipient': recipient, 'digest': digest, 'expires': time.time() + 600, 'attempts': 0})
        ok, error = send_mail({**config, 'recipient': recipient}, '[每日签到] 验证收件邮箱',
                             '你的邮箱验证码是：' + code + '\n10 分钟内有效。如非本人操作，请忽略。')
        if not ok:
            g.store.put('pending_email', None)
            return jsonify(error=error if g.user['id'] == 1 else '验证码未发送成功，请稍后重试或联系管理员。'), 400
        return jsonify(message='验证码已发送，请检查收件箱和垃圾邮件。')

    @app.post('/api/recipient/verify')
    @auth
    def recipient_verify():
        incoming = data()
        with g.store.lock:
            pending = g.store.get('pending_email')
            if not pending or pending['expires'] < time.time() or pending['attempts'] >= 5:
                raise ValueError('验证码已失效，请重新获取。')
            pending['attempts'] += 1
            g.store.put('pending_email', pending)
            code = str(incoming.get('code', ''))
            recipient = str(incoming.get('recipient', '')).strip()
            digest = hmac.new(root.key, (str(g.user['id']) + ':' + recipient + ':' + code).encode(), hashlib.sha256).hexdigest()
            if recipient != pending['recipient'] or not hmac.compare_digest(digest, pending['digest']):
                raise ValueError('邮箱或验证码不正确，请检查。')
            g.store.put('notifications', {'recipient': recipient, 'verified': True})
            g.store.put('pending_email', None)
        return jsonify(message='收件邮箱已验证并保存，之后的签到通知会发送到这里。')

    @app.post('/api/mail/test')
    @auth
    def mail_test():
        rate('test-mail:' + str(g.user['id']), 1, 60)
        ok, error = send_mail(spaces.mail_config(g.user['id']), '[每日签到] 邮箱连接测试',
                             '邮箱通知测试。\n计划：每天北京时间 09:10 签到，16:00 复核与补签。\n这封邮件不代表签到已经执行。')
        if not ok and g.user['id'] != 1:
            error = '发送失败，请确认已验证邮箱；若仍失败请联系管理员检查发信配置。'
        return jsonify(message='测试邮件已提交，请检查收件箱和垃圾邮件。' if ok else error), 200 if ok else 400

    @app.post('/api/notifications')
    @auth
    def notification_preferences():
        incoming = data()
        # Accept the retired field from older clients, but never use or persist it.
        if set(incoming) not in ({'success', 'failure'}, {'success', 'failure', 'afternoon'}) or any(not isinstance(v, bool) for v in incoming.values()):
            raise ValueError('请选择有效的通知偏好。')
        g.store.put('notification_events', {key: incoming[key] for key in ('success', 'failure')})
        return jsonify(message='通知偏好已保存，适用于你的全部签到账号。')

    @app.post('/api/mail/retry')
    @auth
    def mail_retry():
        rate('retry-mail:' + str(g.user['id']), 1, 300)
        with g.runner.mail_lock, g.store.connect() as db:
            db.execute("UPDATE outbox SET state='pending', attempts=0, due=0 WHERE state='failed'")
        return jsonify(message='已重新排队你自己的失败通知，不会重新执行签到。')

    @app.post('/api/password')
    @auth
    def change_password():
        incoming = data()
        rate('password:' + str(g.user['id']), 5, 300)
        if not accounts.authenticate(g.user['username'], incoming.get('current_password', '')):
            raise ValueError('当前密码不正确。')
        accounts.change_password(g.user['id'], incoming.get('password', ''))
        session.clear()
        return jsonify(message='密码已修改，请重新登录。', login_required=True)

    @app.post('/api/schedule')
    @auth
    def schedule():
        enabled = data().get('enabled')
        if not isinstance(enabled, bool):
            raise ValueError('计划状态无效。')
        registry = ProgramAccounts(g.store)
        configured = {a['id'] for a in registry.list() if registry.scoped(a['id']).get('credential:' + a['channel'])}
        if enabled and (not spaces.notification_ready(g.user['id']) or not configured):
            raise ValueError('请先配置小程序订阅提醒或已验证的邮箱通知，并连接至少一个签到账号。')
        registry.set_schedule(enabled, configured)
        return jsonify(message='已开启；若今天计划时间已过，将补执行最近一轮任务。' if enabled else '已暂停后续定时任务；正在执行的任务会完成。')

    @app.post('/api/run')
    @auth
    def run():
        incoming = data()
        kind, target = incoming.get('kind', 'check'), incoming.get('channel')
        account_id = incoming.get('account_id')
        if account_id:
            selected = ProgramAccounts(g.store).require(account_id)
            if target and target != selected['channel']:
                raise ValueError('任务程序与所选账号不一致。')
            target = selected['id']
        if kind not in ('check', 'manual') or (not account_id and target is not None and target not in CHANNELS):
            raise ValueError('任务参数无效。')
        rate('run:' + str(g.user['id']), 6, 60)
        if not g.runner.submit(kind, target):
            return jsonify(error='你的任务正在运行，请稍后查看结果。'), 409
        return jsonify(message='任务已提交，页面会自动更新结果。'), 202

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(HTTPException)
    def http_error(exc):
        msg = '上传文件超过 50 MB。' if exc.code == 413 else '请求无效，请刷新页面重试。'
        return jsonify(error=msg), exc.code

    @app.errorhandler(Exception)
    def unexpected(exc):
        return jsonify(error='处理失败，请检查配置并重试。'), 500

    mini_login.install(app, root, auth, rate, data, validate_login, mini_login_client)
    if worker:
        spaces.start()
    return app
