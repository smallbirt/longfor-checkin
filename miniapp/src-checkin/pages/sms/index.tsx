import { useEffect, useState } from 'react';
import { Input, Text, View } from '@tarojs/components';
import { useRouter } from '@tarojs/taro';
import { api } from '../../shared/api';
import { Btn, Card, ErrorBox, go, Heading, Load, Note, useAction, usePageState } from '../../shared/ui';

export default function Sms() {
  const id = useRouter().params.id || ''; const { value: s, error, loading, reload } = usePageState(id); const action = useAction();
  const account = s?.accounts.find(a => a.id === id);
  const [phone, setPhone] = useState(''); const [code, setCode] = useState(''); const [retry, setRetry] = useState(0); const [pending, setPending] = useState(false); const [message, setMessage] = useState('');
  useEffect(() => { if (s) { setRetry(s.login_methods.app.retry_after); setPending(s.login_methods.app.pending); } }, [s]);
  useEffect(() => { if (!retry) return; const timer = setTimeout(() => setRetry(retry - 1), 1000); return () => clearTimeout(timer); }, [retry]);
  const available = account?.channel === 'app' && s?.login_methods.app.effective_sms_available;
  return <View className='screen'><Heading title='短信更新 App 登录' subtitle={account ? '仅更新：' + account.alias : undefined} /><ErrorBox message={error || action.error} /><Load loading={loading} />{message && <Note>{message}</Note>}{s && <Card><Text className='muted'>首次需先导入本人的 App HAR；短信只更新当前 App 账号，小程序请重新导入文件。</Text>{!available && <Note>此账号尚缺少短信登录设备信息。请重新导入包含 App 成功登录响应的 HAR，或使用文件更新。</Note>}<Text className='field-label'>本人龙湖账号手机号</Text><Input className='field' type='number' maxlength={11} value={phone} placeholder='11 位手机号' onInput={e => setPhone(e.detail.value)} /><Btn secondary busy={action.busy} disabled={!available || !!retry || !/^1[3-9]\d{9}$/.test(phone)} onClick={() => void action.run(() => api('/api/login/app/send?account_id=' + encodeURIComponent(id), { phone, target: 'app', device_mode: 'preferred' }), async r => { setMessage(r.message); setPending(true); setRetry(60); setCode(''); await reload(); })}>{retry ? retry + ' 秒后可重发' : '获取短信验证码'}</Btn>{pending && <><Text className='muted small'>发送目标：{s.login_methods.app.pending_phone || '本次输入的手机号'}</Text><Text className='field-label'>短信验证码</Text><Input className='field' type='number' maxlength={6} value={code} placeholder='6 位验证码' onInput={e => setCode(e.detail.value)} /><Btn busy={action.busy} disabled={code.length !== 6} onClick={() => void action.run(() => api('/api/login/app/verify?account_id=' + encodeURIComponent(id), { code }), async r => { setMessage(r.message); setCode(''); setPending(false); await reload(); })}>验证并更新登录</Btn></>}<Btn secondary onClick={() => go('bind', { id })}>改用 HAR 更新</Btn><Note>验证结果未确认时不会自动重试，旧凭证保持不变。请先查看账号状态，再决定下一步。</Note></Card>}</View>;
}
