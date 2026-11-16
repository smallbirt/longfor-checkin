"""PC administration APIs. Native mini-program UI is deliberately unchanged."""
import secrets
import json
from flask import g, jsonify, render_template, request
from .entitlements import Wallet, DEFAULT_RULES, integer, reason, request_key
from .program_accounts import ProgramAccounts
from .store import now


def install_routes(app, root, accounts, spaces, auth, admin, data, rate):
    def rules():
        return root.get('entitlement_rules', DEFAULT_RULES)

    def target(uid):
        value = accounts.get(uid)
        if not value or uid == 1:
            raise ValueError('只能管理存在的普通用户。')
        store, runner = spaces.get(uid)
        return value, store, runner

    def masked(address):
        if not isinstance(address, str) or '@' not in address:
            return ''
        local, domain = address.rsplit('@', 1)
        return local[:2] + '***@' + domain

    def summary(uid):
        u, store, _ = target(uid)
        pref = spaces.preferences(uid)
        w = Wallet(store).public(rules()['price'])
        prefs = store.get('notification_events', {})
        items = ProgramAccounts(store).public()
        states = {'auth_expired': 'expired', 'request_error': 'failed', 'unknown': 'failed',
                  'captcha_required': 'failed', 'credentials_missing': 'idle', 'unsigned': 'pending', 'signed': 'signed'}
        rows = []
        for a in items:
            status = a.get('status') or {}
            state = states.get(status.get('state'), 'pending') if status.get('day') == now().date().isoformat() else 'pending'
            rows.append({'id': a['id'], 'name': a['alias'], 'channel': a['program_name'], 'auto': a['enabled'],
                         'state': state if a['configured'] else 'idle', 'signTime': a['sign_time'], 'checkTime': a['check_time']})
        note = root.cipher.decrypt(u['note'].encode()).decode() if u['note'] else ''
        return {'id': uid, 'username': u['username'], 'name': u['nickname'] or u['username'], 'email': masked(pref.get('recipient')),
                'status': u['status'], 'created': u['created'], 'last': u['last_active'], 'note': note,
                'points': w['points'], 'free': w['free'], 'purchased': w['purchased'], 'accounts': rows,
                'mail': bool(pref.get('verified')), 'push': spaces.push.ready(store),
                'success': prefs.get('success', True), 'failure': prefs.get('failure', True)}

    def ledger(uid):
        _, store, _ = target(uid)
        return [{'id': r['id'], 'userId': uid, 'type': r['kind'], 'delta': r['delta'], 'balance': r['balance'],
                 'time': r['created'], 'actor': r['actor'], 'reason': r['reason'], 'related': '—',
                 'free': r['free_slots'], 'purchased': r['purchased_slots']} for r in Wallet(store).ledger()]

    @app.get('/admin/users')
    @admin
    def admin_users_page():
        return render_template('admin_users.html')

    @app.get('/api/admin/users')
    @admin
    def admin_users():
        users, entries, errors = [], [], []
        for uid in accounts.ids():
            if uid == 1:
                continue
            try:
                users.append(summary(uid))
                entries.extend(ledger(uid))
            except Exception:
                # An unreadable tenant must not expose raw exceptions or hide other users.
                u = accounts.get(uid)
                errors.append({'id': uid, 'username': u['username'], 'error': '用户空间暂时无法读取，请检查数据或密钥。'})
        with root.connect() as db:
            audit = [{'id': r['id'], 'userId': r['user_id'], 'type': r['kind'], 'actor': r['actor'],
                      'reason': r['reason'], 'time': r['created']}
                     for r in db.execute('SELECT * FROM admin_events ORDER BY created DESC,id DESC LIMIT 300').fetchall()]
        for r in entries:
            audit.append({'id': r['id'], 'userId': r['userId'], 'type': {'grant': '发放积分', 'deduct': '扣减积分', 'purchase': '购买名额', 'quota': '调整免费名额'}[r['type']],
                          'actor': r['actor'], 'reason': r['reason'], 'time': r['time']})
        return jsonify(users=users, ledger=sorted([r for r in entries if r['type'] != 'quota'], key=lambda r: r['time'], reverse=True)[:500],
                       audit=sorted(audit, key=lambda r: r['time'], reverse=True)[:500], rules=rules(),
                       registration=root.get('registration_enabled', True), errors=errors)

    @app.post('/api/admin/users')
    @admin
    def admin_user_create():
        rate('admin-create-user', 20, 3600)
        incoming = data()
        name = incoming.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24:
            raise ValueError('昵称需为 1–24 字。')
        u = accounts.register(incoming.get('username', ''), secrets.token_urlsafe(32), nickname=name.strip(),
                              audit=('新增用户', g.user['username'], '新用户初始积分 0，按当前默认规则分配免费名额。'))
        link = accounts.issue_reset(u['id'])
        return jsonify(message='用户已创建，请将一次性激活链接交给用户。', reset_url=link), 201

    @app.post('/api/admin/users/<int:uid>/profile')
    @admin
    def admin_user_profile(uid):
        target(uid)
        incoming = data()
        note = incoming.get('note')
        if not isinstance(note, str) or len(note) > 300:
            raise ValueError('备注不能超过 300 字。')
        token = root.cipher.encrypt(note.strip().encode()).decode() if note.strip() else ''
        with root.connect() as db:
            db.execute('UPDATE user_meta SET note=? WHERE user_id=?', (token, uid))
            accounts.record_event(db, uid, '更新管理员备注', g.user['username'], '已更新备注。' if token else '已清空备注。')
        return jsonify(message='备注已保存。')

    @app.post('/api/admin/users/<int:uid>/access')
    @admin
    def admin_user_access(uid):
        _, _, runner = target(uid)
        incoming = data()
        kind, why = incoming.get('action'), reason(incoming.get('reason'))
        if kind not in ('disable', 'restore', 'revoke', 'reset'):
            raise ValueError('用户管理操作无效。')
        if not runner.lock.acquire(blocking=False):
            return jsonify(error='该用户任务正在执行，请完成后再修改账号状态。'), 409
        try:
            with runner.mail_lock:
                audit = ({'disable': '停用用户', 'restore': '恢复用户', 'revoke': '退出所有设备', 'reset': '重置本站登录'}[kind], g.user['username'], why)
                if kind == 'reset':
                    link = accounts.issue_reset(uid, audit=audit)
                else:
                    accounts.revoke(uid, {'disable': 'disabled', 'restore': 'active'}.get(kind), audit=audit)
        finally:
            runner.lock.release()
        return jsonify(message='操作已完成。', **({'reset_url': link} if kind == 'reset' else {}))

    @app.post('/api/admin/users/credits')
    @admin
    def admin_user_credits():
        incoming = data()
        ids, kind = incoming.get('user_ids'), incoming.get('kind')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(i) is not int or i <= 1 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('请选择有效的普通用户。')
        if kind not in ('grant', 'deduct', 'quota') or kind != 'grant' and len(ids) != 1:
            raise ValueError('只有发放积分支持批量操作。')
        why, key = reason(incoming.get('reason')), request_key(incoming.get('request_id'))
        value = integer(incoming.get('value'), 0 if kind == 'quota' else 1, 30 if kind == 'quota' else 100000, '调整数量')
        # Validate identities before doing any per-tenant work. Each wallet is atomic.
        for uid in ids:
            target(uid)
        completed, errors = [], []
        for uid in ids:
            try:
                _, store, _ = target(uid)
                Wallet(store).change(kind, value, why, g.user['username'], key)
                completed.append(uid)
            except ValueError as exc:
                errors.append({'id': uid, 'error': str(exc)})
            except Exception:
                errors.append({'id': uid, 'error': '用户空间暂时无法写入，请使用相同操作标识重试。'})
        return jsonify(message='已完成 %s 位用户的调整。' % len(completed), completed=completed, errors=errors), 200 if completed else 400

    @app.post('/api/admin/entitlement-rules')
    @admin
    def admin_rules():
        incoming = data()
        value = {'free': integer(incoming.get('free'), 0, 30, '免费名额'),
                 'price': integer(incoming.get('price'), 1, 100000, '扩容价格')}
        with root.lock, root.connect() as db:
            db.serialize('accounts')
            row = db.execute("SELECT value FROM settings WHERE key='entitlement_rules'").fetchone()
            old = json.loads(root.cipher.decrypt(row['value'].encode())) if row else DEFAULT_RULES
            token = root.cipher.encrypt(json.dumps(value).encode()).decode()
            db.execute("INSERT INTO settings(key,value) VALUES('entitlement_rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (token,))
            accounts.record_event(db, 0, '修改权益规则', g.user['username'], '新用户免费 %s → %s 个，扩容价格 %s → %s 积分。' % (old['free'], value['free'], old['price'], value['price']))
        return jsonify(message='权益规则已保存，已有用户名额不变。')

    @app.get('/credits')
    @auth
    def credits_page():
        return render_template('credits.html', page='credits')

    @app.get('/api/credits')
    @auth
    def credits_state():
        return jsonify(wallet=Wallet(g.workspace_store).public(rules()['price']), ledger=Wallet(g.workspace_store).ledger())

    @app.post('/api/credits/purchase')
    @auth
    def credits_purchase():
        if g.user['id'] == 1:
            raise ValueError('管理员无需购买自动打卡名额。')
        incoming = data()
        price = integer(incoming.get('price'), 1, 100000, '扩容价格')
        key = request_key(incoming.get('request_id'))
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='任务正在执行，请完成后再扩容。'), 409
        try:
            if (accounts.get(g.user['id']) or {}).get('status') != 'active':
                return jsonify(error='账号已停用。'), 403
            with g.workspace_store.connect() as db:
                previous = db.execute('SELECT kind FROM credit_ledger WHERE request_id=?', (key,)).fetchone()
            if not previous and rules()['price'] != price:
                return jsonify(error='扩容价格已更新，请刷新后重新确认。'), 409
            row = Wallet(g.workspace_store).change('purchase', price, '购买 1 个自动打卡名额', g.user['username'], key)
        finally:
            g.runner.lock.release()
        return jsonify(message='名额已增加，积分流水已记录。', receipt=row['id'])

    @app.get('/reset-password')
    def reset_password_page():
        return render_template('reset_password.html')

    @app.post('/api/reset-password')
    def reset_password_submit():
        rate('reset-password:' + (request.remote_addr or ''), 10, 300)
        incoming = data()
        if incoming.get('password') != incoming.get('confirm_password'):
            raise ValueError('两次密码输入不一致。')
        accounts.reset_password(incoming.get('token'), incoming.get('password'))
        return jsonify(message='密码已设置，请使用新密码登录。')
