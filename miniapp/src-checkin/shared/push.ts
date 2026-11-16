import Taro from '@tarojs/taro';
import { api } from './api';

export type PushState = { scope: 'user'; scenario: 'failure'; configured: boolean; bound: boolean; enabled: boolean; subscriptions: { account_id: string; remaining: number }[] };
export type PushIntent = PushState & { nonce: string; template_id: string; account_id: string; appid: string; expires_at: number };

export async function prepareSubscription() {
  const login = await Taro.login();
  if (!login.code) throw new Error('微信身份校验未完成，请重新准备订阅。');
  return api<PushIntent>('/api/mini-push/prepare', { code: login.code });
}

// Call directly from the tap handler, before awaiting any network or login call.
export function requestSubscription(intent: PushIntent) {
  // Taro 4.1's cross-platform type erroneously requires Alipay's entityIds too.
  const request = Taro.requestSubscribeMessage as unknown as (options: { tmplIds: string[] }) => Promise<Record<string, unknown>>;
  return new Promise<string>((resolve, reject) => {
    request({ tmplIds: [intent.template_id] }).then(result => {
      if (result[intent.template_id] !== 'accept') {
        reject(new Error('此次未允许订阅，未增加通知次数。可在小程序设置中调整接收权限。'));
      } else resolve(intent.nonce);
    }, reject);
  });
}

export function saveSubscription(nonce: string) {
  return api<PushState & { message: string }>('/api/mini-push/subscribe', { nonce, accepted: true });
}
