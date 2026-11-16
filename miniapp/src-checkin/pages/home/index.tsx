import { Text, View } from '@tarojs/components';
import { accountStatus, PROGRAMS } from '../../shared/types';
import { AccountCard, Btn, Card, Empty, ErrorBox, go, Heading, Load, Note, Record, Section, tab, usePageState } from '../../shared/ui';

export default function Home() {
  const { value: s, loading, error, reload } = usePageState();
  const accounts = s?.accounts || [];
  const signed = accounts.filter(a => accountStatus(a, s?.today || '') === 'signed').length;
  const attention = accounts.filter(a => ['auth_expired', 'unknown', 'captcha_required', 'request_error', 'credentials_missing'].includes(accountStatus(a, s?.today || '')));
  const enabled = accounts.filter(a => a.enabled);
  const china = new Date(Date.now() + 8 * 3600000).toISOString();
  const hm = china.slice(11, 16);
  const next = enabled.flatMap(a => [{ time: a.sign_time, name: PROGRAMS[a.channel] + ' · ' + a.alias, type: '签到' }, { time: a.check_time, name: PROGRAMS[a.channel] + ' · ' + a.alias, type: '复核' }]).filter(t => t.time > hm).sort((a, b) => a.time.localeCompare(b.time))[0];
  return <View className='screen'><Heading title='今日签到' subtitle={s ? s.today + ' · 北京时间' : '让每天少一件挂心的事'} action='刷新' onAction={() => void reload()} /><ErrorBox message={error || s?.worker_error} /><Load loading={loading} />
    {s && <>{s.local_debug && <Note>本地真实接口：定时调度和后台通知投递未启动，手动操作使用真实服务。</Note>}<Card className='hero'><View className='row spread'><View><Text className='hero-caption'>今日已确认</Text><View className='progress-numbers'><Text className='hero-number'>{signed}</Text><Text className='hero-total'> / {accounts.length}</Text></View><Text className='hero-caption'>{enabled.length} 个账号运行中</Text></View><View className='progress-dial'><Text>{accounts.length ? Math.round(signed / accounts.length * 100) : 0}%</Text><Text className='small'>今日进度</Text></View></View><View className='progress-track'><View className='progress-fill' style={{ width: (accounts.length ? signed / accounts.length * 100 : 0) + '%' }} /></View><View className='hero-foot'><Text>{s.running ? '正在执行任务，请稍候…' : next ? '下一次 ' + next.time + ' · ' + next.name + ' · ' + next.type : enabled.length ? '今日计划时间已过，以运行记录为准' : '添加账号并确认计划后，自动执行'}</Text></View></Card>
      {accounts.length === 0 ? <Empty /> : <><Section title='需要处理' action='全部账号' onAction={() => tab('accounts')} />{attention.length ? attention.slice(0, 3).map(a => <AccountCard key={a.id} account={a} today={s.today} running={s.running} reload={reload} />) : <Card className='all-good'><View className='check-circle'>✓</View><View><Text className='section-title'>暂时没有待处理问题</Text><Text className='muted'>签到状态以服务端复核为准。</Text></View></Card>}
      <Section title='最近记录' action='更多' onAction={() => tab('history')} />{s.jobs.length ? s.jobs.slice(0, 5).map(job => <Record key={job.id} job={job} />) : <Note>任务执行后，记录会显示在这里。</Note>}</>}
      <Btn secondary onClick={() => go('bind')}>＋ 添加签到账号</Btn></>}
  </View>;
}
