import { useState } from 'react';
import { Text, Video, View } from '@tarojs/components';
import { BASE_URL } from '../../shared/api';
import { Btn, Card, ErrorBox, go, Heading, Note } from '../../shared/ui';

export default function Tutorial() {
  const [error, setError] = useState('');
  const [key, setKey] = useState(0);
  return <View className='screen'><Heading title='视频教程' subtitle='沿用平台现有的操作视频' /><Card><Video key={key} id='checkin-tutorial' className='tutorial-video' src={BASE_URL + '/static/tutorial/checkin-tutorial.mp4'} poster={BASE_URL + '/static/tutorial/poster.jpg'} controls showFullscreenBtn enableProgressGesture onError={() => setError('视频暂时无法播放，请检查服务地址及媒体域名配置，或先阅读操作指南。')} /><ErrorBox message={error} />{error && <Btn secondary onClick={() => { setError(''); setKey(key + 1); }}>重试播放</Btn>}<Text className='muted small'>可全屏观看；视频内容以原教程为准，当前页面布局可能有所变化。</Text></Card><Note>原视频保留在签到服务中，不会打包进小程序安装包。</Note><Btn secondary onClick={() => go('help')}>查看文字操作指南</Btn></View>;
}
