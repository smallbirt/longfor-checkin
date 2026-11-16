import { useState } from 'react';
import { Input, Text, View } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import { api, bindCurrentWechat, WechatBinding } from '../../shared/api';
import { Btn, Card, ErrorBox, Heading, Note, useAction } from '../../shared/ui';

export default function WechatAccount() {
  const action = useAction();
  const [binding, setBinding] = useState<WechatBinding>();
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const reload = async () => setBinding(await api<WechatBinding>('/api/wechat/binding'));
  useDidShow(() => { void action.run(reload); });
  function bind() {
    setMessage('');
    void action.run(() => bindCurrentWechat(password), async () => {
      setPassword(''); setMessage('绑定成功，下次可使用微信快捷登录。'); await reload();
    });
  }
  async function unbind() {
    const result = await Taro.showModal({ title: '解除微信登录绑定？', content: '解绑后微信快捷登录失效，其他设备需重新登录。签到计划、积分和订阅通知保留。', confirmText: '确认解绑' });
    if (!result.confirm) return;
    setMessage('');
    await action.run(() => api('/api/wechat/unbind', { password }), async () => {
      setPassword(''); setMessage('已解除绑定，你仍可使用账号密码登录。'); await reload();
    });
  }
  return <View className='screen'><Heading title='微信账号绑定' subtitle='一个微信，对应一个平台账号' />
    <ErrorBox message={action.error} />{message && <Note>{message}</Note>}
    <Card><Text className='section-title'>{!binding ? '正在读取…' : binding.bound ? '已绑定微信登录' : '尚未绑定微信登录'}</Text>
      <Text className='muted'>{binding?.bound ? '已验证的微信可直接登录当前账号。' : '绑定当前微信后，原来的签到账号、计划和积分会继续沿用。'}</Text>
      {binding?.bound_at && <Text className='muted small'>绑定时间：{binding.bound_at.replace('T', ' ').slice(0, 19)}</Text>}
      {binding && <><Text className='field-label'>验证本站账号密码</Text><Input className='field' password maxlength={128} value={password} onInput={event => setPassword(event.detail.value)} placeholder='请输入当前平台账号密码' />
        {binding.bound ? <Btn danger busy={action.busy} disabled={!password} onClick={() => void unbind()}>解除微信登录绑定</Btn> : <Btn busy={action.busy} disabled={!password || !binding.enabled} onClick={bind}>绑定当前微信</Btn>}
        {!binding.enabled && <Note>管理员尚未开启微信登录。已有绑定可以解绑，账号密码登录仍可使用。</Note>}</>}
    </Card><Note>只绑定你本人的微信。切换微信或平台账号前，需先在原账号解除对应绑定。微信订阅通知仍需单独授权。</Note>
  </View>;
}
