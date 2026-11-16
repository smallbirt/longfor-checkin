export default defineAppConfig({
  pages: ['pages/home/index', 'pages/accounts/index', 'pages/history/index', 'pages/mine/index', 'pages/login/index', 'pages/wechat/index', 'pages/account/index', 'pages/bind/index', 'pages/notifications/index', 'pages/result/index', 'pages/sms/index', 'pages/help/index', 'pages/video/index'],
  window: { navigationBarTitleText: '每日签到', navigationBarBackgroundColor: '#f4f7f8', navigationBarTextStyle: 'black', backgroundColor: '#f4f7f8' },
  tabBar: { color: '#61727b', selectedColor: '#107f78', backgroundColor: '#ffffff', borderStyle: 'white', list: [
    { pagePath: 'pages/home/index', text: '首页', iconPath: 'assets/home.png', selectedIconPath: 'assets/home-active.png' },
    { pagePath: 'pages/accounts/index', text: '我的签到', iconPath: 'assets/accounts.png', selectedIconPath: 'assets/accounts-active.png' },
    { pagePath: 'pages/history/index', text: '运行记录', iconPath: 'assets/history.png', selectedIconPath: 'assets/history-active.png' },
    { pagePath: 'pages/mine/index', text: '我的', iconPath: 'assets/mine.png', selectedIconPath: 'assets/mine-active.png' }
  ] }
});
