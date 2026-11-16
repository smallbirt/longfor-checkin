import { useEffect, useRef, useState } from 'react';
import { Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import { api, BASE_URL, errorText, needLogin, unauthorized } from './api';
import { PushIntent, PushState, prepareSubscription, requestSubscription, saveSubscription } from './push';
import { Btn, Card, ErrorBox } from './ui';

export function SubscribeCard({ username, onSaved }: { username: string; onSaved?: () => void }) {
  const [state, setState] = useState<PushState>();
  const [intent, setIntent] = useState<PushIntent>();
  const [pending, setPending] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const lock = useRef(false);
  const generation = useRef(0);
  const prefix = 'checkin-push-ack:' + BASE_URL + ':' + username + ':';
  const storage = prefix + 'all-accounts';
  const pendingStorage = useRef(storage);
  const remaining = state?.subscriptions.reduce((total, s) => total + s.remaining, 0) || 0;

  async function prepare(ticket = generation.current) {
    setBusy(true); setError(''); setIntent(undefined);
    try {
      const current = await api<PushState>('/api/mini-push');
      if (ticket !== generation.current) return;
      setState(current);
      // Preserve an accepted consent whose acknowledgement failed in the old
      // account-specific UI. Retrying it must not open another consent dialog.
      const savedKey = [storage, ...Taro.getStorageInfoSync().keys.filter(k => k.startsWith(prefix) && k !== storage)].find(k => Taro.getStorageSync(k));
      pendingStorage.current = savedKey || storage;
      const saved = savedKey ? Taro.getStorageSync(savedKey) as string : '';
      if (saved) { setPending(saved); return; }
      if (!current.configured) return;
      if (current.subscriptions.some(s => s.remaining > 0)) return;
      const next = await prepareSubscription();
      if (ticket === generation.current) { setIntent(next); setState(next); }
    } catch (e) {
      if (ticket === generation.current) { setError(errorText(e)); if (unauthorized(e)) needLogin(); }
    } finally { if (ticket === generation.current) setBusy(false); }
  }

  useEffect(() => {
    const ticket = ++generation.current;
    setPending(''); setMessage(''); void prepare(ticket);
    return () => { generation.current += 1; };
  }, [username]);

  async function acknowledge(nonce: string) {
    const result = await saveSubscription(nonce);
    Taro.removeStorageSync(pendingStorage.current); setPending(''); setState(result); setIntent(undefined);
    setMessage(result.message); onSaved?.();
  }

  function subscribe() {
    if (lock.current || !intent || intent.scope !== 'user' || busy) return;
    if (Date.now() >= intent.expires_at * 1000) {
      setMessage('订阅准备已过期，正在更新。完成后请再点击订阅。'); void prepare(); return;
    }
    lock.current = true;
    // Keep this before any await: WeChat requires the actual user tap.
    const request = requestSubscription(intent);
    setBusy(true); setError(''); setMessage('');
    void (async () => {
      let accepted = '';
      try {
        accepted = await request;
        pendingStorage.current = storage; Taro.setStorageSync(storage, accepted); setPending(accepted);
        await acknowledge(accepted);
      } catch (e) {
        setError(accepted ? '微信已允许，但保存未完成。请点击“重试保存订阅”，不会再次申请订阅。' : errorText(e));
        if (unauthorized(e)) needLogin();
      } finally { lock.current = false; setBusy(false); }
    })();
  }

  async function retry() {
    if (lock.current || !pending) return;
    lock.current = true; setBusy(true); setError('');
    try { await acknowledge(pending); }
    catch (e) { setError(errorText(e)); if (unauthorized(e)) needLogin(); }
    finally { lock.current = false; setBusy(false); }
  }

  return <Card><Text className='section-title'>复核失败提醒</Text>
    <Text className='muted'>覆盖你名下全部签到账号，无需选择。任一账号定时复核失败或登录失效时发送一条提醒，同一次任务不重复发送。全部成功不消耗订阅次数。</Text>
    <ErrorBox message={error} />{message && <Text className='result-message'>{message}</Text>}
    {state && !state.configured ? <Text className='muted'>管理员尚未配置订阅模板及推送密钥，当前可使用邮箱提醒。</Text>
      : pending ? <><Btn busy={busy} onClick={() => void retry()}>重试保存订阅</Btn><Btn secondary disabled={busy} onClick={() => { Taro.removeStorageSync(pendingStorage.current); setPending(''); void prepare(); }}>放弃此次保存，重新准备</Btn></>
      : remaining > 0 ? <Text className='badge badge-signed'>已订阅全部账号 · 剩余 {remaining} 次提醒</Text>
      : intent ? <Btn busy={busy} onClick={subscribe}>订阅下一次失败提醒</Btn>
      : <Btn secondary busy={busy} onClick={() => void prepare()}>重新准备订阅</Btn>}
    <Text className='muted small'>请开启自动计划和“失败和登录失效”通知。微信一次订阅可发送一条提醒，收到后可再次订阅；早间签到不消耗本次订阅，未订阅也会正常签到。</Text>
  </Card>;
}
