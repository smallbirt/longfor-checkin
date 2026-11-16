from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
BASE_URL = 'https://gw2c-hw-open.longfor.com/lmarketing-task-api-mvc-prod/openapi/task/v1'
CHANNELS = {
    'app': {'name': '龙湖 App', 'channel': 'L0', 'bu_code': 'L00602',
            'activity_no': '11111111111736501868255956070000', 'source': '2'},
    'mini': {'name': '微信小程序', 'channel': 'C2', 'bu_code': 'C20400',
             'activity_no': '11111111111686241863606037740000', 'source': '5'},
}
PROVIDERS = {'qq': {'host': 'smtp.qq.com', 'port': 465, 'domain': 'qq.com'},
             '163': {'host': 'smtp.163.com', 'port': 465, 'domain': '163.com'}}
SAFE_HEADERS = {'token', 'x-lf-usertoken', 'x-lf-channel', 'x-lf-bu-code',
                'x-lf-dxrisk-token', 'x-lf-dxrisk-source', 'x-gaia-api-key',
                'cookie', 'user-agent', 'referer', 'origin'}
# Captcha proofs are short-lived and are intentionally never stored or replayed.
RISK_CODES = {'8040012', '8040016'}
AUTH_CODES = {'801001', '801007', '801009', '900005', '900006'}
OK_CODES = {'0000', '200'}
