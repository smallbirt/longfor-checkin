"""Explain import failures using fixed labels and numeric codes, never raw auth data."""
import base64
import json
from urllib.parse import urlsplit
from .constants import CHANNELS
from .login_client import LOGIN_PATHS


def numeric_code(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        value=str(value)
        if value and value.isascii() and value.isdigit() and len(value)<=10:
            return value
    return None


def capture_failure_hint(entries):
    known=[]
    other_login=False
    for entry in entries:
        if not isinstance(entry,dict):
            continue
        req=entry.get('request') or {}
        if not isinstance(req,dict):
            continue
        try:
            url=urlsplit(req.get('url',''))
        except (ValueError,TypeError,AttributeError):
            continue
        if url.scheme!='https' or url.netloc!='gw2c-hw-open.longfor.com' or req.get('method')!='POST':
            continue
        channel=LOGIN_PATHS.get(url.path)
        if channel:
            known.append((channel,entry))
        elif '/publicApi/login/' in url.path:
            other_login=True
    if not known:
        if other_login:
            return '抓到了龙湖登录相关请求，但没有当前支持的短信登录成功接口，可能使用了微信一键登录、自动登录或接口版本已变化。需要分析这份 HAR 适配，不是抓得越久越好。'
        return '没有找到当前支持的成功登录或签到请求。请从退出登录后、重新登录前开始记录；仅打开已登录首页通常不足以导入。'
    # Prefer a success-shaped response over an earlier failed attempt. All text
    # below is constructed locally; no URLs, response messages or tokens escape.
    reasons=[]
    for channel,entry in known:
        name=CHANNELS[channel]['name']
        response=entry.get('response') or {}
        if not isinstance(response,dict):
            response={}
        content=response.get('content') or {}
        if not isinstance(content,dict) or not content.get('text'):
            reasons.append((0,name+'：已找到登录请求，但没有响应正文。请确认 HTTPS 内容可解密，导出时包含响应内容，并等登录完成后再停止。'))
            continue
        try:
            text=content['text']
            if content.get('encoding')=='base64':
                text=base64.b64decode(text,validate=True).decode('utf-8')
            result=json.loads(text)
            if not isinstance(result,dict):
                raise ValueError()
        except (ValueError,TypeError,UnicodeError):
            reasons.append((1,name+'：登录响应正文无法解析。请导出包含原始 JSON 响应的 HAR；若仍失败需要适配此文件。'))
            continue
        code=numeric_code(result.get('code'))
        if response.get('status')!=200 or code!='200':
            status=response.get('status')
            details=[]
            if type(status) is int and 100<=status<=599:
                details.append('HTTP '+str(status))
            if code:
                details.append('业务码 '+code)
            reasons.append((2,name+'：找到登录请求，但响应未满足当前成功条件'+('（'+' / '.join(details)+'）' if details else '')+'。请确认手机确已登录成功；若已成功，需要检查接口格式兼容性。'))
            continue
        reasons.append((3,name+'：登录响应成功，但 Token、请求设备字段、渠道或记录时间与当前解析规则不匹配。请提供这份 HAR 检查适配，不需要延长抓包时间。'))
    return max(reasons,key=lambda row:row[0])[1]


def validation_failure_hint(cred,result):
    channel=next((key for key,value in CHANNELS.items() if (cred.get('headers') or {}).get('x-lf-channel')==value['channel']),None)
    name=CHANNELS[channel]['name'] if channel else '所选渠道'
    state=result.get('state') if isinstance(result,dict) else None
    if not isinstance(state,str):
        state=None
    labels={'auth_expired':'服务端判定新登录失效','captcha_required':'状态查询要求验证',
        'request_error':'服务端拒绝状态查询','unknown':'未获得明确的今日签到状态'}
    details=[]
    reason=None
    diagnostics=result.get('diagnostics') if isinstance(result,dict) else None
    if isinstance(diagnostics,list):
        for item in reversed(diagnostics):
            if not isinstance(item,dict):
                continue
            code=numeric_code(item.get('business_code'))
            status=item.get('http_status')
            if code:
                details.append('业务码 '+code)
            if type(status) is int and 100<=status<=599:
                details.append('HTTP '+str(status))
            reasons={'timeout':'查询超时','tls':'TLS 连接异常','connection':'连接失败',
                'network':'网络异常','invalid_response':'响应格式不匹配','response_too_large':'响应过大'}
            if isinstance(item.get('reason'),str) and item['reason'] in reasons:
                reason=item['reason'];details.append(reasons[reason])
            if details:
                break
    message=name+'：已识别登录凭证，但自动状态查询未通过；'+labels.get(state,'未获得明确的今日签到状态')
    if details:
        message+='（'+' / '.join(details)+'）'
    message+='。旧凭证已保留。'
    if reason in ('timeout','tls','connection','network'):
        return message+'请先检查运行服务的网络，不必重新抓更久。'
    if state=='auth_expired':
        return message+'请重新登录后导出新的成功登录响应；若新包仍失败，需检查登录与签到接口兼容性。'
    return message+'请提供这份 HAR 和此错误码分析，不要仅靠延长抓包时间反复导入。'
