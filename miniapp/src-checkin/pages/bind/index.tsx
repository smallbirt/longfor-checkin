import { useEffect, useState } from 'react';
import { Input, Picker, Switch, Text, View } from '@tarojs/components';
import Taro, { useRouter } from '@tarojs/taro';
import { api, state, upload } from '../../shared/api';
import { Account, Channel, PROGRAMS } from '../../shared/types';
import { Btn, Card, ErrorBox, go, Heading, Note, ProgramIcon, tab, useAction } from '../../shared/ui';

export default function Bind() {
  const existingId = useRouter().params.id;
  const action = useAction();
  const [step, setStep] = useState(existingId ? 2 : 1);
  const [account, setAccount] = useState<Account>();
  const [channel, setChannel] = useState<Channel>('app'); const [alias, setAlias] = useState('');
  const [sign, setSign] = useState('09:10'); const [check, setCheck] = useState('16:00'); const [enabled, setEnabled] = useState(false);
  const [message, setMessage] = useState(''); const [filename, setFilename] = useState(''); const [mailReady, setMailReady] = useState(false);
  useEffect(() => { void action.run(() => state(existingId), s => {
    setMailReady(s.notification_configured);
    if (existingId) { const a = s.accounts.find(a => a.id === existingId); if (!a) throw new Error('账号已移除或不属于当前用户。'); setAccount(a); setChannel(a.channel); setAlias(a.alias); setSign(a.sign_time); setCheck(a.check_time); setEnabled(a.enabled); }
  }); }, [existingId]);
  async function create() {
    await action.run(() => api<{ account: Account }>('/api/accounts', { channel, alias }), r => { setAccount(r.account); setStep(2); });
  }
  async function choose() {
    if (!account) return;
    await action.run(async () => {
      let file: Taro.chooseMessageFile.ChooseFile;
      try { const r = await Taro.chooseMessageFile({ count: 1, type: 'file' }); file = r.tempFiles[0]; }
      catch (e) { if (String((e as { errMsg?: string })?.errMsg).includes('cancel')) return; throw new Error('没有选到文件，请先把 HAR 发送到微信文件传输助手。'); }
      if (!file) return;
      if (file.size > 50 * 1024 * 1024) throw new Error('文件超过 50 MB，请缩短抓包过程后重新导出。');
      setFilename(file.name);
      const r = await upload(account.id, channel, file.path);
      const fresh = await state(account.id);
      const bound = fresh.accounts.find(a => a.id === account.id);
      if (!bound?.configured) throw new Error('登录信息尚未确认，请刷新账号状态。');
      setAccount(bound); setMessage(r.message); setMailReady(fresh.notification_configured); setStep(3);
    });
  }
  async function finish() {
    if (!account) return;
    await action.run(() => api('/api/accounts/' + account.id, { alias, sign_time: sign, check_time: check, enabled }), () => { setStep(4); });
  }
  return <View className='screen'><Heading title={existingId ? '更新登录信息' : '添加签到账号'} subtitle='同一程序也可以添加多个账号' /><View className='steps'>{['选择程序', '连接账号', '确认计划'].map((text, i) => <View key={text} className={'step ' + (step >= i + 1 ? 'active' : '')}><Text>{i + 1}</Text><Text>{text}</Text></View>)}</View><ErrorBox message={action.error} />
    {step === 1 && <><Card><Text className='section-title'>选择签到程序</Text>{(['app', 'mini'] as Channel[]).map(c => <View key={c} className={'program-option ' + (channel === c ? 'selected' : '')} onClick={() => setChannel(c)}><ProgramIcon channel={c} /><View className='grow'><Text className='account-name'>{PROGRAMS[c]}</Text><Text className='muted small'>支持多个独立账号</Text></View><Text className='selection-dot'>{channel === c ? '✓' : ''}</Text></View>)}<View className='program-option disabled'><Text className='muted'>更多签到程序</Text><Text className='muted small'>待接入</Text></View><Text className='field-label'>给这个账号起个昵称</Text><Input className='field' value={alias} maxlength={30} placeholder='例如：主号、家人号、备用号' onInput={e => setAlias(e.detail.value)} /><Btn busy={action.busy} disabled={!alias.trim()} onClick={() => void create()}>下一步，连接账号</Btn></Card><Note>创建后先保存为暂停状态，完成校验并由你确认后再自动执行。</Note></>}
    {step === 2 && <Card><Text className='section-title'>{PROGRAMS[channel]} · {account?.alias || alias}</Text><Text className='muted'>导入这个账号的 HAR，一次合并登录和设备信息。</Text><View className='instruction'><Text>1. 在对应 App / 小程序中开始抓包。</Text><Text>2. 完成一次本人的登录，打开签到页面。</Text><Text>3. 导出包含响应内容的 HAR。</Text><Text>4. 发到微信文件传输助手，再从这里选择。</Text></View><Note>请选对应端的文件。服务会先校验再保存，更新失败会保留旧配置。缺少签到设备时，可在同一次抓包中包含正常签到请求后重新导出。</Note>{filename && <Text className='file-name'>已选择：{filename}</Text>}<Btn busy={action.busy} disabled={!account} onClick={() => void choose()}>选择 HAR 并校验</Btn><Btn secondary disabled={action.busy} onClick={() => go('help')}>查看抓包说明</Btn>{account && <Btn secondary disabled={action.busy} onClick={() => tab('accounts')}>稍后继续，保留当前账号</Btn>}</Card>}
    {step === 3 && <Card><View className='success-mark'>✓</View><Text className='section-title'>登录文件已通过校验</Text><Text className='result-message'>{message}</Text><Text className='field-label'>账号昵称</Text><Input className='field' value={alias} maxlength={30} onInput={e => setAlias(e.detail.value)} /><Text className='field-label'>签到时间（北京时间）</Text><Picker mode='time' value={sign} onChange={e => setSign(e.detail.value)}><View className='field picker-value'>{sign}<Text>选择 ›</Text></View></Picker><Text className='field-label'>复核时间（北京时间）</Text><Picker mode='time' value={check} onChange={e => setCheck(e.detail.value)}><View className='field picker-value'>{check}<Text>选择 ›</Text></View></Picker><View className='setting-row'><View className='grow'><Text>开启自动执行</Text><Text className='muted small'>由你确认，默认先暂停</Text></View><Switch checked={enabled} color='#107f78' onChange={e => setEnabled(e.detail.value)} /></View>{!mailReady && <Note>开启前请先订阅小程序复核提醒或验证接收邮箱；现在也可以保持暂停并完成添加。</Note>}{!mailReady && <Btn secondary onClick={() => go('notifications')}>去设置通知</Btn>}<Note>开启后，若今日计划时间已过，会补执行最近一轮任务。</Note><Btn busy={action.busy} onClick={() => void finish()}>确认并保存</Btn><Btn secondary disabled={action.busy} onClick={() => setStep(2)}>重新选择登录文件</Btn></Card>}
    {step === 4 && <Card className='empty'><View className='success-mark'>✓</View><Text className='section-title'>{existingId ? '登录信息已更新' : '账号添加完成'}</Text><Text className='muted'>{PROGRAMS[channel]} · {alias}</Text><Text>{enabled ? '已开启自动执行' : '已保存，自动执行暂停中'}</Text><Btn onClick={() => tab('accounts')}>查看我的签到</Btn></Card>}
  </View>;
}
