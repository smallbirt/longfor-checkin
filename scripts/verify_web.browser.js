async (page) => {
  const ctx=await page.context().browser().newContext({viewport:{width:1440,height:1000}});
  const p=await ctx.newPage(),checks=[],errors=[],overflow=[],requests=[];
  const assert=(ok,label)=>{if(!ok)throw Error(label);checks.push(label);};
  const visit=async path=>{await p.goto('http://127.0.0.1:18091'+path);await p.waitForSelector('main');};
  const state=()=>p.evaluate(()=>fetch('/api/state').then(r=>r.json()));
  const capture={log:{entries:[{startedDateTime:'2026-10-23T09:10:00Z',request:{method:'POST',url:'https://gw2c-hw-open.longfor.com/lmarketing-task-api-mvc-prod/openapi/task/v1/signature/clock',headers:[{name:'token',value:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},{name:'x-lf-usertoken',value:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},{name:'x-lf-channel',value:'L0'},{name:'x-lf-bu-code',value:'L00602'},{name:'x-lf-dxrisk-token',value:'fake-device-for-ui-test'},{name:'x-lf-dxrisk-source',value:'2'},{name:'x-gaia-api-key',value:'application-key'}],postData:{text:JSON.stringify({activity_no:'11111111111736501868255956070000'})}}}]}};
  const fixtureToken=Date.now().toString(16).padStart(32,'a');for(const h of capture.log.entries[0].request.headers)if(['token','x-lf-usertoken'].includes(h.name))h.value=fixtureToken;
  p.on('pageerror',e=>errors.push(e.message));
  p.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:18091/')&&!r.url().startsWith('data:'))requests.push(r.url());});
  try{
    await visit('/login');await p.getByLabel('用户名',{exact:true}).fill('demo');await p.getByLabel('密码',{exact:true}).fill('demo-password-2026');await p.getByRole('button',{name:'登录',exact:true}).click();await p.waitForFunction(()=>document.getElementById('signed-count')?.textContent!=='—');
    assert(await p.locator('.sidebar').isVisible(),'桌面侧边导航显示');
    const before=await state();
    assert(before.accounts.length>=5,'真实 API 返回多账号实例');
    assert(await p.locator('#jobs .activity-row').count()===5,'首页最近记录为五条');
    await visit('/accounts');await p.waitForSelector('.program-account');
    assert(await p.locator('.program-account').count()===before.accounts.length,'账号列表由真实 API 渲染');
    await p.locator('[data-program=mini]').click();assert(await p.locator('.program-account').count()===before.accounts.filter(a=>a.channel==='mini').length,'小程序筛选正常');
    await p.locator('[data-program=all]').click();await p.locator('#account-search').fill('家人');assert(await p.locator('.program-account').count()===before.accounts.filter(a=>a.alias.includes('家人')).length,'中文昵称搜索正常');await p.locator('#account-search').fill('');
    await p.getByRole('link',{name:'添加账号',exact:true}).click();
    const alias='Web 测试 <App> '+Date.now().toString().slice(-4);
    await p.getByLabel('账号昵称',{exact:true}).fill(alias);await p.getByRole('button',{name:'下一步：导入登录',exact:true}).click();await p.locator('#new-import-form').waitFor({state:'visible'});
    let d=await state();const added=d.accounts.find(a=>a.alias===alias);
    assert(!!added&&d.accounts.length===before.accounts.length+1,'添加第二个同类 App 不覆盖旧账号');
    assert(!added.enabled&&!added.configured,'新建实例默认未导入、未开启计划');
    await p.locator('#new-har-file').setInputFiles({name:'demo-app.har',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(capture))});await p.getByRole('button',{name:'导入并校验',exact:true}).click();await p.locator('#new-plan-form').waitFor({state:'visible'});
    assert(!await p.locator('#new-enabled').isChecked(),'导入后仍需主动确认开启');
    await p.locator('#new-sign-time').fill('10:20');await p.locator('#new-check-time').fill('17:30');await p.locator('#new-enabled').check();await p.getByRole('button',{name:'完成添加',exact:true}).click();await p.waitForURL('**/accounts/'+added.id);await p.waitForSelector('.account-result');
    d=await state();const configured=d.accounts.find(a=>a.id===added.id);
    assert(configured.configured&&configured.enabled&&configured.sign_time==='10:20','导入、校验、保存独立计划全流程正常');
    assert(await p.locator('.intro h1').innerText()===alias,'昵称按文本显示，未作为 HTML 注入');
    await p.getByRole('button',{name:'修改配置',exact:true}).click();assert(await p.locator('#plan-alias').inputValue()===alias,'修改配置预填原昵称');assert(await p.locator('#plan-sign').inputValue()==='10:20','修改配置预填原时间');
    await p.locator('#plan-check').fill('08:00');await p.getByRole('button',{name:'保存修改',exact:true}).click();await p.locator('#plan-error').waitFor({state:'visible'});assert((await p.locator('#plan-error').innerText()).includes('晚于'),'错误的复核时间显示具体错误');
    await p.locator('#plan-check').fill('18:00');await p.getByRole('button',{name:'保存修改',exact:true}).click();await p.locator('#plan-form').waitFor({state:'hidden'});
    await p.getByRole('button',{name:'立即签到',exact:true}).click();await p.waitForFunction(id=>fetch('/api/state').then(r=>r.json()).then(s=>s.accounts.find(a=>a.id===id)?.status?.state==='signed'),added.id);await p.reload();await p.getByRole('link',{name:'查看今日结果',exact:true}).waitFor();
    assert(await p.getByRole('button',{name:'立即签到',exact:true}).count()===0,'确认签到后主操作变为查看结果');
    await p.getByRole('link',{name:'查看今日结果',exact:true}).click();await p.waitForSelector('.activity-row');assert(await p.locator('.activity-row').count()===1,'账号、日期、成功状态联合筛选生效');
    await visit('/credentials?account_id='+added.id+'#app');await p.waitForFunction(()=>document.getElementById('app-connection-badge')?.textContent!=='正在读取');assert((await p.locator('.selected-instance-banner').innerText()).includes(alias),'登录页面明确当前账号');assert(await p.locator('#login-tab-mini').isHidden(),'App 账号不显示错误渠道入口');
    const mini=before.accounts.find(a=>a.channel==='mini');await visit('/credentials?account_id='+mini.id+'#mini');await p.waitForFunction(()=>document.getElementById('mini-connection-badge')?.textContent!=='正在读取');assert(await p.locator('#login-panel-mini').isVisible(),'小程序登录面板正确显示');assert(await p.locator('#login-panel-app').isHidden(),'小程序不显示 App 短信入口');
    await visit('/settings');await p.waitForFunction(()=>document.getElementById('recipient-badge')?.textContent!=='正在读取');assert(await p.locator('#recipient-editor').isHidden(),'已配置邮箱默认展示状态而非完整表单');await p.getByRole('button',{name:'修改收件邮箱',exact:true}).click();assert(await p.locator('#recipient').inputValue()===before.mail.recipient,'修改邮箱预填原地址');await p.getByRole('button',{name:'取消修改',exact:true}).click();
    await p.locator('#notify-success').uncheck();await p.getByRole('button',{name:'保存提醒偏好',exact:true}).click();await p.waitForFunction(()=>fetch('/api/state').then(r=>r.json()).then(s=>s.notification_events.success===false));await p.locator('#notify-success').check();await p.getByRole('button',{name:'保存提醒偏好',exact:true}).click();
    assert((await state()).accounts.filter(a=>a.id!==added.id).length===before.accounts.length,'其他账号仍完整保留');
    const routes=['/','/accounts','/accounts/new','/accounts/'+added.id,'/credentials?account_id='+added.id+'#app','/credentials?account_id='+mini.id+'#mini','/settings','/history','/help','/video'];
    for(const width of [320,375,390,430,768,1024,1440]){await p.setViewportSize({width,height:900});for(const r of routes){await visit(r);const bad=await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth);if(bad)overflow.push({width,route:r,dimensions:await p.evaluate(()=>({scroll:document.documentElement.scrollWidth,viewport:innerWidth}))});}}
    assert(!overflow.length,'所有主页面在七种宽度下无横向溢出：'+JSON.stringify(overflow));
    await p.setViewportSize({width:390,height:844});await visit('/');await p.waitForFunction(()=>document.getElementById('signed-count')?.textContent!=='—');assert(await p.locator('.mobile-tabs').isVisible(),'手机显示四个底部导航入口');await p.screenshot({path:'/tmp/longfor-web-home-mobile.png',fullPage:true});
    await visit('/accounts');await p.waitForSelector('.program-account');await p.screenshot({path:'/tmp/longfor-web-accounts-mobile.png',fullPage:true});
    await p.setViewportSize({width:1440,height:1000});await visit('/accounts');await p.waitForSelector('.program-account');await p.screenshot({path:'/tmp/longfor-web-accounts-desktop.png',fullPage:true});
    await visit('/accounts/'+added.id);await p.waitForSelector('.account-result');await p.getByRole('button',{name:'移除此账号',exact:true}).click();await p.getByRole('button',{name:'确认移除',exact:true}).click();await p.waitForURL('**/accounts');
    d=await state();assert(!d.accounts.some(a=>a.id===added.id),'移除实例真实生效');await visit('/history?account_id='+added.id);await p.waitForSelector('.activity-row');assert(await p.locator('.activity-row').count()>=1,'已移除账号的历史记录仍可查看');
    assert(!errors.length,'无 JavaScript 错误：'+JSON.stringify(errors));assert(!requests.length,'页面交互不请求外部资源：'+JSON.stringify(requests));return {passed:checks.length,checks,errors,overflow};
  }finally{await ctx.close();}
}
