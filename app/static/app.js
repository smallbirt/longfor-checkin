const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const page = document.body.dataset.page;
const currentAccountId=document.body.dataset.accountId||'', currentAccountChannel=document.body.dataset.accountChannel||'';
let notificationPrefsInitialized=false;
let pushPlatformDraft=false;
let state, loaded = false, refreshBusy = false, feedbackTimer, registrationEnabled, sharedDeviceEnabled = false;
let recipientEditing=false, recipientDraftInitialized=false, senderEditing=false, senderDraftInitialized=false, adminConfig;
const editingChannels={app:false,mini:false}, busyEditors=new Set();
let cursors = [null], nextCursor = null, historyBusy = false;
let lastChannels = '', lastJobs = '', smsRetryAt = 0;
const labels = {signed:'已签到', unsigned:'未签到', unknown:'待确认', auth_expired:'登录失效', captcha_required:'需要验证', credentials_missing:'待配置', inactive:'活动未开放', request_error:'请求异常'};
const kindLabels = {morning:'09:10 签到', afternoon:'16:00 复核与补签', manual:'手动签到', check:'状态查询'};
function node(tag, text, cls) {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;}
function link(text, href, cls=''){const a=node('a',text,cls);a.href=href;return a;}
function feedback(text, bad=false){clearTimeout(feedbackTimer);const box=$('feedback');box.hidden=false;box.replaceChildren(node('span',text));box.className=bad?'error':'';const close=node('button','关闭','quiet');close.onclick=()=>box.hidden=true;box.append(close);if(!bad)feedbackTimer=setTimeout(()=>box.hidden=true,6000);}
async function api(path, data){if(currentAccountId&&/^(state(?:\?|$)|import(?:\?|$)|login\/(?:app|sms)\/)/.test(path)){path+=(path.includes('?')?'&':'?')+'account_id='+encodeURIComponent(currentAccountId);}if(currentAccountId&&path==='run')data={...data,account_id:currentAccountId};const opts={headers:{'X-CSRF-Token':csrf}};if(data!==undefined){opts.method='POST';if(data instanceof FormData)opts.body=data;else{opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(data);}}const r=await fetch('/api/'+path,opts);if(r.status===401){location.href='/login';throw Error('请先登录');}let d;try{d=await r.json();}catch{throw Error('服务暂时未响应，请稍后刷新。');}if(!r.ok)throw Error(d.error||d.message||'操作失败，请重试。');return d;}
async function action(button, task){if(button.disabled)return;button.disabled=true;const original=button.textContent;button.textContent='处理中…';try{const d=await task();if(d.message)feedback(d.message);if(d.login_required){location.href='/login';return;}await refresh();}catch(e){feedback(e.message,true);}finally{button.disabled=false;if(button.textContent==='处理中…')button.textContent=original;if(state)render();if(page==='admin')$('shared-device-disable').disabled=!sharedDeviceEnabled;}}
function inlineError(id,message=''){const box=$(id);box.textContent=message;box.hidden=!message;}
function editorAction(button,key,task){return action(button,async()=>{busyEditors.add(key);if(state)render();for(const suffix of ['cancel','edit']){const control=$(key+'-'+suffix);if(control)control.disabled=true;}try{return await task();}finally{busyEditors.delete(key);for(const suffix of ['cancel','edit']){const control=$(key+'-'+suffix);if(control)control.disabled=false;}}});}
function formatTime(t){if(!t)return '尚无';const d=new Date(t);return Number.isNaN(d.getTime())?'尚无':d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});}
function badge(s){return node('span',labels[s]||'待确认','badge '+(s==='signed'?'good':['auth_expired','request_error'].includes(s)?'bad':s==='unknown'?'':'warn'));}
function renderJobs(list){const target=$('jobs');if(!target)return;const signature=JSON.stringify(list);if(lastJobs===signature)return;lastJobs=signature;target.replaceChildren();if(!list.length)target.append(node('p','暂无记录。导入凭证后会先查询状态，不会立即签到。','muted empty-state'));
 for(const job of list){const row=node('article',undefined,'job');const info=node('div');info.append(node('strong',kindLabels[job.kind]||job.kind),node('p'));info.lastChild.append(node('time',formatTime(job.created)));const results=node('div');if(job.status==='running')results.append(node('p','正在查询 / 执行…'));else if(job.status==='interrupted')results.append(node('p','任务被中断；下次运行先查询状态。'));for(const r of job.results){const p=node('p');p.append(node('strong',(r.channel==='app'?'App':'小程序')+' · '),node('span',r.message));results.append(p);}const mail=node('div');const names={sent:'邮件已提交服务器',pending:'邮件待发送 / 重试中',failed:'邮件发送失败'};mail.append(node('p',names[job.mail_state]||'无需邮件'));if(job.mail_error)mail.append(node('p',job.mail_error,'error'));row.append(info,results,mail);target.append(row);}
}
function renderHome(){
 $('date').textContent=state.today+' / 北京时间';$('schedule-status').textContent=state.schedule_enabled?'运行中':'已暂停';$('schedule-status').className='badge '+(state.schedule_enabled?'good':'');$('toggle-schedule').textContent=state.schedule_enabled?'暂停定时':'开启定时';$('toggle-schedule').disabled=false;$('check-all').disabled=state.running||!state.channels.some(c=>c.configured);
 const hint=$('setup-hint');hint.replaceChildren();const missing=state.channels.some(c=>!c.configured);hint.hidden=!missing&&state.mail_configured;if(missing){hint.append(node('span','先连接自己的 App 和小程序账号。'),link('更新登录凭证 →','/credentials'));}if(!state.mail_configured){hint.append(node('span',state.sender_configured?' 验证收件邮箱后即可开启每日计划。':' 平台发信尚未配置，请联系管理员。'),link('通知设置 →','/settings'));}
 const signature=JSON.stringify([state.channels,state.today,state.running]);if(lastChannels!==signature){lastChannels=signature;const cards=$('channels');cards.replaceChildren();for(const c of state.channels){const card=node('article',undefined,'card channel');const top=node('div',undefined,'channel-header');top.append(node('h2',c.name));const current=c.status&&c.status.day===state.today;const s=current?c.status.state:'unknown';top.append(badge(c.configured?s:'credentials_missing'));card.append(top,node('p',current?c.status.message:c.configured?'今天尚未查询，请查询服务端状态。':'连接自己的账号，开始设置。','message'),node('p','凭证更新：'+formatTime(c.imported_at),'meta'),node('p','状态查询：'+formatTime(c.status?.checked_at),'meta'));if(c.blocked)card.append(node('p',c.blocked==='captcha_required'?'下次签到会尝试自动滑块验证；也可先在手机完成签到。':'登录失效，请重新登录后更新凭证。','hint'));const actions=node('div',undefined,'actions');for(const [kind,text] of [['check','查询状态'],['manual','尝试签到']]){const b=node('button',text,kind==='check'?'secondary':'quiet');b.disabled=!c.configured||state.running;b.onclick=()=>action(b,()=>api('run',{kind,channel:c.id}));actions.append(b);}if(!c.configured||c.blocked==='auth_expired')actions.append(link('更新凭证','/credentials','button-link secondary'));card.append(actions);cards.append(card);}}
 renderJobs(state.jobs);
}
function renderConnections(){
 const sources={personal:'个人设备',platform:'平台设备',saved:'已保存设备',missing:'待补齐'};
 for(const key of ['app','mini']){
  const c=state.channels.find(item=>item.id===key),configured=Boolean(c?.configured),busy=busyEditors.has(key);
  const current=c?.status?.day===state.today?c.status:null;
  const expired=c?.blocked==='auth_expired'||current?.state==='auth_expired';
  const usable=current&&['signed','unsigned'].includes(current.state)&&!expired;
  const badge=$(`${key}-connection-badge`);badge.textContent=!configured?'未连接':expired?'登录失效':usable?'登录可用':'已配置 · 待确认';badge.className='badge '+(!configured?'warn':expired?'bad':usable?'good':'');
  $(`${key}-connection-status`).textContent=!configured?'导入本渠道的 HAR，自动保存其中的登录与设备信息。':expired?'原配置已保留，请更新登录后再验证。':c.status?.message||'配置已保存，可以验证当前登录状态。';
  $(`${key}-updated-at`).textContent=formatTime(c?.imported_at);
  const ready=c?.device_ready??c?.has_fingerprint,source=c?.effective_device_source||c?.device_source||'missing';
  $(`${key}-device-summary`).textContent=ready?sources[source]+' · 自动使用':'当前没有可用设备；导入时会自动从 HAR 提取。';
  $(`${key}-configured-actions`).hidden=!configured;
  $(`${key}-editor`).hidden=configured&&!editingChannels[key];
  $(`${key}-first-help`).hidden=configured;
  $(`${key}-editor-title`).textContent=configured?'更新登录':'连接账号';
  $(`${key}-cancel`).hidden=!configured;$(`${key}-cancel`).disabled=busy;
  $(`${key}-edit`).disabled=busy||state.running;$(`${key}-edit`).setAttribute('aria-expanded',String(Boolean(editingChannels[key])));
  $(`${key}-check`).disabled=busy||state.running;
  const save=$(`${key}-import-save`);save.disabled=busy||state.running;if(save.textContent!=='处理中…')save.textContent=configured?'保存更新':'导入并连接';
 }
 const sms=state.login_methods.app;smsRetryAt=Date.now()+sms.retry_after*1000;
 $('app-sms-details').hidden=sms.first_import_required;
 $('sms-status').textContent=sms.effective_sms_available?'使用本人手机号更新 App，现有设备配置自动保留。':'当前短信设备不完整，请使用上方“更新登录”导入包含成功登录响应的 App HAR。';
 $('device-status').textContent='签到设备：'+sources[sms.device_sources.app]+'。';
 $('device-help').hidden=sms.effective_sms_available||sms.first_import_required;
 $('device-help').textContent='无需单独导入设备凭证。可在同一份 App HAR 中包含成功登录和签到记录后更新。';
 const pending=sms.pending&&sms.pending_target==='app';
 $('sms-pending').textContent=pending?'本次验证码发往 '+sms.pending_phone+'，请在 10 分钟内填写。':'尚未请求验证码。';
 $('sms-verify').disabled=!pending||state.running;$('sms-phone').required=!pending;
 renderSmsCountdown();
}
function renderRecipient(){
 const verified=Boolean(state.mail.verified),editing=!verified||recipientEditing;
 if(!recipientDraftInitialized){$('recipient').value=state.mail.recipient||'';recipientDraftInitialized=true;}
 $('mail-status').textContent=verified?state.mail.recipient+'。'+(state.sender_configured?'通知发送到此邮箱。':'平台发信暂未配置，请联系管理员。'):state.sender_configured?'尚未设置收件邮箱，请填写并验证。':'平台发信尚未配置，请联系管理员后设置收件邮箱。';
 $('recipient-badge').textContent=verified?'已验证':'待设置';$('recipient-badge').className='badge '+(verified?'good':'warn');
 $('recipient-configured-actions').hidden=!verified;$('recipient-editor').hidden=!editing;
 $('recipient-edit').setAttribute('aria-expanded',String(recipientEditing));$('recipient-edit').disabled=busyEditors.has('recipient');
 $('recipient-editor-title').textContent=verified?'修改收件邮箱':'设置收件邮箱';
 $('recipient-edit-help').textContent=verified?'新地址验证成功前，通知仍发送到原邮箱。':'只需填写你的收件邮箱，无需邮箱密码或授权码。';
 $('recipient-cancel').hidden=!verified;$('recipient-cancel').disabled=busyEditors.has('recipient');
 $('test-mail').hidden=!verified;$('test-mail').disabled=!state.mail_configured;
 updateRecipientControls();
}
function updateRecipientControls(){if(page!=='settings'||!state)return;const same=state.mail.verified&&$('recipient').value.trim()===state.mail.recipient;$('send-code').disabled=!state.sender_configured||Boolean(same)||busyEditors.has('recipient');}
function renderMiniPush(){
 const p=state.mini_push||{};const count=(p.subscriptions||[]).reduce((n,s)=>n+s.remaining,0);
 $('mini-push-badge').textContent=!p.configured?'待配置':p.enabled?'已开启':p.bound?'已暂停':'待订阅';
 $('mini-push-badge').className='badge '+(p.enabled?'good':'warn');
 $('mini-push-status').textContent=!p.configured?'管理员尚未配置订阅模板及推送密钥。':p.enabled?`小程序提醒已开启，待用订阅 ${count} 次。覆盖全部账号，任一账号复核失败时提醒，成功保留订阅次数。`:'请在微信小程序的通知设置或结果页订阅下一次提醒。';
 $('mini-push-pause').disabled=!p.enabled;$('mini-push-unbind').disabled=!p.bound;
}
function renderPushPlatform(d){
 const p=d.mini_push||{};
 $('push-platform-badge').textContent=p.configured?(p.enabled?'已开启':'已停用'):p.draft?'待填写密钥':'待配置';
 $('push-platform-status').textContent=p.configured?`AppID：${p.appid}。${p.enabled?'通道已开启，等待用户在小程序订阅。':'通道已暂停。'}`:p.draft?'模板和字段已保存，请填写 AppSecret 后开启通道。':'尚未配置，请准备符合签到业务的订阅模板、AppID 和 AppSecret。';
 if(!pushPlatformDraft){$('push-appid').value=p.appid||'wx5442ffd1e656a143';$('push-secret').value='';$('push-template').value=p.template_id||'';for(const key of ['account','result','time'])$('push-'+key+'-field').value=p.fields?.[key]||'';$('push-environment').value=p.environment||'formal';$('push-platform-enabled').checked=Boolean(p.enabled);}
 $('push-secret').required=$('push-platform-enabled').checked&&(!p.configured||$('push-appid').value!==p.appid);
}
function updateSenderRequirement(){if(page!=='admin'||!adminConfig)return;const same=adminConfig.configured&&$('provider').value===adminConfig.mail.provider&&$('sender').value.trim()===adminConfig.mail.sender;$('mail-password').required=!same;$('mail-password').placeholder=same?'留空保留当前授权码':'请输入新发件账号的 SMTP 授权码';}
function render(){
 $('worker-status').textContent=state.worker_error||('后台更新：'+formatTime(state.heartbeat));
 if(page==='home')renderHome();
 if(page==='settings'){renderRecipient();renderMiniPush();if(!notificationPrefsInitialized){const prefs=state.notification_events||{};for(const k of ['success','failure'])if($('notify-'+k))$('notify-'+k).checked=prefs[k]!==false;notificationPrefsInitialized=true;}}
 if(page==='credentials')renderConnections();
 loaded=true;
}
async function loadHistory(){if(historyBusy)return;historyBusy=true;try{const cursor=cursors[cursors.length-1];const d=await api('jobs'+(cursor?'?before='+cursor:''));nextCursor=d.next_cursor;renderJobs(d.jobs);$('page-number').textContent='第 '+cursors.length+' 页';}catch(e){feedback(e.message,true);}finally{historyBusy=false;$('previous-page').disabled=cursors.length===1;$('next-page').disabled=!nextCursor;}}
async function loadAdmin(){
 const d=await api('admin');adminConfig=d;renderPushPlatform(d);
 if(!senderDraftInitialized){$('provider').value=d.mail.provider||'qq';$('sender').value=d.mail.sender||'';$('mail-password').value='';senderDraftInitialized=true;smtpInfo();}
 $('sender-status').textContent=d.configured?(d.mail.provider==='163'?'163 邮箱':'QQ 邮箱')+' · '+d.mail.sender+'。授权码已保存。':'尚未配置发信服务，普通用户暂时无法验证收件邮箱或接收通知。';
 $('sender-badge').textContent=d.configured?'已配置':'待设置';$('sender-badge').className='badge '+(d.configured?'good':'warn');
 $('sender-configured-actions').hidden=!d.configured;$('sender-editor').hidden=d.configured&&!senderEditing;
 $('sender-edit').setAttribute('aria-expanded',String(senderEditing));$('sender-edit').disabled=busyEditors.has('sender');
 $('sender-editor-title').textContent=d.configured?'修改发信配置':'设置发信邮箱';
 $('sender-cancel').hidden=!d.configured;$('sender-cancel').disabled=busyEditors.has('sender');updateSenderRequirement();
 registrationEnabled=d.registration_enabled;$('user-count').textContent='当前共有 '+d.user_count+' 个账号（含管理员）。';$('registration-status').textContent=registrationEnabled?'注册已开放，新用户可自行创建账号。':'注册已关闭，已有用户仍可登录。';$('registration-toggle').textContent=registrationEnabled?'关闭新用户注册':'开放新用户注册';$('registration-toggle').disabled=false;
 const device=d.shared_device;sharedDeviceEnabled=device.enabled;
 const names={app:'App',mini:'小程序'};
 $('shared-device-status').textContent=(device.enabled?'共享已启用。':'共享已关闭。')+(device.channels.length?' 可用签到设备：'+device.channels.map(key=>names[key]||'未知渠道').join('、')+'。':' 暂无可用签到设备，请在账号页面导入包含设备信息的 HAR。')+' 最近更新：'+formatTime(device.updated_at);
 $('shared-device-enable').textContent=device.enabled?'刷新管理员设备配置':'启用管理员设备配置';$('shared-device-disable').disabled=!device.enabled;
}
async function refresh(){if(refreshBusy)return;refreshBusy=true;try{state=await api('state');if(page==='admin')await loadAdmin();render();if(page==='history')await loadHistory();}catch(e){feedback(e.message,true);}finally{refreshBusy=false;}}
function on(id,event,handler){if($(id))$(id).addEventListener(event,handler);}
function renderSmsCountdown(){if(page!=='credentials'||!state)return;const seconds=Math.max(0,Math.ceil((smsRetryAt-Date.now())/1000));const b=$('sms-send');if(b.textContent==='处理中…')return;b.disabled=!state.login_methods.app.effective_sms_available||state.running||seconds>0;b.textContent=seconds>0?seconds+' 秒后可重发':'获取 App 验证码';}
on('sms-send','click',e=>{if(!$('sms-phone').value||!$('sms-phone').reportValidity())return;const phone=$('sms-phone').value;action(e.currentTarget,()=>api('login/app/send',{phone,target:'app',device_mode:'preferred'}));});
on('sms-form','submit',e=>{e.preventDefault();action(e.submitter,async()=>{const code=$('sms-code').value;if(!/^[0-9]{6}$/.test(code))throw Error('请填写 6 位短信验证码。');try{return await api('login/app/verify',{code});}finally{$('sms-code').value='';await refresh();}});});
function selectLoginChannel(channel){if(currentAccountChannel&&channel!==currentAccountChannel)return;for(const key of ['app','mini']){const selected=key===channel,tab=$('login-tab-'+key);tab.classList.toggle('active',selected);tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;$('login-panel-'+key).hidden=!selected;}history.replaceState(null,'','#'+channel);}
for(const key of ['app','mini']){on('login-tab-'+key,'click',()=>selectLoginChannel(key));on('login-tab-'+key,'keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?'app':e.key==='End'?'mini':key==='app'?'mini':'app';selectLoginChannel(next);$('login-tab-'+next).focus();});}
function smtpInfo(){const p=$('provider').value;$('smtp-info').textContent=(p==='qq'?'smtp.qq.com':'smtp.163.com')+' · 465 · SSL/TLS';$('sender').placeholder='你的账号@'+(p==='qq'?'qq.com':'163.com');}
on('provider','change',()=>{smtpInfo();updateSenderRequirement();});on('sender','input',updateSenderRequirement);
for(const key of ['app','mini']){
 on(key+'-edit','click',()=>{editingChannels[key]=true;inlineError(key+'-import-error');render();$(key+'-har-file').focus();});
 on(key+'-cancel','click',()=>{if(busyEditors.has(key))return;editingChannels[key]=false;$(key+'-har-file').value='';inlineError(key+'-import-error');render();$(key+'-edit').focus();});
 on(key+'-check','click',e=>action(e.currentTarget,()=>api('run',{kind:'check',channel:key})));
 on(key+'-login-form','submit',e=>{e.preventDefault();editorAction(e.submitter,key,async()=>{inlineError(key+'-import-error');try{const file=$(key+'-har-file').files[0];if(!file)throw Error('请选择本渠道的 HAR 文件。');if(file.size>50*1024*1024)throw Error('文件超过 50 MB，请只保留登录与签到过程后重新导出。');const d=await api('import',new FormData(e.target));$(key+'-har-file').value='';editingChannels[key]=false;return d;}catch(error){inlineError(key+'-import-error',error.message);throw error;}});});
}
on('recipient-edit','click',()=>{recipientEditing=true;$('recipient').value=state.mail.recipient||'';$('email-code').value='';inlineError('recipient-error');render();$('recipient').focus();});
on('recipient-cancel','click',()=>{if(busyEditors.has('recipient'))return;recipientEditing=false;$('recipient').value=state.mail.recipient||'';$('email-code').value='';inlineError('recipient-error');render();$('recipient-edit').focus();});
on('recipient','input',updateRecipientControls);
on('send-code','click',e=>{if(!$('recipient').reportValidity())return;editorAction(e.currentTarget,'recipient',async()=>{inlineError('recipient-error');try{return await api('recipient/code',{recipient:$('recipient').value});}catch(error){inlineError('recipient-error',error.message);throw error;}});});
on('recipient-form','submit',e=>{e.preventDefault();editorAction(e.submitter,'recipient',async()=>{inlineError('recipient-error');try{const d=await api('recipient/verify',{recipient:$('recipient').value,code:$('email-code').value});$('email-code').value='';recipientEditing=false;recipientDraftInitialized=false;return d;}catch(error){inlineError('recipient-error',error.message);throw error;}});});
on('sender-edit','click',()=>{senderEditing=true;senderDraftInitialized=false;inlineError('sender-error');$('sender-editor').hidden=false;$('sender-edit').setAttribute('aria-expanded','true');loadAdmin().then(()=>$('sender').focus()).catch(error=>feedback(error.message,true));});
on('sender-cancel','click',()=>{if(busyEditors.has('sender'))return;senderEditing=false;senderDraftInitialized=false;$('mail-password').value='';inlineError('sender-error');$('sender-editor').hidden=true;$('sender-edit').setAttribute('aria-expanded','false');loadAdmin().catch(error=>feedback(error.message,true));$('sender-edit').focus();});
on('mail-form','submit',e=>{e.preventDefault();editorAction(e.submitter,'sender',async()=>{inlineError('sender-error');try{const d=await api('mail',{provider:$('provider').value,sender:$('sender').value,password:$('mail-password').value});$('mail-password').value='';senderEditing=false;senderDraftInitialized=false;return d;}catch(error){inlineError('sender-error',error.message);throw error;}});});
on('password-form','submit',e=>{e.preventDefault();action(e.submitter,()=>{if($('new-password').value!==$('confirm-password').value)throw Error('两次输入的新密码不一致。');return api('password',{current_password:$('current-password').value,password:$('new-password').value});});});
on('test-mail','click',e=>action(e.currentTarget,()=>api('mail/test',{})));
on('mini-push-pause','click',e=>action(e.currentTarget,()=>api('mini-push/pause',{})));
on('mini-push-unbind','click',e=>action(e.currentTarget,()=>api('mini-push/unbind',{})));
on('push-platform-form','input',()=>{pushPlatformDraft=true;if(adminConfig)renderPushPlatform(adminConfig);});
on('push-platform-form','change',()=>{pushPlatformDraft=true;if(adminConfig)renderPushPlatform(adminConfig);});
on('push-platform-cancel','click',()=>{pushPlatformDraft=false;if(adminConfig)renderPushPlatform(adminConfig);$('push-platform-editor').open=false;});
on('push-platform-form','submit',e=>{e.preventDefault();action(e.submitter,async()=>{const result=await api('admin/mini-push',{appid:$('push-appid').value.trim(),secret:$('push-secret').value,template_id:$('push-template').value.trim(),fields:Object.fromEntries(['account','result','time'].map(k=>[k,$('push-'+k+'-field').value.trim()])),environment:$('push-environment').value,scenario:'failure',enabled:$('push-platform-enabled').checked});pushPlatformDraft=false;$('push-secret').value='';$('push-platform-editor').open=false;return result;});});
on('retry-mail','click',e=>action(e.currentTarget,()=>api('mail/retry',{})));
on('check-all','click',e=>action(e.currentTarget,()=>api('run',{kind:'check'})));
on('toggle-schedule','click',e=>action(e.currentTarget,()=>api('schedule',{enabled:!state.schedule_enabled})));
on('registration-toggle','click',e=>action(e.currentTarget,()=>api('admin/registration',{enabled:!registrationEnabled})));
on('shared-device-enable','click',e=>action(e.currentTarget,()=>api('admin/shared-device',{enabled:true})));
on('shared-device-disable','click',e=>action(e.currentTarget,()=>api('admin/shared-device',{enabled:false})));
on('next-page','click',()=>{if(historyBusy||!nextCursor)return;cursors.push(nextCursor);loadHistory();});
on('previous-page','click',()=>{if(historyBusy||cursors.length===1)return;cursors.pop();loadHistory();});
if(page==='credentials')selectLoginChannel(currentAccountChannel||(location.hash==='#mini'?'mini':'app'));
on('notification-events-form','submit',e=>{e.preventDefault();action(e.submitter,()=>api('notifications',Object.fromEntries(['success','failure'].map(k=>[k,$('notify-'+k).checked]))));});
refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);if(page==='credentials')setInterval(renderSmsCountdown,1000);
