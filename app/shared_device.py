"""Administrator-owned device template. Account credentials are never shared."""
import re
import secrets
import json
from .constants import CHANNELS
from .store import now

KEY = 'shared_device'


def header(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and all(32 <= ord(c) < 127 for c in value)


def native_profile(value):
    value = value or {}
    fingerprint, ua = value.get('fingerprint', ''), value.get('user_agent', '')
    if isinstance(fingerprint, str) and re.fullmatch(r'[A-Za-z0-9+/=_-]{20,256}', fingerprint) and header(ua, 1024):
        return {'fingerprint': fingerprint, 'user_agent': ua, 'kind': 'shared_default'}
    return {}


def sign_device(value, source, ua=''):
    value = value or {}
    risk = value.get('risk_token', '')
    source = value.get('source', source)
    ua = value.get('user_agent') or ua
    if header(risk, 16384) and source in ('1', '2', '3', '5') and (not ua or header(ua, 1024)):
        return {'risk_token': risk, 'source': source, 'user_agent': ua}
    return {}


def from_credential(cred, channel, ua=''):
    headers = (cred or {}).get('headers', {})
    return sign_device({'risk_token': headers.get('x-lf-dxrisk-token', ''),
        'source': headers.get('x-lf-dxrisk-source', CHANNELS[channel]['source']),
        'user_agent': headers.get('user-agent', '')}, CHANNELS[channel]['source'], ua)


def device_snapshot(root, partial=False):
    """Build from an explicit allowlist, never serialize an owner's credential."""
    old = root.get(KEY) or {}
    profile = native_profile(root.get('login-profile:app')) or native_profile(old.get('profile'))
    ua = profile.get('user_agent', '')
    if not partial and not profile:
        raise ValueError('请先为管理员导入 App 成功登录的设备配置，再启用平台默认设备。')
    devices = {}
    for key, channel in CHANNELS.items():
        device = from_credential(root.get('credential:' + key), key, ua) or sign_device(old.get('devices', {}).get(key), channel['source'], ua)
        if device:
            devices[key] = device
    if not partial and set(devices) != set(CHANNELS):
        raise ValueError('请先为管理员导入 App 和小程序各自的签到页设备信息，再启用平台默认设备。')
    return {'profile': profile, 'devices': devices}


def sync(root):
    """Publish admin device changes automatically; an explicit disable stays off."""
    with root.lock:
        old = root.get(KEY) or {}
        snapshot = device_snapshot(root, partial=True)
        if not old and not snapshot['profile'] and not snapshot['devices']:
            return None
        enabled = old.get('enabled', True)
        if all(old.get(k) == snapshot[k] for k in ('profile', 'devices')) and old.get('enabled') == enabled:
            return old
        snapshot.update(enabled=enabled, generation=secrets.token_urlsafe(24), updated_at=now().isoformat())
        root.put(KEY, snapshot)
        return snapshot


def configure(root, enabled):
    if not isinstance(enabled, bool):
        raise ValueError('默认设备状态无效。')
    with root.lock:
        config = device_snapshot(root) if enabled else device_snapshot(root, partial=True)
        config.update(enabled=enabled, generation=secrets.token_urlsafe(24), updated_at=now().isoformat())
        root.put(KEY, config)
    return config


def active(root):
    config = root.get(KEY) or {}
    return config if config.get('enabled') is True else None


def summary(root):
    config = root.get(KEY) or {}
    return {'enabled': config.get('enabled') is True,
            'channels': sorted(config.get('devices', {})) if config.get('enabled') else [],
            'profile_available': bool(native_profile(config.get('profile'))),
            'missing': ([] if native_profile(config.get('profile')) else ['App 短信登录设备']) +
                [CHANNELS[k]['name'] + '签到设备' for k in CHANNELS if k not in config.get('devices', {})],
            'updated_at': config.get('updated_at')}


def resolve(root, store, targets):
    config = active(root) or {}
    own = store.get('login-profile:app') or {}
    personal = bool(native_profile(own))
    profile = own if personal else native_profile(config.get('profile'))
    profile_source = 'personal' if personal else 'platform' if profile else 'missing'
    devices, sources = {}, {}
    for key in targets:
        previous = store.get('credential:' + key) or {}
        own_device = from_credential(previous, key, profile.get('user_agent', ''))
        default = sign_device(config.get('devices', {}).get(key), CHANNELS[key]['source'], profile.get('user_agent', ''))
        if own_device and previous.get('device_kind') != 'shared_default':
            devices[key], sources[key] = own_device, 'personal'
        elif default:
            devices[key], sources[key] = default, 'platform'
        elif own_device:
            devices[key], sources[key] = own_device, 'saved'
        else:
            sources[key] = 'missing'
    uses_shared = profile_source == 'platform' or 'platform' in sources.values()
    return {'profile': profile, 'profile_source': profile_source, 'devices': devices, 'device_sources': sources,
            'shared_generation': config.get('generation') if uses_shared else None}


def import_bundle(root, raw):
    if len(raw) > 65536:
        raise ValueError('设备配置文件不能超过 64 KB。')
    try:
        payload = json.loads(raw)
        profile = native_profile(payload.get('profile'))
        devices = {k: sign_device(payload.get('devices', {}).get(k), c['source'], profile.get('user_agent', '')) for k, c in CHANNELS.items()}
        if payload.get('version') != 1 or not profile or not all(devices.values()):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise ValueError('设备配置文件无效，请使用导出的设备专用配置；不要上传账号 Token 或完整抓包。') from None
    with root.lock:
        config = {'profile': profile, 'devices': devices, 'enabled': True,
                  'generation': secrets.token_urlsafe(24), 'updated_at': now().isoformat()}
        root.put(KEY, config)
        # The administrator's newer imported sign-page devices take precedence.
        return sync(root)


def ensure_current(root, pending):
    if pending.get('device_mode') == 'shared' or pending.get('shared_generation'):
        config = active(root)
        if not config or config.get('generation') != pending.get('shared_generation'):
            raise ValueError('平台默认设备已更新或关闭，请重新获取短信验证码。')
