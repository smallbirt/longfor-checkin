import { useState } from 'react';
import { View } from '@tarojs/components';
import Taro, { useDidShow, useRouter } from '@tarojs/taro';
import { api, errorText, needLogin, unauthorized } from '../../shared/api';
import { Job, State } from '../../shared/types';
import { SubscribeCard } from '../../shared/SubscribeCard';
import { Btn, ErrorBox, Heading, Load, Record, tab } from '../../shared/ui';

export default function ResultPage() {
  const params = useRouter().params;
  const id = params.job_id || '';
  const receipt = params.receipt || '';
  const requestedAccount = params.account_id || '';
  const [job, setJob] = useState<Job>(); const [state, setState] = useState<State>();
  const [error, setError] = useState(''); const [busy, setBusy] = useState(true);
  async function load() {
    setBusy(true); setError('');
    try {
      if (!/^[1-9][0-9]*$/.test(id)) throw new Error('通知中的运行记录无效。');
      const [current, result] = await Promise.all([api<State>('/api/state'), api<{ job: Job }>('/api/jobs/' + id + (receipt ? '?receipt=' + encodeURIComponent(receipt) : ''))]);
      setState(current); setJob(result.job);
    } catch (e) { setError(errorText(e)); if (unauthorized(e)) { if (/^[1-9][0-9]*$/.test(id)) Taro.setStorageSync('checkin-return-result', { id, receipt, accountId: requestedAccount }); needLogin(); } }
    finally { setBusy(false); }
  }
  useDidShow(() => { void load(); });
  return <View className='screen'><Heading title='本次运行结果' subtitle='看完结果，顺手订阅下一次通知' /><ErrorBox message={error} /><Load loading={busy} />
    {job && state && <><Record job={job} expanded detailLink={false} /><SubscribeCard username={state.user.username} /></>}
    {error && <Btn secondary busy={busy} onClick={() => void load()}>重新读取结果</Btn>}<Btn secondary onClick={() => tab('home')}>返回首页</Btn>
  </View>;
}
