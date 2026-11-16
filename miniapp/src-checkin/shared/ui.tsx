import { PropsWithChildren, useEffect, useRef, useState } from 'react';
import { Button, Image, Text, View } from '@tarojs/components';
import Taro, { useDidHide, useDidShow } from '@tarojs/taro';
import { api, errorText, needLogin, state, unauthorized } from './api';
import { Account, accountStatus, Job, KINDS, label, PROGRAMS, State, timeLabel } from './types';

let historyFilter: Record<string, string> | undefined;
export function takeHistoryFilter() { const value = historyFilter; historyFilter = undefined; return value; }
export function go(page: string, params: Record<string, string> = {}) {
  if (page === 'history') { historyFilter = params; tab('history'); return; }
  const query = Object.entries(params).map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
  Taro.navigateTo({ url: '/pages/' + page + '/index' + (query ? '?' + query : '') });
}
export function tab(page: string) { Taro.switchTab({ url: '/pages/' + page + '/index' }); }
export function usePageState(accountId?: string) {
  const [value, setValue] = useState<State>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const active = useRef(false);
  const inflight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  async function reload() {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const next = await state(accountId);
      if (active.current) { setValue(next); setError(''); }
      if (next.running && active.current) timer.current = setTimeout(() => void reload(), 2500);
    } catch (e) {
      if (active.current) { setError(errorText(e)); if (unauthorized(e)) { setValue(undefined); needLogin(); } }
    } finally { inflight.current = false; if (active.current) setLoading(false); }
  }
  useDidShow(() => { active.current = true; void reload(); });
  useDidHide(() => { active.current = false; clearTimeout(timer.current); });
  useEffect(() => () => { active.current = false; clearTimeout(timer.current); }, []);
  return { value, error, loading, reload };
}
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  async function run<T>(action: () => Promise<T>, success?: (value: T) => void | Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { const result = await action(); await success?.(result); return result; }
    catch (e) { setError(errorText(e)); if (unauthorized(e)) needLogin(); }
    finally { lock.current = false; setBusy(false); }
  }
  return { busy, error, run, clear: () => setError('') };
}
export function Btn({ children, onClick, busy = false, disabled = false, secondary = false, danger = false }: PropsWithChildren<{ onClick: () => void; busy?: boolean; disabled?: boolean; secondary?: boolean; danger?: boolean }>) {
  return <Button className={'btn ' + (secondary ? 'secondary ' : '') + (danger ? 'danger' : '')} loading={busy} disabled={busy || disabled} onClick={onClick}>{children}</Button>;
}
export function Card({ children, className = '' }: PropsWithChildren<{ className?: string }>) { return <View className={'card ' + className}>{children}</View>; }
export function Heading({ title, subtitle, action, onAction }: { title: string; subtitle?: string; action?: string; onAction?: () => void }) {
  return <View className='heading'><View><Text className='eyebrow'>DAILY CHECK-IN</Text><Text className='page-title'>{title}</Text>{subtitle && <Text className='muted'>{subtitle}</Text>}</View>{action && <Button className='text-button' onClick={onAction}>{action}</Button>}</View>;
}
export function Section({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) { return <View className='section-heading'><Text className='section-title'>{title}</Text>{action && <Button className='text-button' onClick={onAction}>{action} ›</Button>}</View>; }
export function ErrorBox({ message }: { message?: string }) { return message ? <View className='notice error' role='alert'><Text>{message}</Text></View> : null; }
export function Note({ children }: PropsWithChildren) { return <View className='notice'><Text>{children}</Text></View>; }
export function Empty({ title = '还没有签到账号', text = '添加你的第一个账号，开始安排每天的签到。', action = '添加账号', onAction = () => go('bind') }: { title?: string; text?: string; action?: string; onAction?: () => void }) {
  return <Card className='empty'><Image src='/assets/empty.png' className='empty-icon' /><Text className='section-title'>{title}</Text><Text className='muted'>{text}</Text>{action && <Btn secondary onClick={onAction}>{action}</Btn>}</Card>;
}
export function Load({ loading }: { loading: boolean }) { return loading ? <Card className='loading'><Text>正在同步签到状态…</Text><View className='skeleton' /><View className='skeleton short' /></Card> : null; }
export function Status({ state: value }: { state: string }) { return <Text className={'badge badge-' + value}>{label(value)}</Text>; }
export function ProgramIcon({ channel }: { channel: string }) { return <View className={'program-icon ' + channel}><Image src={'/assets/' + (channel === 'app' ? 'app' : 'mini') + '.png'} /></View>; }
export function AccountCard({ account: a, today, running, reload }: { account: Account; today: string; running?: boolean; reload: () => Promise<void> }) {
  const action = useAction();
  const status = accountStatus(a, today);
  return <Card><View className='account-head' onClick={() => go('account', { id: a.id })}><ProgramIcon channel={a.channel} /><View className='grow'><Text className='account-name'>{a.alias}</Text><Text className='muted small'>{PROGRAMS[a.channel]}{a.phone_hint ? ' · ' + a.phone_hint : ''}</Text></View><Status state={status} /></View>
    <View className='account-meta'><Text>{a.configured ? '已连接' : '待导入登录文件'} · {a.enabled ? '自动执行中' : '自动执行已暂停'}</Text><Text>签到 {a.sign_time} / 复核 {a.check_time}</Text></View>
    <ErrorBox message={action.error} /><View className='row account-actions'><Button className='text-button grow left' onClick={() => go('account', { id: a.id })}>管理账号 ›</Button>
      <Btn secondary busy={action.busy} disabled={running || !a.configured} onClick={() => void action.run(() => api('/api/accounts/' + a.id, { enabled: !a.enabled }), reload)}>{a.enabled ? '暂停' : '开启'}</Btn></View></Card>;
}
export function Record({ job, expanded = false, detailLink = true }: { job: Job; expanded?: boolean; detailLink?: boolean }) {
  const [open, setOpen] = useState(expanded);
  return <Card className='record'><View className='row spread' onClick={() => setOpen(!open)}><Text className='record-title'>{KINDS[job.kind] || job.kind}</Text><Text className='muted small'>{job.created.slice(5, 16).replace('T', ' ')} {open ? '⌃' : '⌄'}</Text></View>
    {job.results.map((r, i) => <View key={i} className='result'><View className='row spread'><Text className='grow'>{r.channel ? PROGRAMS[r.channel] : '签到'} · {r.account_alias || '主号'}</Text><Status state={r.state} /></View>{open && <View><Text className='result-message'>{r.message}</Text>{r.rewards?.map((reward, n) => <Text key={n} className='muted'>{reward}</Text>)}<Button className='text-button left' onClick={() => r.account_id && go('account', { id: r.account_id })}>查看此账号 ›</Button></View>}</View>)}
    {job.status !== 'done' && <Text className='muted'>{job.status === 'running' ? '执行中…' : '等待执行或恢复'}</Text>}
    {open && <View className='record-foot'>{detailLink && <Button className='text-button left' onClick={() => go('result', { job_id: String(job.id) })}>查看结果 / 订阅下一次通知 ›</Button>}<Text>小程序提醒：{({ sent: '已发送', pending: '待发送', sending: '发送中', failed: '发送失败', uncertain: '发送结果未确认，不自动重发', cancelled: '已取消' } as Record<string, string>)[job.mini_push_state || ''] || '本轮无订阅推送'}</Text>{job.mini_push_error && <Text className='error-text'>{job.mini_push_error}</Text>}<Text>执行时间：{timeLabel(job.created)}</Text><Text>邮箱：{({ sent: '发送成功', pending: '待发送 / 重试中', failed: '发送失败' } as Record<string, string>)[job.mail_state || ''] || '本轮无邮件通知'}</Text>{job.mail_error && <Text className='error-text'>{job.mail_error}</Text>}</View>}</Card>;
}
