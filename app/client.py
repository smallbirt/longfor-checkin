import json
import logging
import requests
from .constants import BASE_URL, CHANNELS, OK_CODES, AUTH_CODES, RISK_CODES
from .store import now
from .captcha import solve_captcha, valid_proof


logger = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, state, message, reason=None, http_status=None, business_code=None):
        super().__init__(message)
        self.state = state
        # Only explicitly constructed metadata is retained. Never log exception
        # strings, response messages/bodies, headers, URLs or captcha proofs.
        self.diagnostic = {}
        if reason in ('http', 'business', 'timeout', 'tls', 'connection', 'network',
                      'invalid_response', 'response_too_large'):
            self.diagnostic['reason'] = reason
        if type(http_status) is int and 100 <= http_status <= 599:
            self.diagnostic['http_status'] = http_status
        if isinstance(business_code, str) and business_code.isascii() and business_code.isdigit() and len(business_code) <= 10:
            self.diagnostic['business_code'] = business_code


class LongforClient:
    def __init__(self, transport=None, captcha_solver=solve_captcha):
        self.transport = transport or requests.Session()
        self.captcha_solver = captcha_solver
        if transport is None:
            self.transport.trust_env = False

    def request(self, cred, endpoint, risk=False, captcha_token=None):
        def failure(state, message, reason, http_status=None, business_code=None):
            error = APIError(state, message, reason, http_status, business_code)
            channel = next((key for key, value in CHANNELS.items()
                            if cred.get('headers', {}).get('x-lf-channel') == value['channel']), 'unknown')
            diagnostic = {'channel': channel,
                'operation': {'/signature/page-info': 'query', '/signature/clock': 'submit'}.get(endpoint, 'unknown'),
                'captcha_proof_used': bool(captcha_token), **error.diagnostic}
            logger.warning('longfor_api_error %s', json.dumps(diagnostic, ensure_ascii=True))
            return error

        h = dict(cred['headers'])
        h.pop('x-lf-dxrisk-captcha-token', None)
        if not risk:
            h = {k: v for k, v in h.items() if not k.startswith('x-lf-dxrisk')}
        h['Content-Type'] = 'application/json;charset=UTF-8'
        if risk and captcha_token is not None:
            if not valid_proof(captcha_token):
                raise APIError('captcha_required', '未获得有效验证码凭证。')
            h['x-lf-dxrisk-captcha-token'] = captcha_token
        try:
            # Only fixed service endpoints; never use a URL supplied by an import.
            r = self.transport.post(BASE_URL + endpoint, headers=h,
                json={'activity_no': cred['activity_no']}, timeout=(10, 20), allow_redirects=False)
            if r.status_code == 401:
                raise failure('auth_expired', '登录失效，请在手机重新登录并更新凭证。', 'http', r.status_code)
            if r.status_code != 200:
                raise failure('request_error', '接口请求失败（HTTP %s），稍后查询状态。' % r.status_code, 'http', r.status_code)
            if len(r.content) > 1024 * 1024:
                raise failure('unknown', '接口响应过大，无法确认签到状态。', 'response_too_large', r.status_code)
            try:
                d = r.json()
            except ValueError:
                raise failure('unknown', '接口响应格式异常，尚不能确认签到结果。', 'invalid_response', r.status_code) from None
            if not isinstance(d, dict):
                raise failure('unknown', '接口响应格式异常，尚不能确认签到结果。', 'invalid_response', r.status_code)
        except requests.Timeout:
            raise failure('unknown', '接口请求超时，尚不能确认签到结果。', 'timeout') from None
        except requests.exceptions.SSLError:
            raise failure('unknown', '接口 TLS 连接异常，尚不能确认签到结果。', 'tls') from None
        except requests.ConnectionError:
            raise failure('unknown', '接口连接失败，尚不能确认签到结果。', 'connection') from None
        except requests.RequestException:
            raise failure('unknown', '接口网络异常，尚不能确认签到结果。', 'network') from None
        except ValueError:
            raise failure('unknown', '接口响应格式异常，尚不能确认签到结果。', 'invalid_response') from None
        code = str(d.get('code', ''))
        if code in AUTH_CODES:
            raise failure('auth_expired', '登录失效，请在手机重新登录并更新凭证。', 'business', r.status_code, code)
        if code in RISK_CODES:
            raise failure('captcha_required', '需要人工验证，请打开对应手机签到页完成验证。', 'business', r.status_code, code)
        if code not in OK_CODES:
            safe_code = code if code.isascii() and code.isdigit() and len(code) <= 10 else '未知'
            raise failure('request_error', '服务端返回业务码 %s，请在手机查看活动状态。' % safe_code, 'business', r.status_code, safe_code)
        data = d.get('data')
        if not isinstance(data, dict):
            raise failure('unknown', '响应字段不完整，不能确认签到状态。', 'invalid_response', r.status_code, code)
        return data

    def status(self, cred, day):
        d = self.request(cred, '/signature/page-info')
        rows = d.get('seven_days_signs')
        if not isinstance(rows, list):
            raise APIError('unknown', '缺少签到日历，未执行签到。')
        target = [int(x) for x in day.split('-')]
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                matches = [int(row.get(k, 0)) for k in ('sign_year', 'sign_month', 'sign_day')] == target
            except (ValueError, TypeError):
                continue
            if matches:
                status = str(row.get('sign_status'))
                if status == '20':
                    return True, d
                if status == '10':
                    return False, d
        raise APIError('unknown', '没有找到当天的明确签到状态，未提交签到。')

    def run(self, cred, day, allow_sign=True):
        captcha_attempted = False
        captcha_solved = False
        diagnostics = []
        stage = 'initial_status'
        def note(error, failed_stage):
            if error.diagnostic:
                diagnostic = {'stage': failed_stage, **error.diagnostic}
                if not diagnostics or diagnostics[-1] != diagnostic:
                    diagnostics.append(diagnostic)
        def result(state, message, **extra):
            return {'state': state, 'message': message, 'checked_at': now().isoformat(),
                    'captcha_attempted': captcha_attempted, 'captcha_solved': captcha_solved,
                    **({'diagnostics': diagnostics} if diagnostics else {}), **extra}
        try:
            signed, page = self.status(cred, day)
            if signed:
                return result('signed', '已向服务端确认今日已签到。')
            if not allow_sign:
                return result('unsigned', '服务端显示今日未签到。')
            if str(page.get('task_show_status')) != '20':
                return result('inactive', '活动未开始或已结束，未提交签到。')
            if cred.get('blocked') and cred['blocked'] != 'captcha_required':
                return result(cred['blocked'], '等待手机处理登录或验证；今日仍未签到，请处理后更新凭证。')
            if cred['headers'].get('x-lf-dxrisk-token') in (None, '', 'undefined', 'null'):
                return result('credentials_missing', '缺少有效设备指纹，请导入完整签到请求。')
            clock = None
            submit_error = None
            try:
                needs_captcha = cred.get('blocked') == 'captcha_required'
                if not needs_captcha:
                    try:
                        stage = 'submit'
                        clock = self.request(cred, '/signature/clock', risk=True)
                    except APIError as exc:
                        if exc.state != 'captcha_required':
                            raise
                        note(exc, stage)
                        needs_captcha = True
                if needs_captcha:
                    captcha_attempted = True
                    try:
                        proof = self.captcha_solver()
                    except Exception:
                        # Neither solver exceptions nor proof contents enter logs/results.
                        proof = None
                    if not valid_proof(proof):
                        return result('captcha_required', '自动滑块验证未完成，请在手机签到页完成验证。')
                    captcha_solved = True
                    try:
                        stage = 'status_after_captcha'
                        signed, current = self.status(cred, day)
                    except APIError as exc:
                        if exc.state == 'auth_expired':
                            raise
                        note(exc, stage)
                        return result('unknown', '滑块验证通过，但状态复核失败；未再次提交签到。')
                    if signed:
                        return result('signed', '滑块验证后查询确认今日已签到，无需再次提交。')
                    if str(current.get('task_show_status')) != '20':
                        return result('inactive', '滑块验证后活动已关闭，未再次提交。')
                    stage = 'submit_after_captcha'
                    clock = self.request(cred, '/signature/clock', risk=True, captcha_token=proof)
            except APIError as exc:
                if exc.state in ('auth_expired', 'captcha_required'):
                    raise
                note(exc, stage)
                submit_error = exc
            # A timeout may have occurred after the server committed the sign-in.
            # Query only; never blindly retry a POST in this run.
            try:
                stage = 'verify_status'
                signed, _ = self.status(cred, day)
            except APIError as exc:
                note(exc, stage)
                return result('unknown', '已尝试签到，但复核失败：' + str(exc) + ' 稍后请查询，避免重复提交。')
            if signed:
                rewards = []
                for item in (clock or {}).get('reward_info', []):
                    if isinstance(item, dict) and isinstance(item.get('reward_num'), (int, float)):
                        kind = {20: '成长值', 10: '珑珠'}.get(item.get('reward_type'), '奖励')
                        rewards.append('%s %g' % (kind, item['reward_num']))
                message = '自动滑块验证通过，签到后已确认今日状态。' if captcha_solved else '签到后已确认今日状态。'
                return result('signed', message, rewards=rewards)
            if submit_error:
                return result('unknown', '签到请求异常：' + str(submit_error) + ' 复核仍未签到；本轮不重复提交。')
            return result('unknown', '签到接口已响应，但今日状态尚未确认；请稍后查询。')
        except APIError as exc:
            note(exc, stage)
            if exc.state == 'captcha_required' and captcha_attempted:
                return result('captcha_required', '自动验证后仍需要手机验证，本轮停止提交；请在手机签到页处理。')
            return result(exc.state, str(exc))
