/* Standalone UI prototype. No business APIs, credentials, SMS or email requests. */
(() => {
  'use strict';
  const STORAGE_KEY = 'daily-checkin:prototype:v1';
  const PROGRAMS = [
    { id: 'longfor_app', name: '龙湖 App', short: 'App', kind: 'app', signTime: '09:10', checkTime: '16:00' },
    { id: 'longfor_mini', name: '龙湖小程序', short: '小程序', kind: 'mini', signTime: '09:10', checkTime: '16:00' }
  ];
  const paths = {
    home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    records:'<path d="M8 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M3 7h4M3 12h4M3 17h4m4-9h6m-6 4h6m-6 4h4"/>',
    user:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    check:'<path d="m5 12 4 4L19 6"/>', checkCircle:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    arrow:'<path d="m9 5 7 7-7 7"/>', back:'<path d="m15 5-7 7 7 7"/>', plus:'<path d="M12 5v14M5 12h14"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    warning:'<path d="m10.3 4-8 14a2 2 0 0 0 1.7 3h16a2 2 0 0 0 1.7-3l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    phone:'<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>',
    mini:'<path d="M9 7a5 5 0 1 0 0 10h2V7h4a5 5 0 0 1 0 10"/><path d="M15 7v10"/>',
    mail:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/>',
    wecom:'<path d="M21 11a8 8 0 0 1-8 8H7l-4 3v-7a8 8 0 1 1 18-4Z"/><path d="M8 10h8m-8 4h5"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/>',
    pause:'<path d="M8 5v14M16 5v14"/>', play:'<path d="m8 4 12 8-12 8Z"/>',
    file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8m-8 4h5"/>',
    bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 2v6m10-6v6M3 11h18m-13 5h2m4 0h2"/>',
    edit:'<path d="m16 3 5 5-12 12-6 1 1-6Z"/><path d="m13 6 5 5"/>',
    settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor"/><circle cx="15" cy="17" r="3" fill="currentColor"/>',
    book:'<path d="M12 5v16M3 3c4-1 6 0 9 2 3-2 5-3 9-2v16c-4-1-6 0-9 2-3-2-5-3-9-2Z"/>',
    logout:'<path d="M9 3H4v18h5m0-9h12m-4-4 4 4-4 4"/>',
    x:'<path d="m6 6 12 12M6 18 18 6"/>',
    trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
    qr:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="3" y="15" width="6" height="6" rx="1"/><path d="M15 15h3v3h3m-6 0v3m6-9v2M12 3v3m0 6h3M3 12h3m6 6v3m6 0h3"/>',
    sparkle:'<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8Z"/>'
  };
  const icon = (name, cls = '') => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `demo-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const cnDate = d => new Intl.DateTimeFormat('sv-SE', { timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit' }).format(d);
  const today = () => cnDate(new Date());
  const clock = iso => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso));
  const fullDate = iso => new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso));
  const program = id => PROGRAMS.find(p => p.id === id);
  const getAccount = id => state.accounts.find(a => a.id === id);
  const mark = (id, large = false) => `<span class="program-mark ${program(id)?.kind === 'mini' ? 'mini' : ''} ${large ? 'large' : ''}">${icon(program(id)?.kind === 'mini' ? 'mini' : 'phone')}</span>`;
  const states = {
    signed:['已签到','success','checkCircle'], unsigned:['待签到','info','clock'], auth_expired:['登录失效','warning','warning'],
    failed:['签到失败','danger','x'], unknown:['查询异常','warning','info'], paused:['已暂停','','pause']
  };
  const kinds = {manual:'手动签到',check:'状态复核',morning:'计划签到',afternoon:'下午复核'};
  const messages = {signed:'已向服务端确认今日已签到。', unsigned:'服务端显示今日未签到。',auth_expired:'登录已失效，请更新此账号的登录文件。',failed:'签到提交失败，复核确认今日仍未签到。',unknown:'本次状态查询未完成，请稍后复核。'};
  const badge = (status, label) => {const s=states[status]||states.unknown;return `<span class="badge ${s[1]}">${icon(s[2])}${esc(label||s[0])}</span>`;};
  const accountState = a => a.authState === 'expired' ? 'auth_expired' : (a.resultDate === today() ? a.todayState : 'unsigned');
  const cardState = a => !a.enabled ? 'paused' : accountState(a);
  const activeAccounts = () => state.accounts.filter(a=>a.enabled);
  const navLink = (to, text, cls='') => `<a class="${cls}" href="#${to}">${text}</a>`;
  const note = (text, type='') => `<div class="info-note ${type}">${icon(type==='warning'?'warning':'info')}<span>${text}</span></div>`;
  const title = (heading, subtitle='', eyebrow='') => `<div class="page-heading">${eyebrow?`<span class="eyebrow">${eyebrow}</span>`:''}<h1>${heading}</h1>${subtitle?`<p>${subtitle}</p>`:''}</div>`;
  const empty = (heading, text, cta='', symbol='grid') => `<div class="empty"><div class="icon-box">${icon(symbol)}</div><h2>${heading}</h2><p>${text}</p>${cta}</div>`;
  const spinner = '<span class="busy-spinner" aria-hidden="true"></span>';
  const main = document.getElementById('main');
  const toastEl = document.getElementById('toast');
  const dialogEl = document.getElementById('dialog');
  let generation=0, busy=new Set(), draft=null, method='import', smsSent=false, toastTimer, confirmCallback;
  let filters={program:'all',status:'all',search:''};

  function seed() {
    const day=today(), yesterday=cnDate(new Date(Date.now()-86400000));
    const make=(id,type,alias,phone,result,enabled=true,auth='valid')=>({id,programTypeId:type,alias,maskedIdentity:phone,authState:auth,enabled,signTime:'09:10',checkTime:'16:00',todayState:result,resultDate:day,credentialUpdatedAt:`${day}T08:50:00+08:00`,scenario:'success'});
    const accounts=[make('app-main','longfor_app','主号','138****0826','signed'),make('app-family','longfor_app','家人号','139****1658','unsigned',true,'expired'),make('app-spare','longfor_app','备用号','136****0932','unsigned',false),make('mini-main','longfor_mini','主号','138****0826','signed'),make('mini-family','longfor_mini','家人号','139****1658','unsigned')];
    const log=(id,accountId,status,time,kind='morning',date=day)=>({id,accountId,programTypeId:accounts.find(a=>a.id===accountId).programTypeId,aliasSnapshot:accounts.find(a=>a.id===accountId).alias,kind,state:status,message:messages[status],created:`${date}T${time}:00+08:00`,notification:{channel:'邮箱',state:status==='unsigned'?'none':'sent',attempts:status==='unsigned'?0:1}});
    return {version:1,accounts,runs:[log('run-1','app-family','auth_expired','09:11'),log('run-2','mini-main','signed','09:10'),log('run-3','app-main','signed','09:10'),log('run-4','mini-family','unsigned','09:09','check'),log('run-5','app-main','unsigned','08:55','check'),log('run-6','mini-family','failed','09:10','morning',yesterday)],notifications:{email:{enabled:true,provider:'qq',address:'demo@qq.com'},wecom:{bound:false,enabled:false},events:{success:true,failure:true},simulateFailure:false}};
  }
  function load() {
    try {const d=JSON.parse(localStorage.getItem(STORAGE_KEY));if(d?.version===1&&Array.isArray(d.accounts)&&Array.isArray(d.runs)&&d.notifications?.events&&d.notifications?.email&&d.notifications?.wecom&&d.accounts.every(a=>program(a.programTypeId)&&typeof a.id==='string'&&typeof a.alias==='string'&&/^\d\d:\d\d$/.test(a.signTime)&&/^\d\d:\d\d$/.test(a.checkTime)))return d;}catch(_){/* fall back to isolated demo data */}
    return seed();
  }
  let state=load();
  state.notifications.events=Object.fromEntries(['success','failure'].map(key=>[key,state.notifications.events[key]!==false]));
  function save() {try{localStorage.setItem(STORAGE_KEY,JSON.stringify(state));}catch(_){toast('浏览器未允许本地存储，刷新后将恢复演示数据。');}}
  function toast(text) {clearTimeout(toastTimer);toastEl.textContent=text;toastEl.classList.add('visible');toastTimer=setTimeout(()=>toastEl.classList.remove('visible'),3200);}
  const route=()=>{const u=new URL(location.hash.slice(1)||'/home','https://prototype.local');return {path:u.pathname,query:u.searchParams};};
  function go(to, replace=false) {if(replace){history.replaceState(null,'',`#${to}`);render();window.scrollTo(0,0);}else if(location.hash===`#${to}`){render();window.scrollTo(0,0);}else location.hash=to;}
  function notifyModal(heading,text,action,label='确认') {confirmCallback=action;dialogEl.innerHTML=`<h2 id="dialog-title">${esc(heading)}</h2><p>${esc(text)}</p><div class="btn-row"><button class="btn outline" data-action="cancel-dialog">取消</button><button class="btn primary" data-action="confirm-dialog">${esc(label)}</button></div>`;dialogEl.showModal();}
  function nextFor(a) {
    if(!a.enabled)return null;if(a.authState==='expired')return null;
    const now=new Date(),day=today();
    const candidates=[{time:a.signTime,kind:'签到'},{time:a.checkTime,kind:'复核'}].map(t=>({...t,date:new Date(`${day}T${t.time}:00+08:00`)}));
    const future=candidates.filter(c=>c.date>now).sort((a,b)=>a.date-b.date);
    if(future.length)return {...future[0],dayLabel:'今天'};
    const first=candidates.sort((a,b)=>a.date-b.date)[0];return {...first,date:new Date(first.date.getTime()+86400000),dayLabel:'明天'};
  }
  function nextLabel(a) {if(!a.enabled)return '自动签到已暂停';if(a.authState==='expired')return '更新登录后恢复计划';const n=nextFor(a);return `${n.dayLabel} ${n.time} ${n.kind}`;}
  function switchButton(a) {return `<button class="switch-wrap" role="switch" aria-checked="${a.enabled}" aria-label="${esc(program(a.programTypeId).name+' '+a.alias+' 自动签到')}" data-action="toggle-account" data-id="${esc(a.id)}"><span>${a.enabled?'自动签到':'已暂停'}</span><span class="switch" aria-hidden="true"></span></button>`;}
  function accountCard(a) {return `<article class="card account-card"><a href="#/account/${encodeURIComponent(a.id)}" class="account-open" aria-label="管理 ${esc(program(a.programTypeId).name+' '+a.alias)}"><div class="account-head">${mark(a.programTypeId)}<div class="account-ident"><h3>${esc(a.alias)}</h3><p>${esc(program(a.programTypeId).name)} · ${esc(a.maskedIdentity)}</p></div>${badge(cardState(a))}</div><div class="account-status"><span class="auth-status">${icon(a.authState==='expired'?'warning':'shield')}${a.authState==='expired'?'需要更新登录':'登录有效'}</span><span class="caption">${a.enabled?'独立计划':'手动签到仍可用'}</span></div></a><div class="account-footer"><span class="caption">${esc(nextLabel(a))}</span>${switchButton(a)}</div></article>`;}
  function runRows(runs) {return `<div class="card run-list">${runs.map(r=>{const a=getAccount(r.accountId),s=states[r.state]||states.unknown;return `<div class="run-row"><div class="icon-box ${r.state==='signed'?'':r.state==='unsigned'?'blue':'orange'}">${icon(s[2])}</div><div class="run-copy"><h3>${esc(program(r.programTypeId)?.name||'已移除程序')} · ${esc(a?.alias||r.aliasSnapshot)}</h3><p>${esc(kinds[r.kind]||r.kind)} · ${r.notification?.state==='sent'?'演示通知已发送':r.notification?.state==='failed'?'通知失败，可重试':'结果记录'}</p></div><div class="run-end">${badge(r.state,r.state==='unsigned'?'未签到':undefined)}<time>${cnDate(new Date(r.created))===today()?clock(r.created):fullDate(r.created)}</time></div><a href="#/run/${encodeURIComponent(r.id)}" aria-label="查看 ${esc(program(r.programTypeId)?.name+' '+(a?.alias||r.aliasSnapshot)+' '+kinds[r.kind]+' '+r.created)}"></a></div>`;}).join('')}</div>`;}
  function homepage() {
    const enabled=activeAccounts(),signed=enabled.filter(a=>accountState(a)==='signed').length;
    const issues=enabled.filter(a=>['auth_expired','failed','unknown'].includes(accountState(a)));
    const pending=enabled.filter(a=>accountState(a)==='unsigned').length;
    const next=enabled.map(a=>({a,n:nextFor(a)})).filter(x=>x.n).sort((a,b)=>a.n.date-b.n.date)[0];
    const dayName=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'long',day:'numeric',weekday:'long'}).format(new Date());
    const percent=enabled.length?signed/enabled.length:0;
    return `${title('今天，也安排好了。','签到有计划，结果有回音。',esc(dayName))}
    <section class="hero" aria-label="今日签到进度"><div class="hero-top"><div><span class="hero-label">今日签到</span><div class="hero-value">${signed}<span>/ ${enabled.length}</span></div><div class="hero-subtitle">${enabled.length?`已开启的 ${enabled.length} 个账号中，${signed} 个已确认`:'添加账号，开始你的签到计划'}</div></div><div class="progress-ring" role="img" aria-label="已完成 ${Math.round(percent*100)}%"><svg viewBox="0 0 110 110" aria-hidden="true"><circle class="ring-bg" cx="55" cy="55" r="44"/><circle class="ring-value" cx="55" cy="55" r="44" stroke-dasharray="276.46" stroke-dashoffset="${276.46*(1-percent)}"/></svg><span class="ring-symbol">${icon('check')}</span></div></div><div class="hero-stats"><div><strong>${signed}</strong><span>已完成</span></div><div><strong>${pending}</strong><span>待签到</span></div><div><strong>${issues.length}</strong><span>待处理</span></div></div>${navLink('/accounts','管理我的签到 '+icon('arrow'),'text-link')}</section>
    ${next?`<div class="card next-card"><span class="icon-box">${icon('clock')}</span><div><h3>下一次${next.n.kind}</h3><p class="caption">${esc(program(next.a.programTypeId).name)} · ${esc(next.a.alias)}</p></div><div class="next-time">${next.n.time}<small>${next.n.dayLabel} · 北京时间</small></div></div>`:''}
    ${issues.length?`<div class="section-heading"><h2>需要处理 <span class="caption">${issues.length}</span></h2></div><div class="stack">${issues.slice(0,2).map(a=>`<div class="attention-card"><span class="icon-box orange">${icon('warning')}</span><div><h3>${esc(program(a.programTypeId).name)} · ${esc(a.alias)}</h3><p>${accountState(a)==='auth_expired'?'登录已失效，更新后继续签到':'本次执行异常，请查看结果'}</p></div>${navLink(`/account/${a.id}${accountState(a)==='auth_expired'?'/auth':''}`,accountState(a)==='auth_expired'?'去更新':'查看','text-link')}</div>`).join('')}</div>`:''}
    ${!state.accounts.length?`<div class="mt">${empty('还没有签到账号','选择一种程序，添加你的第一个账号。',navLink('/add/program',icon('plus')+' 添加账号','btn primary'))}</div>`:''}
    <div class="section-heading"><h2>最近动态</h2>${navLink('/records','全部记录 '+icon('arrow'),'text-link')}</div>
    ${state.runs.length?runRows(state.runs.slice(0,5)):empty('还没有运行记录','完成绑定后，签到和复核结果会出现在这里。','','records')}
    <p class="caption mt" style="text-align:center">${icon('shield')} 每个账号独立运行，结果均需复核确认</p>`;
  }
  function filteredAccounts() {return state.accounts.filter(a=>(filters.program==='all'||a.programTypeId===filters.program)&&(filters.status==='all'||(filters.status==='attention'?a.enabled&&['auth_expired','failed','unknown'].includes(accountState(a)):filters.status==='paused'?!a.enabled:a.enabled&&accountState(a)===filters.status))&&(`${a.alias} ${a.maskedIdentity} ${program(a.programTypeId).name}`).toLowerCase().includes(filters.search.toLowerCase()));}
  function accountListMarkup(filtered) {return filtered.length?filtered.map(accountCard).join(''):empty(state.accounts.length?'没有匹配的账号':'添加你的第一个账号',state.accounts.length?'试试更换筛选条件，或添加新的账号。':'龙湖 App 和小程序分别添加，同一种程序也能添加多个账号。',navLink('/add/program','添加账号','btn primary'));}
  function accountsPage() {
    const filtered=filteredAccounts();
    return `<div class="page-heading heading-row"><div><span class="eyebrow">MY CHECK-INS</span><h1>我的签到</h1><p>不同程序、不同账号，各自安排。</p></div><a href="#/add/program" class="add-button" aria-label="添加账号">${icon('plus')}</a></div><label class="search-wrap">${icon('search')}<input id="account-search" type="search" aria-label="搜索账号" placeholder="搜索昵称、程序或手机号" value="${esc(filters.search)}"></label><div class="filters" aria-label="按程序筛选">${[{id:'all',name:'全部'},...PROGRAMS].map(p=>`<button class="chip ${filters.program===p.id?'active':''}" data-action="filter-program" data-id="${p.id}" aria-pressed="${filters.program===p.id}">${p.name}</button>`).join('')}</div><div class="filter-row"><div class="count-label" style="align-self:center;margin:0">共 ${filtered.length} 个账号</div><label><span class="field-label">账号状态</span><select id="account-status-filter" aria-label="筛选账号状态">${[['all','全部状态'],['signed','今日已签到'],['unsigned','待签到'],['attention','需要处理'],['paused','已暂停']].map(([v,t])=>`<option value="${v}" ${filters.status===v?'selected':''}>${t}</option>`).join('')}</select></label></div><div id="account-list">${accountListMarkup(filtered)}</div><div class="mt">${note('想添加第二个龙湖 App？点击右上角“＋”，再次选择龙湖 App 即可。')}</div>`;
  }
  function accountDetail(a) {
    const status=accountState(a),latest=state.runs.find(r=>r.accountId===a.id),isBusy=busy.has(a.id);
    return `<div class="detail-profile">${mark(a.programTypeId,true)}<h1>${esc(a.alias)}</h1><p>${esc(program(a.programTypeId).name)} · ${esc(a.maskedIdentity)}</p>${badge(cardState(a))}</div>
    <section class="card result-card"><div class="result-title"><h2>今日结果</h2>${badge(status,status==='unsigned'?'尚未签到':undefined)}</div><p>${esc(messages[status]||messages.unsigned)}</p>${latest?`<p class="caption mt">最近执行 ${fullDate(latest.created)}</p>`:''}<div class="btn-row">${status==='auth_expired'?navLink(`/account/${a.id}/auth`,'更新登录','btn primary'):status==='signed'?navLink(`/records?account=${a.id}&date=${today()}&state=signed`,'查看今日结果','btn primary'):`<button class="btn primary" data-action="run" data-id="${a.id}" ${isBusy?'disabled':''}>${isBusy?spinner:icon('play')}${isBusy?'正在执行…':'立即签到'}</button>`}${status!=='auth_expired'?`<button class="btn outline" data-action="check" data-id="${a.id}" ${isBusy?'disabled':''}>${isBusy?spinner:icon('refresh')}复核状态</button>`:''}</div></section>
    <div class="section-heading"><h2>独立签到计划</h2>${navLink(`/account/${a.id}/edit`,'修改配置 '+icon('arrow'),'text-link')}</div><div class="card"><div class="row between"><div><h3>自动签到</h3><p class="caption">${esc(nextLabel(a))}</p></div>${switchButton(a)}</div><div class="plan-times"><div><strong>${esc(a.signTime)}</strong><span>每日签到</span></div><div><strong>${esc(a.checkTime)}</strong><span>状态复核</span></div></div><p class="caption mt">北京时间 · 暂停计划后，仍可手动签到</p></div>
    <div class="section-heading"><h2>账号管理</h2></div><div class="card cell-group">${cell(`/account/${a.id}/auth`,'shield','登录配置',a.authState==='expired'?'登录已失效，请更新':'已绑定 · 可修改登录配置',a.authState==='expired'?'待更新':'有效')}${cell(`/records?account=${a.id}`,'records','此账号的记录','仅查看当前账号的签到与复核')}${cell('/notifications','bell','通知设置','企业微信与邮箱通知')}</div>
    <details class="demo-select"><summary>演示执行场景</summary><label><span class="field-label">下次执行的模拟结果</span><select id="scenario-select" data-id="${a.id}">${[['success','签到成功 / 正常复核'],['failed','签到提交失败 / 仍未签到'],['auth_expired','登录失效'],['unknown','查询异常']].map(([v,t])=>`<option value="${v}" ${a.scenario===v?'selected':''}>${t}</option>`).join('')}</select></label><p class="field-hint">仅控制此原型的模拟结果，不会请求真实接口。</p></details>
    <button class="text-link mt" style="color:var(--danger);width:100%" data-action="delete-account" data-id="${a.id}">${icon('trash')}移除此账号</button>`;
  }
  function cell(to,symbol,heading,subtitle,value='') {return `<a class="cell" href="#${to}"><span class="icon-box ${symbol==='mail'?'blue':''}">${icon(symbol)}</span><span class="cell-main"><h3>${heading}</h3>${subtitle?`<p>${subtitle}</p>`:''}</span>${value?`<span class="cell-value">${esc(value)}</span>`:''}${icon('arrow','chevron')}</a>`;}
  function scheduleForm(a,adding=false) {return `<form id="${adding?'add-plan':'edit-account'}" data-id="${esc(a.id||'')}"><div class="card"><div class="form-section"><label class="field-label" for="alias">账号昵称</label><input id="alias" name="alias" value="${esc(a.alias)}" required maxlength="30" placeholder="例如：主号、家人号"><p class="field-hint">用于区分同一程序下的多个账号。</p></div><div class="divider"></div><div class="time-fields"><label><span class="field-label">每日签到</span><input type="time" name="signTime" value="${esc(a.signTime)}" required></label><label><span class="field-label">状态复核</span><input type="time" name="checkTime" value="${esc(a.checkTime)}" required></label></div><p class="field-hint">北京时间 · 复核时间应晚于签到时间</p><label class="check-label mt"><input type="checkbox" name="enabled" ${a.enabled?'checked':''}>${adding?'确认开启自动签到':'开启此账号的自动签到'}</label></div><div class="mt">${note(adding?'仅在你确认后开启此账号的计划；其他账号不受影响。':'这里只修改当前账号，其他账号的计划和凭证保持不变。')}</div><div id="form-message" class="mt" role="alert"></div><button class="btn primary full mt" type="submit">${adding?'完成添加':'保存修改'}</button></form>`;}
  function editPage(a) {return `${title('修改配置','已有配置已为你填好，只需修改需要调整的内容。',esc(program(a.programTypeId).name+' · '+a.alias))}${scheduleForm(a)}`;}
  function steps(n) {return `<div class="steps" aria-label="添加进度：第 ${n} 步，共 3 步">${['选择程序','绑定账号','签到计划'].map((v,i)=>`<div class="step ${i+1<=n?'active':''}"><span>${i+1<n?'✓':i+1}</span>${v}</div>`).join('')}</div>`;}
  function sampleInput(p) {return `<div class="sample-file">${icon('file')}<div><strong>${esc(p.name)}-登录示例.har</strong><span>示例文件 · 不上传、不读取真实凭证</span></div></div><label><span class="field-label">选择文件校验场景</span><select name="sample" required><option value="valid">完整登录文件（演示成功）</option><option value="missing">缺少成功登录响应（演示失败）</option><option value="expired">登录凭证已失效（演示失败）</option></select></label><p class="field-hint">真实使用时，选择对应端导出的 HAR。登录与必要设备信息从同一份文件提取。</p>`;}
  function addPage(path,q) {
    if(path==='/add/program')return `${title('添加一个签到账号','同一种程序可以添加多次，每个账号独立管理。')}${steps(1)}${PROGRAMS.map(p=>`<button class="program-choice" data-action="choose-program" data-id="${p.id}">${mark(p.id)}<span><h2>${p.name}</h2><p>已添加 ${state.accounts.filter(a=>a.programTypeId===p.id).length} 个 · 可继续添加</p></span>${icon('arrow','chevron')}</button>`).join('')}<button class="program-choice" disabled><span class="icon-box gray">${icon('plus')}</span><span><h2>更多签到程序</h2><p>待接入 · 后续持续扩展</p></span><span class="badge">待接入</span></button><div class="mt">${note('微信登录用于进入本平台。龙湖 App 和小程序的登录需要分别绑定。')}</div>`;
    if(path==='/add/done') {const a=getAccount(q.get('id'));if(!a)return empty('账号不存在','请返回账号列表。',navLink('/accounts','我的签到','btn primary'));return `${steps(3)}<div class="success-panel"><div class="success-icon">${icon('check')}</div><h1>新的签到，安排好了</h1><p>${esc(program(a.programTypeId).name)} · ${esc(a.alias)}<br>${a.enabled?'自动签到计划已开启':'账号已保存，自动计划暂未开启'}</p></div><div class="card"><div class="kv"><span>每日签到</span><span>${a.signTime}</span></div><div class="kv"><span>状态复核</span><span>${a.checkTime}</span></div><div class="kv"><span>时区</span><span>北京时间</span></div></div>${navLink(`/account/${a.id}`,'查看账号','btn primary full mt')}${navLink('/add/program','再添加一个账号','btn outline full mt')}`;}
    if(!draft){queueMicrotask(()=>go('/add/program',true));return '';}
    const p=program(draft.programTypeId);
    if(path==='/add/bind')return `${title('绑定你的账号','给它起个名字，再导入对应端的登录文件。',esc(p.name))}${steps(2)}<form id="add-bind"><div class="card"><div class="form-section"><label class="field-label" for="alias">账号昵称</label><input id="alias" name="alias" required maxlength="30" value="${esc(draft.alias)}" placeholder="例如：家人号"></div>${sampleInput(p)}</div><div class="mt">${note('本原型使用内置示例文件演示。请勿在原型中填写真实密码、验证码或上传真实抓包文件。')}</div><div id="form-message" class="mt" role="alert"></div><button class="btn primary full mt" type="submit">导入并校验</button></form>`;
    if(path==='/add/verified') {if(!draft.validated){queueMicrotask(()=>go('/add/bind',true));return '';}return `${steps(2)}<div class="success-panel"><div class="success-icon">${icon('shield')}</div><h1>登录文件校验通过</h1><p>登录信息和必要设备信息已就绪。<br>接下来，为这个账号安排签到时间。</p></div><div class="card"><div class="kv"><span>签到程序</span><span>${esc(p.name)}</span></div><div class="kv"><span>账号昵称</span><span>${esc(draft.alias)}</span></div><div class="kv"><span>示例账号</span><span>${esc(draft.maskedIdentity)}</span></div><div class="kv"><span>校验方式</span><span>模拟只读状态查询</span></div></div>${navLink('/add/plan','设置签到计划','btn primary full mt')}`;}
    if(path==='/add/plan') {if(!draft.validated){queueMicrotask(()=>go('/add/bind',true));return '';}return `${title('给它安排一个计划','建议时间已填好，你也可以为这个账号单独调整。')}${steps(3)}${scheduleForm(draft,true)}`;}
    return empty('页面不存在','请重新选择签到程序。',navLink('/add/program','返回添加','btn primary'));
  }
  function authPage(a) {const p=program(a.programTypeId);return `${title('修改登录配置','只更新当前账号，校验通过后才会替换原配置。',esc(p.name+' · '+a.alias))}${p.kind==='app'?`<div class="tabs-segment" role="group" aria-label="登录更新方式"><button class="${method==='import'?'active':''}" data-action="auth-method" data-id="import" aria-pressed="${method==='import'}">导入登录文件</button><button class="${method==='sms'?'active':''}" data-action="auth-method" data-id="sms" aria-pressed="${method==='sms'}">短信更新</button></div>`:''}<div class="mb">${note(a.authState==='expired'?'当前登录已失效。更新通过后，按此账号原有计划继续运行。':'当前已有有效配置，更新校验失败会保留原配置。',a.authState==='expired'?'warning':'')}</div><form id="update-auth" data-id="${a.id}"><div class="card">${method==='sms'&&p.kind==='app'?`<label><span class="field-label">已绑定手机号</span><input value="${esc(a.maskedIdentity)}" readonly></label><p class="field-hint mb">仅适用于已首次导入本人 App 登录文件的账号。</p><button type="button" class="btn secondary full" data-action="send-sms">${smsSent?'已发送（演示）':'获取演示验证码'}</button><div id="sms-hint" class="field-hint">${smsSent?'演示验证码：123456。不发送真实短信。':'点击后展示演示验证码，不发送真实短信。'}</div><label class="field-label mt" for="sms-code">演示验证码</label><input id="sms-code" name="code" inputmode="numeric" autocomplete="off" pattern="[0-9]{6}" maxlength="6" required placeholder="请输入 6 位演示验证码">`:sampleInput(p)}</div><div id="form-message" class="mt" role="alert"></div><button class="btn primary full mt" type="submit">校验并更新登录</button></form><div class="mt">${note(p.kind==='mini'?'小程序使用对应端的登录文件更新。企业微信通知和本平台微信登录均不能替代龙湖授权。':'短信更新仅作用于此 App 账号，不会更新任何小程序账号。')}</div>`;}
  function recordsPage(q) {
    const af=q.get('account')||'all',pf=q.get('program')||'all',df=q.get('date')||'',sf=q.get('state')||'all';
    const runs=state.runs.filter(r=>(af==='all'||r.accountId===af)&&(pf==='all'||r.programTypeId===pf)&&(!df||cnDate(new Date(r.created))===df)&&(sf==='all'||r.state===sf));
    const deletedIds=[...new Set(state.runs.filter(r=>!getAccount(r.accountId)).map(r=>r.accountId))];
    return `${title('每一次，都有记录','签到结果与通知状态，清楚可查。','ACTIVITY LOG')}<form id="record-filters"><div class="filter-row"><label><span class="field-label">签到程序</span><select name="program" aria-label="筛选记录程序"><option value="all">全部程序</option>${PROGRAMS.map(p=>`<option value="${p.id}" ${pf===p.id?'selected':''}>${p.name}</option>`).join('')}</select></label><label><span class="field-label">账号</span><select name="account" aria-label="筛选记录账号"><option value="all">全部账号</option>${state.accounts.map(a=>`<option value="${esc(a.id)}" ${af===a.id?'selected':''}>${esc(program(a.programTypeId).short+' · '+a.alias)}</option>`).join('')}${deletedIds.map(id=>`<option value="${esc(id)}" ${af===id?'selected':''}>已移除 · ${esc(state.runs.find(r=>r.accountId===id).aliasSnapshot)}</option>`).join('')}</select></label></div><div class="filter-row"><label><span class="field-label">日期</span><input type="date" name="date" value="${esc(df)}" aria-label="筛选记录日期"></label><label><span class="field-label">执行结果</span><select name="state" aria-label="筛选执行结果"><option value="all">全部结果</option>${Object.entries(states).filter(([v])=>v!=='paused').map(([v,s])=>`<option value="${v}" ${sf===v?'selected':''}>${v==='unsigned'?'确认未签到':s[0]}</option>`).join('')}</select></label></div><div class="row between"><p class="count-label" style="margin:0">共 ${runs.length} 条记录</p><button type="button" class="text-link" data-action="clear-record-filters">清除筛选</button></div></form>${runs.length?runRows(runs):empty('没有匹配的记录','试试其他日期或筛选条件。','','records')}`;
  }
  function runPage(r) {
    const a=getAccount(r.accountId),n=r.notification||{state:'none',channel:'邮箱',attempts:0};
    const nt={sent:'已发送（演示）',failed:'发送失败',none:'本次未发送',unconfigured:'未配置通知'};
    return `${title('执行详情',esc(program(r.programTypeId)?.name+' · '+(a?.alias||r.aliasSnapshot)),esc(kinds[r.kind]))}<div class="card result-card"><div class="result-title"><h2>执行结果</h2>${badge(r.state,r.state==='unsigned'?'确认未签到':undefined)}</div><p>${esc(r.message)}</p><div class="divider"></div><div class="kv"><span>执行时间</span><span>${fullDate(r.created)}（北京）</span></div><div class="kv"><span>执行方式</span><span>${esc(kinds[r.kind])}</span></div><div class="kv"><span>账号</span><span>${esc(a?.maskedIdentity||'账号已移除')}</span></div></div><div class="section-heading"><h2>通知状态</h2>${badge(n.state==='sent'?'signed':n.state==='failed'?'failed':'unsigned',nt[n.state])}</div><div class="card"><div class="kv"><span>通知渠道</span><span>${esc(n.channel||'未设置')}</span></div><div class="kv"><span>发送次数</span><span>${n.attempts||0} 次（演示）</span></div><div class="notification-box"><strong>${esc(program(r.programTypeId)?.name)} · ${esc(a?.alias||r.aliasSnapshot)}</strong>${esc(r.message)}</div>${n.state==='failed'?`<p class="subtext mb">模拟通知服务暂时不可用，签到结果不受影响。</p><button class="btn secondary full" data-action="retry-mail" data-id="${r.id}" ${busy.has(r.id)?'disabled':''}>${busy.has(r.id)?spinner:''}重试演示通知</button>`:''}</div>${a?navLink(`/account/${a.id}`,'管理此账号','btn primary full mt'):note('此账号已移除，历史记录仍保留。')}`;
  }
  function mePage() {return `<div class="profile-top"><div class="profile-avatar">${icon('user')}</div><div><h1>你好，签到体验官</h1><p>一个地方，安排好每天的签到。</p></div></div><div class="card stat-mini"><div><strong>${state.accounts.length}</strong><span>已添加账号</span></div><div><strong>${new Set(state.accounts.map(a=>a.programTypeId)).size}</strong><span>签到程序</span></div><div><strong>${activeAccounts().length}</strong><span>自动运行中</span></div></div><div class="section-heading"><h2>偏好设置</h2></div><div class="card cell-group">${cell('/notifications','bell','通知设置','签到成功、失败或登录失效提醒',state.notifications.wecom.bound?'企业微信已绑定':'邮箱已支持')}${cell('/notifications/email','mail','接收邮箱',state.notifications.email.enabled?esc(state.notifications.email.address):'配置你的接收邮箱',state.notifications.email.enabled?'已开启':'未开启')}${cell('/notifications/wecom','wecom','企业微信','绑定后查看通知演示',state.notifications.wecom.bound?'已绑定':'未绑定')}</div><div class="section-heading"><h2>使用帮助</h2></div><div class="card cell-group">${cell('/help','book','操作手册','添加账号、更新登录与签到说明')}${cell('/video','play','视频教程','查看已发布的操作视频入口')}${cell('/about','info','关于此原型','本机模拟，不执行真实签到')}</div><details class="demo-tools"><summary>原型演示工具</summary><div class="card"><p class="subtext mb">切换首次使用状态，或恢复预置的五个账号。只影响本浏览器的演示数据。</p><div class="stack"><button class="btn outline full" data-action="empty-demo">体验首次使用（空数据）</button><button class="btn secondary full" data-action="reset-demo">恢复默认演示</button></div></div></details>`;}
  function notificationsPage() {const n=state.notifications;return `${title('有结果，也有提醒','一次设置，适用于你添加的所有账号。')}<div class="card"><div class="notification-channel"><span class="icon-box">${icon('wecom')}</span><div><h3>企业微信</h3><p class="caption">签到结果，随时可见</p></div><span class="badge ${n.wecom.bound?'success':''}">${n.wecom.bound?'已绑定':'未绑定'}</span></div>${navLink('/notifications/wecom',n.wecom.bound?'修改企业微信设置':'绑定企业微信','btn secondary full')}</div><div class="card"><div class="notification-channel"><span class="icon-box blue">${icon('mail')}</span><div><h3>邮箱通知</h3><p class="caption">${n.email.enabled?esc(n.email.address):'支持 QQ、163 接收邮箱'}</p></div><span class="badge ${n.email.enabled?'success':''}">${n.email.enabled?'已开启':'未开启'}</span></div>${navLink('/notifications/email',n.email.enabled?'修改接收邮箱':'设置接收邮箱','btn outline full')}</div><div class="section-heading"><h2>希望收到哪些提醒</h2></div><form id="notification-events"><div class="card event-prefs">${[['success','签到成功','每个账号确认成功后提醒'],['failure','失败或登录失效','需要你处理时及时提醒']].map(([key,heading,sub])=>`<label><input type="checkbox" name="${key}" ${n.events[key]?'checked':''}><span><strong>${heading}</strong><span class="caption" style="display:block">${sub}</span></span></label>`).join('')}</div><button class="btn primary full mt" type="submit">保存提醒偏好</button></form><div class="mt">${note('通知会注明程序与账号昵称，可直接定位需要处理的账号。企业微信绑定不提供龙湖登录授权。')}</div><details class="demo-tools"><summary>演示通知失败</summary><label class="check-label"><input type="checkbox" id="simulate-notify-failure" ${n.simulateFailure?'checked':''}>模拟通知发送失败</label><p class="caption">开启后执行签到，可在记录详情中体验通知失败与重试。</p></details>`;}
  function emailPage() {const e=state.notifications.email;return `${title(e.enabled?'修改接收邮箱':'设置接收邮箱','只填写你的接收邮箱即可。')}<form id="email-settings"><div class="card"><label class="field-label" for="email-provider">邮箱类型</label><select id="email-provider" name="provider"><option value="qq" ${e.provider==='qq'?'selected':''}>QQ 邮箱</option><option value="163" ${e.provider==='163'?'selected':''}>163 邮箱</option></select><label class="field-label mt" for="email-address">接收邮箱</label><input id="email-address" name="address" type="email" autocomplete="off" value="${esc(e.address)}" required placeholder="例如 name@qq.com"><p class="field-hint">无需填写邮箱密码或 SMTP 授权码。</p><label class="check-label mt"><input type="checkbox" name="enabled" ${e.enabled?'checked':''}>开启邮箱通知</label></div><div id="form-message" class="mt" role="alert"></div><button type="submit" class="btn primary full mt">保存邮箱配置</button></form><button class="btn outline full mt" data-action="test-notification" data-id="email">发送演示通知</button><div class="mt">${note('此原型只保存演示设置，不连接邮件服务，也不发送真实邮件。')}</div>`;}
  function wecomPage() {const w=state.notifications.wecom;return `${title(w.bound?'企业微信已绑定':'绑定企业微信','接收签到提醒，不用于登录龙湖账号。')}<div class="card"><div class="binding-visual">${icon(w.bound?'checkCircle':'wecom')}</div><h2 style="text-align:center">${w.bound?'通知通道已就绪':'让签到结果，主动找到你'}</h2>${w.bound?`<p class="subtext mt" style="text-align:center">演示成员 · 已关联本平台账号</p><label class="check-label mt"><input id="wecom-enabled" type="checkbox" ${w.enabled?'checked':''}>开启企业微信通知</label><button class="btn primary full mt" data-action="test-notification" data-id="wecom">发送演示通知</button><button class="text-link full" style="width:100%;color:var(--danger)" data-action="unbind-wecom">解除绑定</button>`:`<ol class="number-list"><li>打开企业微信通知绑定入口</li><li>按接入方式完成身份关联</li><li>回到这里确认通知已连接</li></ol><button class="btn primary full" data-action="bind-wecom">模拟完成绑定</button>`}</div><div class="mt">${note('这里演示绑定与通知效果，不提供真实扫码或推送。正式接入时需确认接收人的企业微信身份及可见范围。')}</div>`;}
  function helpPage() {return `${title('签到，从这里开始','几步设置，把每天的小事交给计划。')}<div class="card">${[['选择程序','在“我的签到”点右上角＋，选择龙湖 App 或龙湖小程序；同一种程序可以继续添加。'],['分别绑定账号','首次导入对应端的 HAR。每个账号使用自己的登录信息，并起一个方便辨认的昵称。'],['确认独立计划','设置签到和复核时间，确认开启自动签到。每个账号的设置独立生效。'],['查看结果','首页查看今日进度。登录失效或执行异常会出现在“需要处理”，详情里可以更新登录、手动签到或复核。'],['修改已有配置','进入账号详情后选择“修改配置”。App 已首次导入的账号可用短信更新，小程序仍使用对应端登录文件。'],['开启通知','在“我的”设置企业微信或接收邮箱，并选择成功、异常及下午复核提醒。']].map(([h,p],i)=>`<div class="help-step"><span class="step-num">${i+1}</span><div><h3>${h}</h3><p>${p}</p></div></div>`).join('')}</div><div class="mt">${note('以上为新原型的操作说明。已上线的平台图文手册正文保持不变。')}</div>${navLink('/add/program','开始添加账号','btn primary full mt')}`;}
  function videoPage() {return `${title('视频教程','沿用已发布的视频，操作更直观。')}<div class="video-placeholder"><div class="icon-box">${icon('play')}</div><h2>跟着视频，完成首次配置</h2><p>新原型尚未录制独立视频。<br>可打开现有平台的视频教程，<br>了解登录文件导入的操作。</p><a class="btn primary" href="http://120.55.246.247:8091/video" target="_blank" rel="noopener noreferrer">打开已发布的视频 ${icon('arrow')}</a><p class="caption">将打开现有平台，不改变原型数据。</p></div>`;}
  function aboutPage() {return `${title('每日签到','多程序、多账号的每日签到助手。')}<div class="card"><h2>这是一个可点击的产品原型</h2><p class="subtext mt">所有账号、登录、签到、短信及通知操作均为模拟，只保存在当前浏览器。不会请求龙湖接口，也不会发送真实短信或通知。</p><div class="divider"></div><p class="subtext">一个程序类型可以有多个账号实例。每个实例拥有独立的计划、登录状态与运行记录。</p><div class="divider"></div><p class="subtext">界面参考 TDesign 与 WeUI 的设计体系，按多账号签到场景重新设计。</p></div><div class="card cell-group"><a class="cell" href="https://tdesign.tencent.com/miniprogram/" target="_blank" rel="noopener noreferrer"><span class="cell-main"><h3>TDesign 小程序设计参考</h3></span>${icon('arrow','chevron')}</a><a class="cell" href="https://github.com/Tencent/weui-wxss" target="_blank" rel="noopener noreferrer"><span class="cell-main"><h3>WeUI 微信原生体验参考</h3></span>${icon('arrow','chevron')}</a></div><button class="btn outline full mt" data-action="reset-demo">重置演示数据</button>`;}
  function render() {
    const {path,query:q}=route(),segments=path.split('/').filter(Boolean);let content='',header='每日签到',tab='home';
    if(path==='/home'||path==='/')content=homepage();
    else if(path==='/accounts'){header='我的签到';tab='accounts';content=accountsPage();}
    else if(segments[0]==='account') {tab='accounts';const a=getAccount(decodeURIComponent(segments[1]||''));header=segments[2]==='edit'?'修改配置':segments[2]==='auth'?'更新登录':'账号详情';content=a?(segments[2]==='edit'?editPage(a):segments[2]==='auth'?authPage(a):accountDetail(a)):empty('这个账号已移除','可以添加一个新账号，历史运行记录仍然保留。',navLink('/accounts','返回我的签到','btn primary'));}
    else if(segments[0]==='add'){tab='accounts';header='添加账号';content=addPage(path,q);}
    else if(path==='/records'){tab='records';header='运行记录';content=recordsPage(q);}
    else if(segments[0]==='run'){tab='records';header='执行详情';const r=state.runs.find(r=>r.id===decodeURIComponent(segments[1]||''));content=r?runPage(r):empty('记录不存在','演示数据可能已重置。',navLink('/records','返回记录','btn primary'));}
    else if(path==='/me'){tab='me';header='我的';content=mePage();}
    else if(segments[0]==='notifications'){tab='me';header='通知设置';content=segments[1]==='email'?emailPage():segments[1]==='wecom'?wecomPage():notificationsPage();}
    else if(path==='/help'){tab='me';header='操作手册';content=helpPage();}
    else if(path==='/video'){tab='me';header='视频教程';content=videoPage();}
    else if(path==='/about'){tab='me';header='关于';content=aboutPage();}
    else content=empty('页面走丢了','返回首页，继续查看你的签到。',navLink('/home','返回首页','btn primary'));
    main.innerHTML=content;document.getElementById('header-title').textContent=header;
    const back=document.getElementById('header-back');back.hidden=['/','/home','/accounts','/records','/me'].includes(path);back.innerHTML=icon('back');
    document.getElementById('tabbar').innerHTML=[['home','首页','home'],['accounts','我的签到','grid'],['records','运行记录','records'],['me','我的','user']].map(([id,label,i])=>`<a href="#/${id}" class="${tab===id?'active':''}" ${tab===id?'aria-current="page"':''}><span class="tab-icon">${icon(i)}</span>${label}</a>`).join('');
    document.title=`${header} · 每日签到原型`;
  }
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function error(form,text) {const el=form.querySelector('#form-message');if(el){el.className='form-error mt';el.textContent=text;el.scrollIntoView({block:'nearest',behavior:'smooth'});}}
  function formBusy(form,label) {const btn=form.querySelector('button[type=submit]');const original=btn.innerHTML;btn.disabled=true;btn.innerHTML=`${spinner}${label}`;form.setAttribute('aria-busy','true');return ()=>{btn.disabled=false;btn.innerHTML=original;form.removeAttribute('aria-busy');};}
  function sampleError(sample) {return sample==='missing'?'文件中没有找到成功登录响应。请从登录前开始抓包，完成登录后再导出对应端的 HAR。':sample==='expired'?'登录信息校验未通过：凭证已失效。请在对应端重新登录，再导出新的 HAR。':'';}
  function delivery(status,kind) {
    const n=state.notifications,allowed=kind==='check'?false:status==='signed'?n.events.success:n.events.failure;
    const channels=[n.wecom.bound&&n.wecom.enabled?'企业微信':null,n.email.enabled?'邮箱':null].filter(Boolean);
    return {channel:channels.join('、')||'未设置',state:!allowed?'none':!channels.length?'unconfigured':n.simulateFailure?'failed':'sent',attempts:allowed&&channels.length?1:0};
  }
  async function execute(id,kind) {
    const a=getAccount(id);if(!a||busy.has(id))return;busy.add(id);const gen=generation;
    render();toast(kind==='check'?'正在查询今日状态（演示）…':'正在签到并复核结果（演示）…');await sleep(950);
    if(gen!==generation||!getAccount(id)){busy.delete(id);return;}
    let status=a.authState==='expired'||a.scenario==='auth_expired'?'auth_expired':a.scenario==='unknown'?'unknown':kind==='check'?(accountState(a)==='signed'?'signed':'unsigned'):a.scenario==='failed'?'failed':'signed';
    if(status==='auth_expired')a.authState='expired';
    a.todayState=status==='auth_expired'?'unsigned':status;a.resultDate=today();
    const r={id:uid(),accountId:a.id,programTypeId:a.programTypeId,aliasSnapshot:a.alias,kind,state:status,message:messages[status],created:new Date().toISOString(),notification:delivery(status,kind)};
    state.runs.unshift(r);busy.delete(id);save();render();toast(status==='signed'?'已确认今日签到成功（演示）':status==='unsigned'?'复核完成：今日尚未签到（演示）':messages[status]);
  }
  document.addEventListener('submit',async e=>{
    const form=e.target;if(!(form instanceof HTMLFormElement))return;e.preventDefault();const data=new FormData(form),gen=generation;
    if(form.id==='add-bind') {if(!draft)return;const validating=draft;draft.alias=String(data.get('alias')).trim();if(!draft.alias){error(form,'请填写账号昵称。');return;}draft.validated=false;const restore=formBusy(form,'正在校验示例文件…');await sleep(700);if(gen!==generation||draft!==validating||!form.isConnected)return;restore();const failure=sampleError(data.get('sample'));if(failure){error(form,failure);return;}draft.validated=true;go('/add/verified');}
    else if(form.id==='add-plan'||form.id==='edit-account') {
      const alias=String(data.get('alias')).trim(),signTime=String(data.get('signTime')),checkTime=String(data.get('checkTime'));
      if(!alias){error(form,'请填写账号昵称。');return;}if(checkTime<=signTime){error(form,'请把复核时间设在签到时间之后。');return;}
      if(form.id==='add-plan') {if(!draft?.validated)return;const a={...draft,id:uid(),alias,signTime,checkTime,enabled:data.has('enabled'),authState:'valid',todayState:'unsigned',resultDate:today(),credentialUpdatedAt:new Date().toISOString(),scenario:'success'};delete a.validated;state.accounts.push(a);draft=null;save();go(`/add/done?id=${a.id}`);}
      else {const a=getAccount(form.dataset.id);if(!a)return;Object.assign(a,{alias,signTime,checkTime,enabled:data.has('enabled')});save();go(`/account/${a.id}`);toast('此账号的配置已保存');}
    }
    else if(form.id==='update-auth') {
      const a=getAccount(form.dataset.id);if(!a)return;const sms=method==='sms'&&program(a.programTypeId).kind==='app';
      if(sms&&(!smsSent||data.get('code')!=='123456')){error(form,smsSent?'演示验证码不正确，请输入 123456。原配置已保留。':'请先获取演示验证码。原配置已保留。');return;}
      const restore=formBusy(form,'正在校验登录…');await sleep(750);if(gen!==generation||!form.isConnected)return;restore();const failure=sms?'':sampleError(data.get('sample'));if(failure){error(form,`${failure} 原配置已保留。`);return;}
      if(!getAccount(a.id))return;a.authState='valid';a.credentialUpdatedAt=new Date().toISOString();if(['auth_expired','unknown'].includes(a.todayState))a.todayState='unsigned';a.scenario='success';save();go(`/account/${a.id}`);toast('此账号登录已更新，原签到计划保留（演示）');
    }
    else if(form.id==='notification-events'){state.notifications.events=Object.fromEntries(['success','failure'].map(key=>[key,data.has(key)]));save();toast('提醒偏好已保存');}
    else if(form.id==='email-settings') {
      const provider=String(data.get('provider')),address=String(data.get('address')).trim();if(!new RegExp(`^[^\\s@]+@${provider==='qq'?'qq':'163'}\\.com$`,'i').test(address)){error(form,`请填写与所选类型一致的 ${provider==='qq'?'QQ（@qq.com）':'163（@163.com）'} 邮箱。`);return;}
      state.notifications.email={provider,address,enabled:data.has('enabled')};save();toast('接收邮箱配置已保存（演示）');go('/notifications');
    }
  });
  document.addEventListener('click',async e=>{
    const btn=e.target.closest('[data-action]');if(!btn||btn.disabled)return;const action=btn.dataset.action,id=btn.dataset.id,a=getAccount(id);
    if(action==='filter-program'){filters.program=id;render();}
    else if(action==='toggle-account'&&a){a.enabled=!a.enabled;save();render();toast(a.enabled?'此账号的自动计划已开启':'此账号的自动计划已暂停');}
    else if(action==='choose-program'){const p=program(id);draft={programTypeId:id,alias:`${p.name} ${state.accounts.filter(a=>a.programTypeId===id).length+1}`,maskedIdentity:`178****${String(state.accounts.length+101).padStart(4,'0')}`,signTime:p.signTime,checkTime:p.checkTime,enabled:false,validated:false};go('/add/bind');}
    else if(action==='auth-method'){method=id;smsSent=false;render();}
    else if(action==='send-sms'){smsSent=true;btn.textContent='已发送（演示）';document.getElementById('sms-hint').textContent='演示验证码：123456。不发送真实短信。';toast('演示验证码为 123456，没有发送真实短信');}
    else if(action==='run'&&a)execute(id,'manual');
    else if(action==='check'&&a)execute(id,'check');
    else if(action==='delete-account'&&a)notifyModal('移除此账号？',`将移除“${program(a.programTypeId).name} · ${a.alias}”及其计划，其他账号不受影响。历史记录仍然保留。`,()=>{state.accounts=state.accounts.filter(x=>x.id!==id);save();go('/accounts');toast('账号已移除，历史记录保留');},'确认移除');
    else if(action==='cancel-dialog')dialogEl.close();
    else if(action==='confirm-dialog'){dialogEl.close();confirmCallback?.();confirmCallback=null;}
    else if(action==='clear-record-filters')go('/records');
    else if(action==='bind-wecom'){state.notifications.wecom={bound:true,enabled:true};save();render();toast('企业微信绑定已完成（演示）');}
    else if(action==='unbind-wecom')notifyModal('解除企业微信绑定？','解除后不再通过企业微信提醒。邮箱通知保持当前设置。',()=>{state.notifications.wecom={bound:false,enabled:false};save();render();toast('已解除演示绑定');},'解除绑定');
    else if(action==='test-notification'){
      const enabled=id==='email'?state.notifications.email.enabled:state.notifications.wecom.bound&&state.notifications.wecom.enabled;
      if(!enabled){toast('请先保存并开启此通知渠道');return;}const target=activeAccounts()[0]||state.accounts[0];if(!target){toast('请先添加一个账号，再体验通知');return;}
      btn.disabled=true;btn.innerHTML=spinner+'正在发送…';const gen=generation;await sleep(650);if(gen!==generation)return;
      const r={id:uid(),accountId:target.id,programTypeId:target.programTypeId,aliasSnapshot:target.alias,kind:'afternoon',state:accountState(target),message:messages[accountState(target)],created:new Date().toISOString(),notification:{channel:id==='email'?'邮箱':'企业微信',state:state.notifications.simulateFailure?'failed':'sent',attempts:1}};state.runs.unshift(r);save();go(`/run/${r.id}`);toast('演示通知结果已生成，没有发送真实消息');
    }
    else if(action==='retry-mail'){
      const r=state.runs.find(r=>r.id===id);if(!r||busy.has(id))return;busy.add(id);render();const gen=generation;await sleep(650);if(gen!==generation)return;busy.delete(id);r.notification.attempts++;r.notification.state=state.notifications.simulateFailure?'failed':'sent';save();render();toast(r.notification.state==='sent'?'演示通知重试成功':'仍在模拟服务失败，可在通知设置中关闭');
    }
    else if(action==='empty-demo'||action==='reset-demo')notifyModal(action==='reset-demo'?'恢复默认演示？':'体验首次使用？',action==='reset-demo'?'将恢复预置的五个虚拟账号、计划和记录。当前浏览器中的原型修改会被清除。':'将清空原型中的账号和记录，展示首次进入时的空页面。真实平台数据不受影响。',()=>{generation++;busy.clear();draft=null;state=seed();if(action==='empty-demo'){state.accounts=[];state.runs=[];state.notifications.email.enabled=false;}filters={program:'all',status:'all',search:''};save();go('/home');toast(action==='empty-demo'?'已切换到首次使用状态':'已恢复默认演示');},action==='reset-demo'?'恢复默认':'清空演示');
  });
  document.addEventListener('change',e=>{
    const el=e.target;
    if(el.id==='account-status-filter'){filters.status=el.value;render();}
    else if(el.closest('#record-filters')){const d=new FormData(el.closest('form')),q=new URLSearchParams();for(const [k,v]of d)if(v&&v!=='all')q.set(k,v);go('/records'+(q.size?'?'+q.toString():''));}
    else if(el.id==='scenario-select'){const a=getAccount(el.dataset.id);if(a){a.scenario=el.value;save();toast('已设置此账号的模拟结果');}}
    else if(el.id==='simulate-notify-failure'){state.notifications.simulateFailure=el.checked;save();toast(el.checked?'已开启通知失败演示':'已关闭通知失败演示');}
    else if(el.id==='wecom-enabled'){state.notifications.wecom.enabled=el.checked;save();toast(el.checked?'企业微信通知已开启（演示）':'企业微信通知已暂停（演示）');}
  });
  document.addEventListener('input',e=>{if(e.target.id==='account-search'){filters.search=e.target.value;const filtered=filteredAccounts();document.getElementById('account-list').innerHTML=accountListMarkup(filtered);main.querySelector('.count-label').textContent=`共 ${filtered.length} 个账号`;}});
  document.getElementById('header-back').addEventListener('click',()=>{const {path,query}=route();if(path==='/add/bind')go('/add/program');else if(path==='/add/verified')go('/add/bind');else if(path==='/add/plan')go('/add/verified');else if(path.startsWith('/add'))go('/accounts');else if(/^\/account\/[^/]+\//.test(path))go('/account/'+path.split('/')[2]);else if(path.startsWith('/account'))go('/accounts');else if(path.startsWith('/run'))go('/records');else if(path==='/notifications')go('/me');else if(path.startsWith('/notifications/'))go('/notifications');else go('/me');});
  window.addEventListener('hashchange',()=>{dialogEl.close();const {path}=route();if(path.endsWith('/auth')){method='import';smsSent=false;}render();window.scrollTo(0,0);main.focus({preventScroll:true});});
  if(!location.hash)history.replaceState(null,'','#/home');
  save();
  render();
})();
