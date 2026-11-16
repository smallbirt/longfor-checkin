import { useState } from 'react';
import { Input, Text, View } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { BASE_URL, login, session, wechatLogin, bindWechatLogin } from '../../shared/api';
import { Btn, Card, ErrorBox, Note, useAction } from '../../shared/ui';

export default function Login() {
  const action = useAction();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [register, setRegister] = useState(false);
  const [registration, setRegistration] = useState(false);
  const [visible, setVisible] = useState(false);
  const [ready, setReady] = useState(false);
  const [preview, setPreview] = useState(false);
  const [validation, setValidation] = useState('');
  const [wechatEnabled, setWechatEnabled] = useState(false);
  const [ticket, setTicket] = useState('');
  useDidShow(() => { void action.run(session, s => { setRegistration(s.registration_enabled); setReady(true); setPreview(s.preview); setWechatEnabled(Boolean(s.wechat_login_enabled)); if (s.user) afterLogin(); }); });
  function afterLogin() {
    const target = Taro.getStorageSync('checkin-return-result') as { id?: string; receipt?: string; accountId?: string } | undefined;
    Taro.removeStorageSync('checkin-return-result');
    if (target?.id && /^[1-9][0-9]*$/.test(target.id)) {
      Taro.reLaunch({ url: '/pages/result/index?job_id=' + target.id + (target.receipt ? '&receipt=' + encodeURIComponent(target.receipt) : '') + (target.accountId ? '&account_id=' + encodeURIComponent(target.accountId) : '') });
    } else Taro.switchTab({ url: '/pages/home/index' });
  }
  function submit() {
    setValidation('');
    if (!username.trim() || !password) { setValidation('请填写用户名和密码。'); return; }
    if (register && password !== confirm) { setValidation('两次密码不一致。'); return; }
    void action.run(() => ticket ? bindWechatLogin(ticket, username.trim(), password) : login(username.trim(), password, register), () => { setPassword(''); setConfirm(''); setTicket(''); afterLogin(); });
  }
  function startWechat() {
    setValidation('');
    void action.run(wechatLogin, result => {
      if (result.bind_required && result.ticket) { setTicket(result.ticket); setRegister(false); setPassword(''); setConfirm(''); }
      else afterLogin();
    });
  }
  return <View className='screen login-screen'><View className='brand-mark'>✓</View><Text className='eyebrow'>DAILY CHECK-IN</Text><Text className='login-title'>每天的签到，<Text className='teal'>安排好了。</Text></Text><Text className='muted'>多个程序，多个账号，一个地方管理。</Text>
    {preview && <Note>本地演示环境：使用示例账号，签到、短信和邮件均为模拟。</Note>}
    <Card><Text className='section-title'>{ticket ? '绑定已有平台账号' : register ? '注册平台账号' : '登录每日签到'}</Text><Text className='muted small'>{ticket ? '此微信尚未绑定账号。验证密码后确认一对一绑定，下次可直接使用微信登录。' : '使用现有签到平台的账号和密码，与龙湖账号分开。'}</Text>
      {!ticket && !register && <><Btn busy={action.busy} disabled={!ready || !wechatEnabled} onClick={startWechat}>微信快捷登录</Btn><Text className='muted small center'>{wechatEnabled ? '首次使用需绑定本站账号' : '微信登录待管理员开启，可先使用账号密码'}</Text></>}
      <Text className='field-label'>用户名</Text><Input className='field' maxlength={32} value={username} placeholder='请输入平台用户名' onInput={e => setUsername(e.detail.value)} />
      <Text className='field-label'>密码</Text><View className='password-field'><Input className='field grow' password={!visible} maxlength={128} value={password} placeholder={register ? '设置 10–128 位密码' : '请输入密码'} onInput={e => setPassword(e.detail.value)} /><Text className='password-toggle' onClick={() => setVisible(!visible)}>{visible ? '隐藏' : '显示'}</Text></View>
      {register && <View><Text className='field-label'>确认密码</Text><Input className='field' password maxlength={128} value={confirm} placeholder='再次输入密码' onInput={e => setConfirm(e.detail.value)} /></View>}
      <ErrorBox message={validation || action.error} /><Btn busy={action.busy} disabled={!ready} onClick={submit}>{ticket ? '确认绑定并登录' : register ? '注册并登录' : '账号密码登录'}</Btn>
      {ticket && <Btn secondary disabled={action.busy} onClick={() => { setTicket(''); setPassword(''); setValidation(''); action.clear(); }}>取消绑定</Btn>}
      {registration && <Btn secondary disabled={action.busy} onClick={() => { setTicket(''); setRegister(!register); setPassword(''); setValidation(''); action.clear(); }}>{register ? '已有账号，去登录' : '还没有账号？注册'}</Btn>}
      {register && <Text className='muted small'>注册后可在“我的 → 微信账号绑定”中开启微信登录。</Text>}
      {!ready && !action.busy && <Btn secondary onClick={() => void action.run(session, s => { setRegistration(s.registration_enabled); setPreview(s.preview); setWechatEnabled(Boolean(s.wechat_login_enabled)); setReady(true); })}>重新连接服务</Btn>}
    </Card><Text className='muted small center'>首次使用需绑定你本人的龙湖账号。</Text><Text className='server-info'>签到服务：{BASE_URL}</Text></View>;
}
