import Taro from '@tarojs/taro';
import type { Session, State } from './types';

export const BASE_URL = __CHECKIN_API_BASE_URL__.replace(/\/+$/, '');
const STORAGE = 'daily-checkin-session-v1:' + BASE_URL;
type Jar = { cookie: string; csrf: string };
let jar: Jar = Taro.getStorageSync(STORAGE) || { cookie: '', csrf: '' };
let bootstrap: Promise<Session> | undefined;
let redirecting = false;

function guardServer() {
  let env = 'develop';
  try { env = Taro.getAccountInfoSync().miniProgram.envVersion; } catch { /* DevTools */ }
  if (env !== 'develop' && !BASE_URL.startsWith('https://')) throw new Error('正式版需配置签到服务的 HTTPS 域名，请联系管理员。');
}
function saveCookies(cookies?: string[], header?: Record<string, unknown>) {
  const raw = header?.['Set-Cookie'] || header?.['set-cookie'];
  const values = cookies?.length ? cookies : typeof raw === 'string' ? raw.split(/,(?=\s*[^;,=]+=[^;,]*)/) : [];
  const pairs = new Map(jar.cookie.split('; ').filter(Boolean).map(p => [p.slice(0, p.indexOf('=')), p]));
  values.forEach(v => { const pair = v.split(';')[0].trim(); if (pair.includes('=')) pairs.set(pair.slice(0, pair.indexOf('=')), pair); });
  jar.cookie = [...pairs.values()].join('; ');
  Taro.setStorageSync(STORAGE, jar);
}
function response<T>(status: number, value: unknown): T {
  let parsed = value;
  if (typeof value === 'string') { try { parsed = JSON.parse(value); } catch { throw new Error('服务返回格式异常，请确认签到服务地址和版本。'); } }
  const body = parsed as { error?: string; csrf?: string };
  if (body?.csrf) { jar.csrf = body.csrf; Taro.setStorageSync(STORAGE, jar); }
  if (status < 200 || status >= 300) {
    if (status === 401 || status === 403) { jar.csrf = ''; Taro.setStorageSync(STORAGE, jar); }
    const error = new Error(status === 404 ? '签到服务版本需要更新，请先升级后端。' : body?.error || '请求失败，请稍后重试。');
    Object.assign(error, { status }); throw error;
  }
  return parsed as T;
}
async function raw<T>(path: string, payload?: object): Promise<T> {
  guardServer();
  let r: Taro.request.SuccessCallbackResult;
  try {
    r = await Taro.request({ url: BASE_URL + path, method: payload ? 'POST' : 'GET', data: payload,
      header: { 'Content-Type': 'application/json', Cookie: jar.cookie, 'X-CSRF-Token': jar.csrf }, timeout: 30000 });
  } catch { throw new Error('无法连接签到服务，请检查网络、服务是否启动及小程序合法域名配置。'); }
  saveCookies(r.cookies, r.header);
  return response<T>(r.statusCode, r.data);
}
export function session(): Promise<Session> {
  if (!bootstrap) bootstrap = raw<Session>('/api/session').finally(() => { bootstrap = undefined; });
  return bootstrap;
}
export async function api<T = { message: string }>(path: string, payload?: object): Promise<T> {
  if (!jar.csrf) await session();
  // Mutations are never retried automatically (SMS and check-in may be consumed).
  return raw<T>(path, payload);
}
export function login(username: string, password: string, register = false) {
  return api<Session>('/api/session/' + (register ? 'register' : 'login'), { username, password, ...(register ? { confirm_password: password } : {}) });
}
export type WechatLoginResult = Session & { bind_required: boolean; ticket?: string; expires_in?: number };
export async function wechatLogin() {
  const result = await Taro.login();
  if (!result.code) throw new Error('微信登录未完成，请重试。');
  return api<WechatLoginResult>('/api/session/wechat', { code: result.code });
}
export function bindWechatLogin(ticket: string, username: string, password: string) {
  return api<Session>('/api/session/wechat/bind', { ticket, username, password });
}
export type WechatBinding = { enabled: boolean; bound: boolean; bound_at: string };
export async function bindCurrentWechat(password: string) {
  const result = await Taro.login();
  if (!result.code) throw new Error('微信身份校验未完成，请重试。');
  return api<WechatBinding>('/api/wechat/binding', { code: result.code, password });
}
export function state(accountId?: string) { return api<State>('/api/state' + (accountId ? '?account_id=' + encodeURIComponent(accountId) : '')); }
export async function upload(accountId: string, channel: string, filePath: string) {
  if (!jar.csrf) await session();
  guardServer();
  let r: Taro.uploadFile.SuccessCallbackResult;
  try {
    r = await Taro.uploadFile({ url: BASE_URL + '/api/import?account_id=' + encodeURIComponent(accountId), filePath,
      name: 'file', formData: { channel }, header: { Cookie: jar.cookie, 'X-CSRF-Token': jar.csrf }, timeout: 60000 });
  } catch { throw new Error('文件上传结果未确认，请先刷新账号状态，再决定是否重新导入。'); }
  saveCookies(undefined, (r as unknown as { header?: Record<string, unknown> }).header);
  return response<{ message: string }>(r.statusCode, r.data);
}
export function needLogin() {
  if (redirecting) return;
  redirecting = true;
  Taro.reLaunch({ url: '/pages/login/index', complete: () => { redirecting = false; } });
}
export function errorText(e: unknown) { return e instanceof Error ? e.message : '操作失败，请稍后重试。'; }
export function unauthorized(e: unknown) { return (e as { status?: number })?.status === 401; }
