import { useState } from 'react';
import { Input, Text, View } from '@tarojs/components';
import { accountStatus } from '../../shared/types';
import { AccountCard, Btn, Empty, ErrorBox, go, Heading, Load, usePageState } from '../../shared/ui';

export default function Accounts() {
  const { value: s, loading, error, reload } = usePageState();
  const [program, setProgram] = useState('all');
  const [status, setStatus] = useState('all');
  const [search, setSearch] = useState('');
  const filtered = (s?.accounts || []).filter(a => (program === 'all' || a.channel === program) && a.alias.toLowerCase().includes(search.trim().toLowerCase()) && (status === 'all' || (status === 'paused' ? !a.enabled : accountStatus(a, s?.today || '') === status)));
  return <View className='screen'><Heading title='我的签到' subtitle='每个账号独立配置，互不覆盖' action='添加' onAction={() => go('bind')} /><ErrorBox message={error} />
    <Input className='field search' value={search} placeholder='搜索账号昵称' onInput={e => setSearch(e.detail.value)} /><View className='chips'>{[['all', '全部程序'], ['app', '龙湖 App'], ['mini', '龙湖小程序']].map(([id, text]) => <View key={id} className={'chip ' + (program === id ? 'active' : '')} onClick={() => setProgram(id)}>{text}</View>)}</View>
    <View className='chips compact'>{[['all', '全部状态'], ['signed', '已签到'], ['unsigned', '未签到'], ['auth_expired', '登录失效'], ['paused', '已暂停']].map(([id, text]) => <View key={id} className={'chip ' + (status === id ? 'active' : '')} onClick={() => setStatus(id)}>{text}</View>)}</View><Load loading={loading} />
    {s && <><Text className='muted list-count'>共 {filtered.length} 个账号</Text>{filtered.length ? filtered.map(a => <AccountCard key={a.id} account={a} today={s.today} running={s.running} reload={reload} />) : <Empty title={s.accounts.length ? '没有匹配的账号' : undefined} text={s.accounts.length ? '试试其他程序、状态或昵称。' : undefined} action={s.accounts.length ? '清除筛选' : undefined} onAction={s.accounts.length ? () => { setProgram('all'); setStatus('all'); setSearch(''); } : undefined} />}<Btn onClick={() => go('bind')}>＋ 添加签到账号</Btn></>}
  </View>;
}
