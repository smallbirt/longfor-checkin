import type { PushState } from './push';

export type Channel = 'app' | 'mini';
export type Result = { state: string; message: string; channel?: Channel; account_id?: string; account_alias?: string; day?: string; checked_at?: string; rewards?: string[] };
export type Account = { id: string; channel: Channel; alias: string; enabled: boolean; sign_time: string; check_time: string; configured: boolean; phone_hint?: string; status?: Result; blocked?: boolean };
export type Job = { id: number; kind: string; created: string; status: string; results: Result[]; mail_state?: string; mail_error?: string; mini_push_state?: string; mini_push_error?: string };
export type Events = { success: boolean; failure: boolean; afternoon: boolean };
export type Session = { csrf: string; user: { username: string; is_admin: boolean } | null; registration_enabled: boolean; preview: boolean; wechat_login_enabled?: boolean };
export type State = {
  local_debug?: boolean;
  user: { username: string; is_admin: boolean }; accounts: Account[]; jobs: Job[]; today: string;
  running: boolean; worker_error?: string; heartbeat?: string; sender_configured: boolean;
  mail: { recipient: string; verified: boolean }; mail_configured: boolean;
  notification_events: Events;
  notification_configured: boolean;
  mini_push: PushState;
  login_methods: { app: { effective_sms_available: boolean; retry_after: number; pending: boolean; pending_phone?: string } };
};
export const PROGRAMS = { app: '龙湖 App', mini: '龙湖小程序' };
export const STATES: Record<string, string> = { signed: '已签到', unsigned: '未签到', unknown: '结果未确认', auth_expired: '登录失效', captcha_required: '需要验证', credentials_missing: '未连接', inactive: '已暂停', request_error: '执行失败' };
export const KINDS: Record<string, string> = { manual: '手动签到', check: '状态复核', morning: '定时签到', afternoon: '下午复核' };
export function todayResult(a: Account, day: string) { return a.status?.day === day ? a.status : undefined; }
export function accountStatus(a: Account, day: string) {
  if (!a.configured) return 'credentials_missing';
  if (a.blocked || a.status?.state === 'auth_expired') return 'auth_expired';
  const status = todayResult(a, day);
  if (status) return status.state;
  return a.enabled ? 'pending' : 'inactive';
}
export function label(state: string) { return STATES[state] || (state === 'pending' ? '待执行' : '待确认'); }
export function timeLabel(value?: string) { return value ? value.replace('T', ' ').slice(0, 19) : '暂无'; }
