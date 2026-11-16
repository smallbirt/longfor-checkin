"""Parse untrusted captures as data only. Never execute pasted cURL or follow its URL."""
import json
import shlex
from datetime import datetime
from urllib.parse import urlsplit
from .constants import CHANNELS, SAFE_HEADERS, BASE_URL


class ImportError(ValueError):
    pass


def normalize(headers, body, selected=None, url=None):
    if url:
        u = urlsplit(url)
        if u.scheme != 'https' or u.netloc != 'gw2c-hw-open.longfor.com' or u.path not in {
            urlsplit(BASE_URL).path + '/signature/clock',
            urlsplit(BASE_URL).path + '/signature/page-info',
        }:
            raise ImportError('仅接受龙湖签到或签到状态接口的请求。')
    if not isinstance(headers, dict) or not isinstance(body, dict):
        raise ImportError('请求头和请求体必须是 JSON 对象。')
    h = {str(k).lower().strip(): str(v).strip() for k, v in headers.items()
         if str(k).lower().strip() in SAFE_HEADERS}
    if any('\r' in v or '\n' in v or len(v) > 16000 for v in h.values()):
        raise ImportError('请求头含无效换行或过长内容。')
    found = [k for k, c in CHANNELS.items() if h.get('x-lf-channel') == c['channel']]
    if not found or (selected and found[0] != selected):
        raise ImportError('请求渠道与所选 App／小程序不一致，请复制对应渠道的签到请求。')
    key = found[0]
    c = CHANNELS[key]
    activity = str(body.get('activity_no') or c['activity_no'])
    if activity != c['activity_no'] or h.get('x-lf-bu-code') != c['bu_code']:
        raise ImportError('活动编号或业务编码与当前渠道不一致；活动变更时需要更新配置。')
    token = h.get('x-lf-usertoken') or h.get('token')
    if not token or token in ('null', 'undefined'):
        raise ImportError('缺少登录 token。')
    if h.get('token') and h.get('x-lf-usertoken') and h['token'] != h['x-lf-usertoken']:
        raise ImportError('两个登录 token 不一致，请重新复制完整请求。')
    if not h.get('x-gaia-api-key'):
        raise ImportError('缺少 X-GAIA-API-KEY。')
    if h.get('x-lf-dxrisk-source') and h['x-lf-dxrisk-source'] != c['source']:
        raise ImportError('运行环境编号与渠道不一致。')
    h['token'] = h['x-lf-usertoken'] = token
    h['x-lf-dxrisk-source'] = c['source']
    return key, {'headers': h, 'activity_no': activity}


def parse_har(raw, allow_empty=False, with_timestamps=False, with_devices=False):
    try:
        doc = json.loads(raw)
        entries = doc['log']['entries']
        if not isinstance(entries, list):
            raise ValueError()
        result, devices = {}, {}
        # HAR entries may be reverse chronological. Never pick by array order.
        candidates = []
        for e in entries:
            r = e.get('request', {})
            if r.get('method') != 'POST' or not any(p in r.get('url', '') for p in ('/signature/clock', '/signature/page-info')):
                continue
            try:
                key, cred = normalize({h['name']: h['value'] for h in r['headers']},
                                      json.loads(r['postData']['text']), url=r['url'])
                stamp = datetime.fromisoformat(e['startedDateTime'].replace('Z', '+00:00')).timestamp()
                candidates.append((stamp, key, cred))
            except (KeyError, ValueError, TypeError):
                continue
        for stamp, key, cred in sorted(candidates, key=lambda c: c[0]):
            result[key] = (stamp, cred) if with_timestamps else cred
            if cred['headers'].get('x-lf-dxrisk-token'):
                devices[key] = cred
        if not result and not allow_empty:
            raise ImportError('未找到可用的签到或签到状态请求。打开对应签到页后导出包含请求头和请求体的 HAR，无需实际点击签到。')
        return (result, devices) if with_devices else result
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ImportError):
            raise
        raise ImportError('HAR 格式无效。') from None


def parse_capture(raw):
    """Web upload supports both sign-page requests and successful native logins."""
    from .login_client import parse_login_entries
    signs, devices = parse_har(raw, allow_empty=True, with_timestamps=True, with_devices=True)
    logins = parse_login_entries(json.loads(raw)['log']['entries'])
    merged = {}
    for channel in set(signs) | set(logins):
        sign, login = signs.get(channel), logins.get(channel)
        if login and (not sign or login['stamp'] >= sign[0]):
            merged[channel] = {'credential': login['credential'], 'login': True,
                               'profile': login['profile'], 'device': devices.get(channel)}
        else:
            cred = sign[1]
            device = devices.get(channel)
            if device and not cred['headers'].get('x-lf-dxrisk-token'):
                # A newer read-only query can omit risk headers. Keep its account
                # token, but retain the newest actual sign device independently.
                cred = {**cred, 'headers': {**cred['headers']}}
                for key in ('x-lf-dxrisk-token', 'x-lf-dxrisk-source'):
                    if device['headers'].get(key):
                        cred['headers'][key] = device['headers'][key]
                if not cred['headers'].get('user-agent') and device['headers'].get('user-agent'):
                    cred['headers']['user-agent'] = device['headers']['user-agent']
            merged[channel] = {'credential': cred, 'login': False,
                               'profile': login['profile'] if login else None}
    if not merged:
        from .import_diagnostics import capture_failure_hint
        raise ImportError(capture_failure_hint(json.loads(raw)['log']['entries']))
    return merged


def parse_text(text, selected):
    text = text.strip()
    headers, body, url = {}, {}, None
    if text.startswith('curl '):
        try:
            args = shlex.split(text.replace('\\\n', ''))
            i = 1
            while i < len(args):
                arg = args[i]
                if arg in ('-H', '--header', '-b', '--cookie', '-d', '--data', '--data-raw', '--data-binary', '--url'):
                    i += 1
                    value = args[i]
                    if arg in ('-H', '--header'):
                        k, v = value.split(':', 1)
                        headers[k] = v
                    elif arg in ('-b', '--cookie'):
                        if value.startswith('@'):
                            raise ValueError()
                        headers['cookie'] = value
                    elif arg == '--url':
                        url = value
                    else:
                        body = json.loads(value)
                elif arg.startswith('https://'):
                    url = arg
                i += 1
            if not url:
                raise ValueError()
        except (ValueError, IndexError):
            raise ImportError('无法解析 cURL，请使用含 -H 和 --data-raw 的完整请求，不能引用本地文件。') from None
    elif text.startswith('{'):
        try:
            doc = json.loads(text)
            headers = doc.get('headers', doc)
            body = doc.get('body', {})
            url = doc.get('url') if 'headers' in doc else None
        except (ValueError, AttributeError):
            raise ImportError('JSON 格式错误。') from None
    else:
        for line in text.splitlines():
            if line.startswith(':') or ':' not in line:
                continue
            k, v = line.split(':', 1)
            headers[k] = v
    key, cred = normalize(headers, body, selected=selected, url=url)
    return {key: cred}
