"""Native login protocol. Captures are data; only fixed first-party URLs are called."""
import base64
import hashlib
import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit
import requests
from .constants import CHANNELS
from .login_constants import SIGNING_INPUTS, APP_LOGIN_API_KEY, CHECKIN_API_KEYS

LOGIN_BASE = 'https://gw2c-hw-open.longfor.com/supera/mine/v1_29_2/publicApi/login/'
LOGIN_PATHS = {'/supera/mine/v1_29_2/publicApi/login/smsCodeSecret': 'app',
               '/supera/mine/v2_5_0/publicApi/login/thirdSmsSecret': 'mini'}


class LoginError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def phone_number(value):
    if not isinstance(value, str) or not re.fullmatch(r'1[3-9][0-9]{9}', value):
        raise ValueError('请填写 11 位中国大陆手机号。')
    return value


def mask_phone(value):
    return value[:3] + '****' + value[-4:]


def canonical(body):
    def scalar(value):
        if isinstance(value, dict):
            return '{' + canonical(value) + '}'
        if isinstance(value, list):
            return '[' + ','.join(scalar(x) for x in value) + ']'
        if value is None:
            return 'null'
        if isinstance(value, bool):
            return 'true' if value else 'false'
        return str(value)
    parts = []
    for key in sorted(body):
        value = scalar(body[key])
        if value.strip() and value != 'null':
            parts.append(key + '=' + value)
    return '|'.join(parts)


def signed_headers(body, channel='app', timestamp=None):
    stamp = str(int(time.time() * 1000) if timestamp is None else timestamp)
    value = canonical(body)
    material = (value + '&' if value else '') + stamp + '&' + SIGNING_INPUTS[channel]
    return {'X-LONGZHU-TimeStamp': stamp,
            'X-LONGZHU-Sign': hashlib.md5(material.encode()).hexdigest(),
            'X-Client-Type': 'app' if channel == 'app' else 'microApp'}


def valid_token(token):
    return isinstance(token, str) and re.fullmatch(r'[0-9a-fA-F]{32}', token) is not None


def checkin_credential(channel, token, previous=None):
    if not valid_token(token):
        raise LoginError('登录结果缺少有效凭证，请重新在手机登录。')
    c = CHANNELS[channel]
    headers = {'token': token, 'x-lf-usertoken': token, 'x-lf-channel': c['channel'],
               'x-lf-bu-code': c['bu_code'], 'x-gaia-api-key': CHECKIN_API_KEYS[channel],
               'x-lf-dxrisk-source': c['source']}
    # Native login fingerprint is NOT a check-in risk token. Keep only the user's
    # existing sign-page device context, never a captured one-time captcha proof.
    old = (previous or {}).get('headers', {})
    for key in ('x-lf-dxrisk-token', 'user-agent', 'referer', 'origin'):
        if old.get(key):
            headers[key] = old[key]
    result = {'headers': headers, 'activity_no': c['activity_no']}
    if old.get('x-lf-dxrisk-token'):
        if old.get('x-lf-dxrisk-source') in ('1', '2', '3', '5'):
            headers['x-lf-dxrisk-source'] = old['x-lf-dxrisk-source']
        if (previous or {}).get('device_kind') in ('shared_default', 'browser_sdk'):
            result['device_kind'] = previous['device_kind']
    return result


def parse_login_entries(entries):
    found = {}
    for entry in entries:
        try:
            req = entry['request']
            url = urlsplit(req['url'])
            channel = LOGIN_PATHS.get(url.path)
            if req.get('method') != 'POST' or url.scheme != 'https' or url.netloc != 'gw2c-hw-open.longfor.com' or not channel:
                continue
            h = {x['name'].lower(): x['value'] for x in req['headers']}
            c = CHANNELS[channel]
            if h.get('x-lf-channel') != c['channel'] or h.get('x-lf-bucode') != c['bu_code']:
                continue
            body = json.loads(req['postData']['text'])
            response = entry['response']
            if response['status'] != 200:
                continue
            content = response['content']
            text = content['text']
            if content.get('encoding') == 'base64':
                text = base64.b64decode(text, validate=True).decode('utf-8')
            result = json.loads(text)
            token = result.get('data', {}).get('token')
            if str(result.get('code')) != '200' or not valid_token(token):
                continue
            phone = phone_number(base64.b64decode(body['mobile'], validate=True).decode('ascii'))
            fingerprint = body['fingerprint']
            ua = h.get('user-agent', '')
            if body.get('prefix') != '+86' or not isinstance(fingerprint, str) or not re.fullmatch(r'[A-Za-z0-9+/=_-]{20,256}', fingerprint):
                continue
            if not isinstance(ua, str) or len(ua) > 1024 or '\r' in ua or '\n' in ua:
                continue
            stamp = datetime.fromisoformat(entry['startedDateTime'].replace('Z', '+00:00')).timestamp()
            # Do not retain mobile, code, thirdId, cookies, or the raw HAR.
            profile = {'fingerprint': fingerprint, 'user_agent': ua, 'phone_hint': mask_phone(phone)}
            candidate = {'stamp': stamp, 'credential': checkin_credential(channel, token), 'profile': profile}
            if channel not in found or stamp > found[channel]['stamp']:
                found[channel] = candidate
        except (KeyError, ValueError, TypeError, AttributeError, UnicodeError):
            continue
    return found


class LoginClient:
    def __init__(self, transport=None):
        self.transport = transport

    def request(self, endpoint, phone, profile, code=None):
        phone_number(phone)
        fingerprint = profile.get('fingerprint', '')
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[A-Za-z0-9+/=_-]{20,256}', fingerprint):
            raise LoginError('请先导入自己 App 成功登录的 HAR 文件，配置短信登录设备信息。')
        body = {'mobile': base64.b64encode(phone.encode()).decode(), 'prefix': '+86',
                'fingerprint': fingerprint, 'buCode': 'L00602', 'channel': 'L0'}
        if code is not None:
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
                raise LoginError('请填写 6 位短信验证码。')
            body['code'] = code
        headers = {'Content-Type': 'application/json', 'X-LF-Bucode': 'L00602',
                   'X-LF-Channel': 'L0', 'X-LF-Api-Version': 'v1_29_2',
                   'X-LF-App-Version': '1.29.2', 'X-LF-Stage': 'RELEASE',
                   'X-LF-Bundle-id': 'com.longfor.supera', 'X-Gaia-Api-Key': APP_LOGIN_API_KEY,
                   'User-Agent': profile.get('user_agent', ''), **signed_headers(body)}
        transport = self.transport or requests.Session()
        if self.transport is None:
            transport.trust_env = False
        try:
            r = transport.post(LOGIN_BASE + endpoint, headers=headers, json=body,
                               timeout=(10, 20), allow_redirects=False)
            if r.status_code != 200 or len(r.content) > 1024 * 1024:
                raise LoginError('登录接口响应异常，请在手机查看登录情况。', uncertain=True)
            result = r.json()
            if not isinstance(result, dict):
                raise ValueError()
            if str(result.get('code')) != '200':
                # Never echo a server message that might contain a phone/OTP/token.
                raise LoginError('龙湖未接受本次请求，可能需要手机验证、更新设备信息或重新获取验证码。')
            return result.get('data')
        except requests.RequestException:
            raise LoginError('请求超时或网络异常，结果未确认；请勿连续重发。', uncertain=True) from None
        except (TypeError, ValueError) as exc:
            if isinstance(exc, LoginError):
                raise
            raise LoginError('登录响应无法解析，请在手机检查。', uncertain=True) from None
        finally:
            if self.transport is None:
                transport.close()

    def send(self, phone, profile):
        self.request('sendSmsCodeSecret', phone, profile)

    def verify(self, phone, code, profile):
        result = self.request('smsCodeSecret', phone, profile, code)
        token = result.get('token') if isinstance(result, dict) else None
        if not valid_token(token):
            raise LoginError('短信校验响应缺少有效凭证，请在手机重新登录。', uncertain=True)
        return token
