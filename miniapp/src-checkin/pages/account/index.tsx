import { useEffect, useState } from 'react';
import { Input, Picker, Switch, Text, View } from '@tarojs/components';
import Taro, { useRouter } from '@tarojs/taro';
import { SubscribeCard } from '../../shared/SubscribeCard';
import { api } from '../../shared/api';
import { accountStatus, PROGRAMS } from '../../shared/types';
import { Btn, Card, ErrorBox, go, Heading, Load, Note, ProgramIcon, Record, Section, Status, tab, useAction, usePageState } from '../../shared/ui';

export default function AccountDetail() {
  const id = useRouter().params.id || '';
  const { value: s, error, loading, reload } = usePageState(id);
  const account = s?.accounts.find(a => a.id === id);
  const action = useAction();
  const [editing, setEditing] = useState(false);
  const [alias, setAlias] = useState(''); const [sign, setSign] = useState('09:10'); const [check, setCheck] = useState('16:00'); const [enabled, setEnabled] = useState(false);
  useEffect(() => { if (account && !editing) { setAlias(account.alias); setSign(account.sign_time); setCheck(account.check_time); setEnabled(account.enabled); } }, [account, editing]);
  async function save() { await action.run(() => api('/api/accounts/' + id, { alias, sign_time: sign, check_time: check, enabled }), async () => { setEditing(false); await reload(); Taro.showToast({ title: '配置已保存', icon: 'success' }); }); }
  async function execute(kind: string) { await action.run(() => api('/api/run', { account_id: id, kind }), async () => { Taro.showToast({ title: '任务已提交', icon: 'none' }); await reload(); }); }
  async function remove() {
    const r = await Taro.showModal({ title: '移除这个签到账号？', content: '会删除此账号的登录凭证并停止自动执行，保留历史记录。其他账号不受影响。', confirmText: '移除账号', confirmColor: '#b73939' });
    if (r.confirm) await action.run(() => api('/api/accounts/' + id + '/remove', {}), () => tab('accounts'));
  }
  const signed = account && s && accountStatus(account, s.today) === 'signed';
  return <View className='screen'><Heading title={account?.alias || '账号详情'} subtitle={account ? PROGRAMS[account.channel] : '管理这个账号的登录和计划'} /><ErrorBox message={error || action.error} /><Load loading={loading} />
    {!loading && !account && <Btn secondary onClick={() => tab('accounts')}>返回我的签到</Btn>}
    {account && s && <><SubscribeCard username={s.user.username} onSaved={() => void reload()} /><Card><View className='account-head'><ProgramIcon channel={account.channel} /><View className='grow'><Text className='account-name'>{account.alias}</Text><Text className='muted'>{PROGRAMS[account.channel]}{account.phone_hint ? ' · ' + account.phone_hint : ''}</Text></View><Status state={accountStatus(account, s.today)} /></View><Text className='result-message'>{account.status?.message || '还没有执行记录'}</Text><Text className='muted small'>登录状态：{!account.configured ? '尚未连接' : accountStatus(account, s.today) === 'auth_expired' ? '已失效，请更新' : '已保存，执行时验证'}</Text>
      {signed ? <Btn onClick={() => go('history', { account_id: id, day: s.today })}>查看今日结果</Btn> : <Btn busy={action.busy} disabled={!account.configured || s.running} onClick={() => void execute('manual')}>立即签到</Btn>}
      <Btn secondary busy={action.busy} disabled={!account.configured || s.running} onClick={() => void execute('check')}>{s.running ? '任务正在执行…' : '查询 / 复核状态'}</Btn></Card>
      <Section title='登录信息' /><Card><Text className='muted'>更新失败会保留原凭证，不会改动其他账号。</Text><Btn secondary onClick={() => go('bind', { id })}>{account.configured ? '重新导入登录文件' : '导入并连接账号'}</Btn>{account.channel === 'app' && <Btn secondary disabled={!account.configured} onClick={() => go('sms', { id })}>短信更新 App 登录</Btn>}{account.channel === 'mini' && <Text className='muted small'>小程序登录更新请重新导入对应端 HAR。</Text>}</Card>
      <Section title='独立签到计划' action={editing ? undefined : '修改配置'} onAction={() => setEditing(true)} /><Card>{editing ? <><Text className='field-label'>账号昵称</Text><Input className='field' value={alias} maxlength={30} onInput={e => setAlias(e.detail.value)} /><Text className='field-label'>签到时间（北京时间）</Text><Picker mode='time' value={sign} onChange={e => setSign(e.detail.value)}><View className='field picker-value'>{sign}<Text>修改 ›</Text></View></Picker><Text className='field-label'>复核时间（北京时间）</Text><Picker mode='time' value={check} onChange={e => setCheck(e.detail.value)}><View className='field picker-value'>{check}<Text>修改 ›</Text></View></Picker><View className='setting-row'><View className='grow'><Text>自动执行</Text><Text className='muted small'>开启前需先连接账号并验证邮箱</Text></View><Switch checked={enabled} color='#107f78' onChange={e => setEnabled(e.detail.value)} /></View><Note>开启后，若今日计划时间已过，后台会补执行最近一轮；已完成的计划不会重复执行。</Note><Btn busy={action.busy} onClick={() => void save()}>保存配置</Btn><Btn secondary disabled={action.busy} onClick={() => setEditing(false)}>取消修改</Btn></> : <><View className='setting-row'><Text>自动执行</Text><Text>{account.enabled ? '已开启' : '已暂停'}</Text></View><View className='setting-row'><Text>签到时间</Text><Text>{account.sign_time}</Text></View><View className='setting-row'><Text>复核时间</Text><Text>{account.check_time}</Text></View><Text className='muted small'>每天按北京时间执行，只影响此账号。</Text></>}</Card>
      <Section title='最近记录' action='全部' onAction={() => go('history', { account_id: id })} />{s.jobs.length ? s.jobs.map(job => <Record key={job.id} job={job} />) : <Note>还没有运行记录。</Note>}<Btn secondary danger busy={action.busy} onClick={() => void remove()}>移除这个账号</Btn></>}
  </View>;
}
