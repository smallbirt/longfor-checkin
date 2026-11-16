import { useRef, useState } from 'react';
import { Button, Picker, Text, View } from '@tarojs/components';
import { useDidHide, useDidShow } from '@tarojs/taro';
import { api, errorText, needLogin, unauthorized } from '../../shared/api';
import { Account, Job, PROGRAMS } from '../../shared/types';
import { Btn, Card, Empty, ErrorBox, Heading, Record, takeHistoryFilter } from '../../shared/ui';

type Filters = { channel: string; account_id: string; day: string; state: string };
const CLEAN: Filters = { channel: '', account_id: '', day: '', state: '' };
const STATE_OPTIONS = [['', '全部结果'], ['signed', '已签到'], ['unsigned', '未签到'], ['auth_expired', '登录失效'], ['unknown', '结果未确认'], ['request_error', '执行失败'], ['captcha_required', '需要验证'], ['credentials_missing', '未连接']];
export default function History() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const requestId = useRef(0);
  const [filters, setFilters] = useState<Filters>(CLEAN); const current = useRef<Filters>(CLEAN);
  const [accounts, setAccounts] = useState<Account[]>([]); const [jobs, setJobs] = useState<Job[]>([]); const [cursor, setCursor] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  async function load(f = current.current, append = false) {
    const request = ++requestId.current;
    setBusy(true); setError('');
    try {
      if (!append) { const s = await api<{ accounts: Account[] }>('/api/state'); if (request !== requestId.current) return; setAccounts(s.accounts); }
      const parts = Object.entries(f).filter(([, v]) => v).map(([k, v]) => k + '=' + encodeURIComponent(v));
      if (append && cursor) parts.push('before=' + cursor);
      const result = await api<{ jobs: Job[]; next_cursor: number | null }>('/api/jobs?' + parts.join('&'));
      if (request !== requestId.current) return;
      setJobs(prev => append ? [...prev, ...result.jobs] : result.jobs); setCursor(result.next_cursor); setLoaded(true);
    } catch (e) {
      if (request === requestId.current) { setError(errorText(e)); if (unauthorized(e)) { setJobs([]); needLogin(); } }
    } finally { if (request === requestId.current) setBusy(false); }
  }
  useDidHide(() => { requestId.current += 1; });
  useDidShow(() => { const pending = takeHistoryFilter(); const f = pending ? { ...CLEAN, ...pending } : current.current; current.current = f; setFilters(f); void load(f); });
  function update(patch: Partial<Filters>) { const f = { ...current.current, ...patch }; current.current = f; setFilters(f); setLoaded(false); setJobs([]); void load(f); }
  const options = [{ id: '', alias: '全部账号', description: '全部账号' }, ...accounts.filter(a => !filters.channel || a.channel === filters.channel).map(a => ({ ...a, description: PROGRAMS[a.channel] + ' · ' + a.alias }))];
  const activeCount = Object.values(filters).filter(Boolean).length;
  const programIndex = ['', 'app', 'mini'].indexOf(filters.channel);
  return <View className='screen'><Heading title='运行记录' subtitle='每次执行和通知，都有迹可查' action='刷新' onAction={() => void load()} />
    <Card className='history-filters'>
      <View className='history-filter-heading'>
        <View className='history-filter-heading-label'><Text className='history-filter-title'>筛选条件</Text>{activeCount > 0 && <Text className='history-filter-count'>已选 {activeCount} 项</Text>}</View>
        <Button className='history-filter-reset' disabled={busy || !activeCount} onClick={() => update(CLEAN)}>重置</Button>
      </View>
      <View className={'history-filter-grid' + (busy ? ' is-loading' : '')}>
        <View className='history-filter-row'>
          <View className='history-filter-cell'>
            <Text className='history-filter-label'>程序</Text>
            <Picker className='history-filter-picker' disabled={busy} range={['全部程序', '龙湖 App', '龙湖小程序']} value={programIndex} onChange={e => update({ channel: ['', 'app', 'mini'][Number(e.detail.value)], account_id: '' })}>
              <View className={'history-filter-control' + (filters.channel ? ' is-selected' : '')}><Text className='history-filter-value'>{['全部程序', '龙湖 App', '龙湖小程序'][programIndex]}</Text><View className='history-filter-chevron' /></View>
            </Picker>
          </View>
          <View className='history-filter-cell'>
            <Text className='history-filter-label'>账号</Text>
            <Picker className='history-filter-picker' disabled={busy} range={options.map(a => a.description)} value={Math.max(0, options.findIndex(a => a.id === filters.account_id))} onChange={e => update({ account_id: options[Number(e.detail.value)].id })}>
              <View className={'history-filter-control' + (filters.account_id ? ' is-selected' : '')}><Text className='history-filter-value'>{options.find(a => a.id === filters.account_id)?.alias || (filters.account_id ? '已移除账号' : '全部账号')}</Text><View className='history-filter-chevron' /></View>
            </Picker>
          </View>
        </View>
        <View className='history-filter-row'>
          <View className='history-filter-cell'>
            <Text className='history-filter-label'>日期</Text>
            <Picker className='history-filter-picker' disabled={busy} mode='date' value={filters.day} onChange={e => update({ day: e.detail.value })}>
              <View className={'history-filter-control' + (filters.day ? ' is-selected' : '')}><Text className='history-filter-value'>{filters.day || '全部日期'}</Text><View className='history-filter-chevron' /></View>
            </Picker>
          </View>
          <View className='history-filter-cell'>
            <Text className='history-filter-label'>结果</Text>
            <Picker className='history-filter-picker' disabled={busy} range={STATE_OPTIONS.map(o => o[1])} value={STATE_OPTIONS.findIndex(o => o[0] === filters.state)} onChange={e => update({ state: STATE_OPTIONS[Number(e.detail.value)][0] })}>
              <View className={'history-filter-control' + (filters.state ? ' is-selected' : '')}><Text className='history-filter-value'>{STATE_OPTIONS.find(o => o[0] === filters.state)?.[1]}</Text><View className='history-filter-chevron' /></View>
            </Picker>
          </View>
        </View>
      </View>
    </Card>
    <ErrorBox message={error} />{busy && <Text className='muted'>正在加载记录…</Text>}{jobs.map(j => <Record key={j.id} job={j} />)}{loaded && !jobs.length && <Empty title='暂无匹配的记录' text='执行后会生成记录，也可以调整筛选条件。' action='' />}{cursor && <Btn secondary busy={busy} onClick={() => void load(current.current, true)}>加载更多</Btn>}
  </View>;
}
