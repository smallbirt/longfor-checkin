"""Opt-in mini SMS experiment: omit WeChat auth, never replay a captured code.

Only a user's explicit send/verify action reaches these endpoints. HTTP failures
are summarized from an allowlist; raw server messages and auth values stay private.
"""
import base64
import hashlib
import hmac
import logging
import math
import re
import time
import requests
from flask import g, jsonify, request
from . import shared_device
from .login_client import LoginError, phone_number, mask_phone, signed_headers, valid_token, checkin_credential
from .store import now

BASE = 'https://gw2c-hw-open.longfor.com/supera/mine/v2_5_0/publicApi/login/'
APP_ID = 'wx50282644351869da'
SOURCE = '4E00F235-60AA-E311-8D79-0050568001F7'
PENDING = 'pending_mini_sms'
RETRY = 'mini_sms_retry_at'
log = logging.getLogger(__name__)


def enabled(app):
    return app.config.get('MINI_SMS_EXPERIMENTAL') is True


def selection(root, store):
    config = shared_device.active(root) or {}
    profile = shared_device.native_profile(store.get('login-profile:mini'))
    source = 'personal' if profile else 'missing'
    if not profile and config:
        profile = shared_device.native_profile(root.get('login-profile:mini'))
        source = 'platform' if profile else 'missing'
    result = shared_device.resolve(root, store, ['mini'])
    result.update(profile=profile, profile_source=source)
    uses_shared = source == 'platform' or result['device_sources']['mini'] == 'platform'
    result['shared_generation'] = config.get('generation') if uses_shared else None
    return result


def state(app, root, store):
    active = enabled(app)
    current = selection(root, store) if active else {}
    pending = store.get(PENDING) or {}
    valid = active and pending.get('expires', 0) > time.time() and pending.get('attempts', 5) < 5
    return {'sms_available': active and bool(current.get('profile')), 'experimental': active,
        'import_available': True, 'method': 'mini_sms_without_wechat_experiment' if active else 'wechat_native_import',
        'profile_source': current.get('profile_source', 'missing'), 'pending': valid,
        'pending_phone': pending.get('phone_hint') if valid else None,
        'retry_after': max(0, math.ceil(max(store.get(RETRY, 0), store.get('sms_retry_at', 0)) - time.time()))}


class MiniLoginClient:
    def __init__(self, transport=None):
        self.transport = transport

    def request(self, phone, profile, code=None):
        phone_number(phone)
        profile = shared_device.native_profile(profile)
        if not profile:
            raise LoginError('缺少小程序登录设备，请导入小程序成功登录记录。')
        body = {'appId': APP_ID, 'thirdType': 'WX_APPLET', 'prefix': '+86',
            'mobile': base64.b64encode(phone.encode()).decode(), 'fingerprint': profile['fingerprint']}
        if code is None:
            endpoint = 'thirdSendSecret'
            body['riskToken'] = ''
        else:
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
                raise LoginError('请填写 6 位短信验证码。')
            endpoint = 'thirdSmsSecret'
            body.update(code=code, source=SOURCE)
        # Intentionally omit thirdId/authCode/ticket; never invent, copy or replay them.
        headers = {'Content-Type': 'application/json', 'X-LF-Channel': 'C2', 'X-LF-Bucode': 'C20400',
            'X-LF-App-Version': '2.5.0', 'X-LF-Api-Version': 'v2_5_0',
            'Referer': 'https://servicewechat.com/' + APP_ID + '/523/page-frame.html',
            'User-Agent': profile['user_agent'], **signed_headers(body, 'mini')}
        transport = self.transport or requests.Session()
        if self.transport is None:
            transport.trust_env = False
        try:
            response = transport.post(BASE + endpoint, headers=headers, json=body,
                timeout=(10, 20), allow_redirects=False)
            if response.status_code != 200:
                status = response.status_code if isinstance(response.status_code, int) and 100 <= response.status_code <= 599 else '未知'
                raise LoginError('小程序短信测试：HTTP ' + str(status) + '，结果未确认；请勿连续重发。', uncertain=True)
            if len(response.content) > 1024 * 1024:
                raise ValueError()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            if str(result.get('code')) != '200':
                business = str(result.get('code', ''))
                safe_code = business if re.fullmatch(r'[0-9]{1,10}', business) else '未提供可显示业务码'
                message = str(result.get('msg', result.get('message', '')))
                if any(term in message.lower() for term in ('thirdid', 'authcode', 'ticket', '授权', '微信')):
                    category = '微信授权相关'
                elif any(term in message for term in ('签名', 'signature', 'sign error')):
                    category = '签名相关'
                elif any(term in message.lower() for term in ('fingerprint', '设备', 'risk', '风控')):
                    category = '设备或风控相关'
                elif any(term in message for term in ('验证码', '短信')):
                    category = '短信校验相关'
                elif any(term in message for term in ('参数', '不能为空', '必填')):
                    category = '参数校验相关'
                else:
                    category = '服务端拒绝，原因尚未确认'
                log.warning('longfor_mini_login_error operation=%s business_code=%s category=%s', endpoint, safe_code, category)
                raise LoginError('小程序短信测试未通过：业务码 ' + safe_code + '；' + category + '。本次未携带微信授权参数，旧凭证已保留。')
            return result.get('data')
        except requests.RequestException:
            raise LoginError('小程序短信请求超时或网络异常，结果未确认；收到短信后可填写，请勿连续重发。', uncertain=True) from None
        except (ValueError, TypeError) as exc:
            if isinstance(exc, LoginError):
                raise
            raise LoginError('小程序短信响应无法解析，结果未确认；请勿连续重发。', uncertain=True) from None
        finally:
            if self.transport is None:
                transport.close()

    def send(self, phone, profile):
        self.request(phone, profile)

    def verify(self, phone, code, profile):
        result = self.request(phone, profile, code)
        token = result.get('token') if isinstance(result, dict) else None
        if not valid_token(token):
            raise LoginError('小程序登录响应缺少有效凭证，旧凭证已保留。', uncertain=True)
        return token


def install(app, root, auth, rate, data, validate_login, client=None):
    sms = client or MiniLoginClient()

    def guard():
        if not enabled(app):
            raise ValueError('小程序短信测试入口尚未启用。')

    @app.post('/api/login/mini/send')
    @auth
    def mini_sms_send():
        guard()
        phone = phone_number(data().get('phone'))
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后获取验证码。'), 409
        try:
            picked = selection(root, g.store)
            if not picked['profile']:
                raise ValueError('缺少小程序登录设备，请导入小程序成功登录记录，或由管理员补充。')
            digest = hmac.new(root.key, phone.encode(), hashlib.sha256).hexdigest()
            # Same limits as App SMS; switching channels must not bypass them.
            rate('sms-user:' + str(g.user['id']), 1, 60)
            rate('sms-user-hour:' + str(g.user['id']), 5, 3600)
            rate('sms-phone:' + digest, 5, 3600)
            rate('sms-ip:' + (request.remote_addr or ''), 15, 3600)
            g.store.put(PENDING, None)
            g.store.put(RETRY, time.time() + 60)
            pending = {'phone': phone, 'phone_hint': mask_phone(phone), 'expires': time.time() + 600,
                'attempts': 0, 'target': 'mini', 'device_mode': 'mini_experiment', **picked}
            try:
                sms.send(phone, picked['profile'])
            except LoginError as exc:
                if not exc.uncertain:
                    raise
                g.store.put(PENDING, pending)
                return jsonify(message=str(exc), uncertain=True)
            g.store.put(PENDING, pending)
            return jsonify(message='已请求小程序短信到 ' + mask_phone(phone) + '。请填写本次收到的验证码；发送与登录能否完成仍待验证。')
        finally:
            g.runner.lock.release()

    @app.post('/api/login/mini/verify')
    @auth
    def mini_sms_verify():
        guard()
        code = data().get('code')
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
            raise ValueError('请填写 6 位短信验证码。')
        if not g.runner.lock.acquire(blocking=False):
            return jsonify(error='你的任务正在运行，请稍后验证。'), 409
        try:
            pending = g.store.get(PENDING) or {}
            if pending.get('expires', 0) <= time.time() or pending.get('attempts', 5) >= 5 or pending.get('target') != 'mini':
                g.store.put(PENDING, None)
                raise ValueError('小程序短信验证已过期或尝试次数已用完，请重新获取。')
            def ensure_current():
                shared_device.ensure_current(root, pending)
                if selection(root, g.store)['profile'] != pending['profile']:
                    raise ValueError('小程序登录设备已更新，请重新获取验证码。')
            try:
                ensure_current()
            except ValueError:
                g.store.put(PENDING, None)
                raise
            pending['attempts'] += 1
            g.store.put(PENDING, pending)
            try:
                token = sms.verify(pending['phone'], code, pending['profile'])
            except LoginError as exc:
                if exc.uncertain or pending['attempts'] >= 5:
                    g.store.put(PENDING, None)
                raise
            g.store.put(PENDING, None)
            cred = checkin_credential('mini', token, g.store.get('credential:mini'))
            device = pending.get('devices', {}).get('mini')
            if device:
                cred['headers'].update({'x-lf-dxrisk-token': device['risk_token'], 'x-lf-dxrisk-source': device['source']})
                if device['user_agent']:
                    cred['headers']['user-agent'] = device['user_agent']
                if pending['device_sources']['mini'] in ('platform', 'saved'):
                    cred['device_kind'] = 'shared_default'
            result = validate_login(cred)
            ensure_current()
            cred['imported_at'] = now().isoformat()
            g.store.put('credential:mini', cred)
            g.store.put('status:mini', result)
            return jsonify(message='小程序短信登录及状态查询通过，已保存小程序凭证。App 凭证保持原样；实际签到仍需另行确认。')
        finally:
            g.runner.lock.release()
