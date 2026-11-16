"""One fresh Longfor slider proof per call. Never accepts account credentials."""
import secrets
import string
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

API = 'https://ly-ver.longhu.net/api'
APP_ID = 'd1a43734fc59aeae9f1562dbd70fdf54'
JS_VERSION = '1.3.41.372'


class CaptchaError(Exception):
    pass


class SliderSolver:
    def __init__(self, transport=None, sleeper=time.sleep):
        self.transport = transport or requests.Session()
        if transport is None:
            self.transport.trust_env = False
        self.sleeper = sleeper

    def request(self, method, url, image=False, **kwargs):
        parsed = urlsplit(url)
        allowed = parsed.scheme == 'https' and (
            (parsed.netloc == 'devops-prod.longfor.com' and parsed.path == '/v2/api/uv/type' and method == 'GET') or
            (parsed.netloc == 'ly-ver.longhu.net' and parsed.path in ['/api/a', '/api/p1', '/api/p2'] and method == 'GET') or
            (parsed.netloc == 'ly-ver.longhu.net' and parsed.path == '/api/v1' and method == 'POST'))
        if not allowed:
            raise CaptchaError('验证码地址异常。')
        try:
            response = self.transport.request(method, url, timeout=(10, 20), allow_redirects=False, **kwargs)
            if response.status_code != 200 or len(response.content) > (2 * 1024 * 1024 if image else 128 * 1024):
                raise CaptchaError('验证码服务响应异常。')
            if image:
                return response.content
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (requests.RequestException, ValueError):
            raise CaptchaError('验证码服务连接或响应异常。') from None

    def solve(self):
        # Lazy imports: status queries and application startup do not load OpenCV.
        from PIL import Image
        from .vendor.longfor_captcha.decode_o_string import decode_o_string
        from .vendor.longfor_captcha.restore_correct import restore_image_correct
        from .vendor.longfor_captcha.image_recognition_v2 import find_gap_position
        from .vendor.longfor_captcha.trajectory_enhanced import generate_realistic_trajectory
        from .vendor.longfor_captcha.ac_encoder_full import generate_ac

        kind = self.request('GET', 'https://devops-prod.longfor.com/v2/api/uv/type', params={'appId': 'LdIPdF3earg'})
        if str(kind.get('code')) != '200' or kind.get('data') != 'c':
            raise CaptchaError('当前验证类型不支持自动处理。')
        aid = 'dx-%s-%s-1' % (int(time.time() * 1000), secrets.randbelow(90000000) + 10000000)
        challenge = secrets.token_hex(4) + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        params = dict(w=300, h=150, s=50, ak=APP_ID, c=challenge, jsv=JS_VERSION,
                      aid=aid, wp=1, de=0, uid='', lf=0, tpc='', _r=secrets.randbelow(10**12) / 10**12)
        data = self.request('GET', API + '/a', params=params)
        if data.get('success') is not True or data.get('result') != 1 or not isinstance(data.get('sid'), str):
            raise CaptchaError('未能获取滑块题目。')
        y = data.get('y')
        if not isinstance(y, (int, float)) or not 0 <= y <= 150:
            raise CaptchaError('验证码坐标格式异常。')
        with tempfile.TemporaryDirectory(prefix='longfor-slider-') as folder:
            paths = []
            for name in ['p1', 'p2']:
                relative = data.get(name)
                if not isinstance(relative, str) or not relative.startswith('/api/' + name + '?'):
                    raise CaptchaError('验证码图片地址异常。')
                url = 'https://ly-ver.longhu.net' + relative
                if urlsplit(url).path != '/api/' + name:
                    raise CaptchaError('验证码图片地址异常。')
                path = Path(folder) / (name + '.png')
                path.write_bytes(self.request('GET', url, image=True))
                paths.append(str(path))
            with Image.open(paths[0]) as background, Image.open(paths[1]) as piece:
                width, height = background.size
                if width > 2048 or height > 2048 or width < 32 or height < 50 or piece.width > width or piece.height > height:
                    raise CaptchaError('验证码图片尺寸异常。')
            order = data.get('o')
            if isinstance(order, str):
                order = decode_o_string(order)
            if not isinstance(order, list) or len(order) != 32 or sorted(order) != list(range(32)):
                raise CaptchaError('验证码图片排列格式已变化。')
            restored = restore_image_correct(paths[0], order, width // 32, debug=False)
            x, _ = find_gap_position(restored, paths[1])
            if not 0 <= x <= 250:
                raise CaptchaError('未能确定有效滑动位置。')
            points = generate_realistic_trajectory(x)
            proof_data = generate_ac(points, x, y, sid=data['sid'])
            self.sleeper(points[-1][2] / 1000)
            result = self.request('POST', API + '/v1', data=dict(
                ac=proof_data, ak=APP_ID, c=challenge, uid='', jsv=JS_VERSION,
                sid=data['sid'], aid=aid, x=x, y=y))
        token = result.get('token')
        if result.get('success') is not True or not valid_proof(token):
            raise CaptchaError('自动滑块验证未通过，请在手机完成验证。')
        return token


def valid_proof(value):
    return isinstance(value, str) and 0 < len(value) <= 8192 and not any(c in value for c in '\r\n')


def solve_captcha():
    solver = SliderSolver()
    try:
        return solver.solve()
    finally:
        solver.transport.close()
