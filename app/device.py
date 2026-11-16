"""Collect this browser's SDK payload through a fixed, bounded first-party relay."""
import re
import secrets
import time
import requests
from flask import request, jsonify, render_template, g, url_for
from itsdangerous import URLSafeTimedSerializer, BadData

DEVICE_APP_ID = '6341e15e0d8bdb6b3d20a51fb2b7c171'
DEVICE_URL = 'https://ly-sta.longhu.net/udid/c1'


def valid_device_token(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9+/=_-]{40}', value) is not None


class DeviceClient:
    def __init__(self, transport=None):
        self.transport = transport

    def collect(self, payload, user_agent, origin=None):
        if not isinstance(payload, str) or not 20 <= len(payload) <= 16384 or not payload.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in payload):
            raise ValueError('设备采集内容无效，请重新初始化。')
        if not isinstance(user_agent, str) or not 1 <= len(user_agent) <= 1024 or '\r' in user_agent or '\n' in user_agent:
            raise ValueError('当前浏览器信息无效，请更换浏览器重试。')
        transport = self.transport or requests.Session()
        if self.transport is None:
            transport.trust_env = False
        try:
            headers = {'Param': payload, 'Accept': 'application/json, text/plain, */*', 'User-Agent': user_agent}
            if origin:
                headers.update({'Origin': origin, 'Referer': origin + '/'})
            r = transport.get(DEVICE_URL, headers=headers, timeout=(10, 20), allow_redirects=False)
            if r.status_code != 200 or len(r.content) > 128 * 1024:
                raise ValueError()
            result = r.json()
            if isinstance(result, dict) and type(result.get('status')) is int and result['status'] < 0:
                code = result['status'] if -99 <= result['status'] <= -1 else '未知'
                raise DeviceRejected('设备服务未认可当前环境（设备码 %s）。未保存设备信息，请重试或使用备用导入。' % code)
            if not isinstance(result, dict) or type(result.get('status')) is not int or result.get('status') not in (1, 2) or not valid_device_token(result.get('data')):
                raise ValueError()
            # Never echo the upstream's arbitrary text, encrypted payload, or exceptions.
            return {'status': result['status'], 'data': result['data'], 'msg': 'success'}
        except DeviceRejected:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise ValueError('自动设备初始化未完成，请重试或使用手机登录文件导入。') from None
        finally:
            if self.transport is None:
                transport.close()


class DeviceRejected(ValueError):
    pass


def install_device_routes(app, root, accounts, spaces, auth, rate, device_client=None):
    collector = device_client or DeviceClient()
    tickets = URLSafeTimedSerializer(app.secret_key, salt='device-setup-v1')

    def load(ticket):
        try:
            payload = tickets.loads(ticket, max_age=300)
            user = accounts.get(payload['uid'])
            if not user or user['version'] != payload['version']:
                raise ValueError()
            store, _ = spaces.get(user['id'])
            setup = store.get('device_setup')
            if not setup or setup['nonce'] != payload['nonce'] or setup['expires'] < time.time():
                raise ValueError()
            return user, store, setup
        except (BadData, KeyError, TypeError, ValueError):
            raise ValueError('设备初始化已过期，请重新点击初始化。') from None

    @app.post('/api/device/start')
    @auth
    def device_start():
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后初始化设备。'), 409
        try:
            pending = g.store.get('pending_sms')
            if pending and pending.get('expires', 0) > time.time():
                raise ValueError('已有短信验证进行中，请先完成验证或等本轮过期。')
            rate('device-start-user:' + str(g.user['id']), 6, 600)
            rate('device-start-ip:' + (request.remote_addr or ''), 30, 3600)
            nonce = secrets.token_urlsafe(24)
            g.store.put('device_setup', {'nonce': nonce, 'expires': time.time() + 300, 'issued': []})
            ticket = tickets.dumps({'uid': g.user['id'], 'version': g.user['version'], 'nonce': nonce})
            return jsonify(ticket=ticket, nonce=nonce, frame_url=url_for('device_frame', ticket=ticket))
        finally:
            g.runner.lock.release()

    @app.get('/device/frame/<ticket>')
    def device_frame(ticket):
        _, _, setup = load(ticket)
        script_nonce = secrets.token_urlsafe(24)
        origin = request.host_url.rstrip('/')
        response = app.make_response(render_template('device_frame.html', script_nonce=script_nonce,
            server=origin + url_for('device_collect', ticket=ticket), nonce=setup['nonce'],
            parent_origin=origin, app_id=DEVICE_APP_ID))
        # The pinned first-party SDK requires browser storage. Keep its dynamic
        # code in a dedicated same-origin frame, with its network restricted to
        # this exact ticket-scoped relay. The top-level CSP stays strict.
        response.headers['Content-Security-Policy'] = ("default-src 'none'; script-src 'nonce-" + script_nonce +
            "' 'unsafe-eval'; connect-src " + origin + url_for('device_collect', ticket=ticket) +
            "; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'; form-action 'none'; base-uri 'none'; sandbox allow-scripts allow-same-origin")
        return response

    @app.route('/device/collect/<ticket>', methods=['GET', 'OPTIONS'])
    @app.route('/device/collect/<ticket>/udid/c1', methods=['GET', 'OPTIONS'])
    def device_collect(ticket):
        user, store, setup = load(ticket)
        if request.method == 'OPTIONS':
            return '', 204
        rate('device-collect:' + str(user['id']) + ':' + setup['nonce'], 6, 300)
        if request.query_string:
            raise ValueError('设备请求格式无效。')
        try:
            result = collector.collect(request.headers.get('Param'), request.headers.get('User-Agent', ''), request.host_url.rstrip('/'))
        except ValueError as exc:
            with store.lock:
                current = store.get('device_setup')
                if current and current['nonce'] == setup['nonce']:
                    current['error'] = str(exc)
                    store.put('device_setup', current)
            raise
        with store.lock:
            current = store.get('device_setup')
            if not current or current['nonce'] != setup['nonce'] or current['expires'] < time.time():
                raise ValueError('设备初始化已过期，请重新点击初始化。')
            issued = current['issued']
            if result['data'] not in [item['token'] for item in issued]:
                issued.append({'token': result['data'], 'user_agent': request.headers.get('User-Agent', '')})
            current['issued'] = issued[-6:]
            store.put('device_setup', current)
        return jsonify(result)

    @app.get('/api/device/status')
    @auth
    def device_status():
        setup = g.store.get('device_setup') or {}
        return jsonify(error=setup.get('error', '自动设备初始化未完成，请重试或使用备用导入。'))

    @app.post('/api/device/finish')
    @auth
    def device_finish():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get('ticket'), str) or not valid_device_token(body.get('token')):
            raise ValueError('设备初始化结果无效，请重新初始化。')
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后重试。'), 409
        try:
            user, store, setup = load(body['ticket'])
            if user['id'] != g.user['id']:
                return jsonify(error='此设备初始化不属于当前账号。'), 403
            pending = store.get('pending_sms')
            if pending and pending.get('expires', 0) > time.time():
                raise ValueError('请先完成当前短信验证，再更新设备。')
            issued = next((item for item in setup['issued'] if item['token'] == body['token']), None)
            if not issued:
                raise ValueError('未取得设备服务认可的标识，请重新初始化。')
            store.put('automatic_device', {'fingerprint': issued['token'], 'risk_token': issued['token'],
                'user_agent': issued['user_agent'], 'kind': 'browser_sdk', 'expires': time.time() + 900})
            store.put('device_setup', None)
            return jsonify(message='当前设备已初始化，可以尝试短信登录。自动设备模式为试用，实际签到兼容性仍需确认。')
        finally:
            g.runner.lock.release()
