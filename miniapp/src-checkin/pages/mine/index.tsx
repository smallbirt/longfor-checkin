import { Text, View } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { api, BASE_URL, needLogin } from '../../shared/api';
import { Btn, Card, ErrorBox, go, Heading, Load, Note, useAction, usePageState } from '../../shared/ui';

export default function Mine() {
  const { value: s, error, loading } = usePageState(); const action = useAction();
  async function logout() {
    const result = await Taro.showModal({ title: '退出平台登录？', content: '云端已开启的签到计划会继续运行。', confirmText: '退出登录' });
    if (result.confirm) await action.run(() => api('/api/session/logout', {}), needLogin);
  }
  return <View className='screen'><Heading title='我的' subtitle='通知和使用帮助，都在这里' /><ErrorBox message={error || action.error} /><Load loading={loading} />{s && <><Card className='profile-card'><View className='avatar'>{s.user.username.slice(0, 1).toUpperCase()}</View><View><Text className='account-name'>{s.user.username}</Text><Text className='muted'>已连接 {s.accounts.filter(a => a.configured).length} 个签到账号</Text></View></Card>
    <Card><View className='menu-row' onClick={() => go('notifications')}><View><Text className='account-name'>通知设置</Text><Text className='muted small'>{s.mail.verified ? s.mail.recipient : '配置自己的收件邮箱'}</Text></View><Text>›</Text></View><View className='menu-row' onClick={() => go('notifications')}><View><Text>小程序推送提醒</Text><Text className='muted small'>{!s.mini_push.configured ? '等待管理员配置订阅模板' : s.mini_push.enabled ? '已开启 · 全部账号异常提醒' : '订阅全部账号的复核失败提醒'}</Text></View><Text>›</Text></View></Card>
    <Card><View className='menu-row' onClick={() => go('wechat')}><View><Text>微信账号绑定</Text><Text className='muted small'>绑定后使用微信快捷登录</Text></View><Text>›</Text></View><View className='menu-row' onClick={() => go('help')}><Text>操作指南</Text><Text>›</Text></View><View className='menu-row' onClick={() => go('video')}><Text>视频教程</Text><Text>›</Text></View></Card><Note>微信小程序与网页端共用平台账号、签到账号和计划。关闭小程序后，云端仍会按计划执行。</Note><Text className='server-info'>当前服务：{BASE_URL}</Text><Btn secondary busy={action.busy} onClick={() => void logout()}>退出登录</Btn></>}</View>;
}
