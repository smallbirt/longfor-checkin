import re
import ssl
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from .constants import PROVIDERS

ADDRESS = re.compile(r'^[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')


def validate_mail(data, old=None):
    old = old or {}
    provider = data.get('provider')
    sender = str(data.get('sender', '')).strip()
    recipient = str(data.get('recipient', '')).strip()
    password = str(data.get('password') or '')
    if not password and old.get('provider') == provider and old.get('sender') == sender:
        password = old.get('password', '')
    if provider not in PROVIDERS:
        raise ValueError('请选择 QQ 或 163 邮箱。')
    if not ADDRESS.fullmatch(sender) or sender.rsplit('@', 1)[1].lower() != PROVIDERS[provider]['domain']:
        raise ValueError('发件邮箱需为所选服务的完整 qq.com 或 163.com 地址。')
    if not ADDRESS.fullmatch(recipient):
        raise ValueError('请填写一个有效的收件邮箱。')
    if not password or any(c in password for c in '\r\n'):
        raise ValueError('请填写 SMTP 授权码，不是邮箱登录密码。')
    return {'provider': provider, 'sender': sender, 'recipient': recipient, 'password': password}


def send_mail(config, subject, body):
    if not config:
        return False, '尚未配置邮箱。'
    p = PROVIDERS[config['provider']]
    msg = EmailMessage()
    msg['From'] = config['sender']
    msg['To'] = config['recipient']
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=False)
    msg['Message-ID'] = make_msgid()
    msg.set_content(body)
    try:
        # SSL with certificate and hostname verification; no insecure fallback.
        with smtplib.SMTP_SSL(p['host'], p['port'], timeout=20, context=ssl.create_default_context()) as smtp:
            smtp.login(config['sender'], config['password'])
            refused = smtp.send_message(msg)
            if refused:
                return False, '收件地址被拒绝，请检查收件邮箱。'
        return True, ''
    except smtplib.SMTPAuthenticationError:
        return False, '邮箱认证失败，请确认 SMTP 已开启并更新授权码。'
    except smtplib.SMTPRecipientsRefused:
        return False, '收件地址被拒绝，请检查收件邮箱。'
    except smtplib.SMTPResponseException as exc:
        return False, '邮箱服务拒绝发送（SMTP %s），请检查发信限制和授权码。' % exc.smtp_code
    except (OSError, smtplib.SMTPException):
        return False, '邮件连接失败，请检查服务器网络及出站 465 端口。'
