/* Run only against scripts/preview_wechat.py: isolated data and fake WeChat exchange. */
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const base=process.env.CHECKIN_TEST_URL||'http://127.0.0.1:18098';
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base))throw Error('Only isolated localhost preview is permitted');
const project=path.resolve(__dirname,'..');
const esbuild = require('esbuild');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'checkin-wechat-test-'));
fs.writeFileSync(path.join(dir,'taro.cjs'),`
const storage=new Map(); let code=0;
module.exports={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,structuredClone(v)),
getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),login:async()=>({code:'native-test-'+Date.now()+'-'+(++code)}),
request:async o=>{const r=await fetch(o.url,{method:o.method,headers:o.header,body:o.method==='POST'?JSON.stringify(o.data):undefined});return{statusCode:r.status,data:await r.text(),cookies:r.headers.getSetCookie(),header:Object.fromEntries(r.headers)};},reLaunch:o=>o.complete?.()};`);
(async()=>{
await esbuild.build({entryPoints:[path.join(project,'src-checkin/shared/api.ts')],outfile:path.join(dir,'api.cjs'),bundle:true,platform:'node',format:'cjs',define:{__CHECKIN_API_BASE_URL__:JSON.stringify(base)},plugins:[{name:'native-fake',setup(b){b.onResolve({filter:/^@tarojs\/taro$/},()=>({path:path.join(dir,'taro.cjs')}));}}]});
const api=require(path.join(dir,'api.cjs'));
assert.equal((await api.session()).preview,true);
const first=await api.wechatLogin();assert.equal(first.bind_required,true);assert.ok(first.ticket);
await assert.rejects(()=>api.bindWechatLogin(first.ticket,'demo','wrong'));
assert.equal((await api.session()).user,null);
const before=(await api.session()).csrf;
const bound=await api.bindWechatLogin(first.ticket,'demo','demo-password-2026');
assert.equal(bound.user.username,'demo');assert.notEqual(bound.csrf,before);
assert.equal((await api.state()).user.username,'demo');
assert.equal((await api.api('/api/credits')).wallet.points,20);
assert.equal((await api.api('/api/wechat/binding')).bound,true);
await api.api('/api/session/logout',{});
const next=await api.wechatLogin();assert.equal(next.bind_required,false);assert.equal(next.user.username,'demo');
await api.api('/api/wechat/unbind',{password:'demo-password-2026'});
assert.equal((await api.api('/api/wechat/binding')).bound,false);
assert.equal((await api.state()).user.username,'demo');
await api.bindCurrentWechat('demo-password-2026');
assert.equal((await api.api('/api/wechat/binding')).bound,true);
assert.equal((await api.api('/api/credits')).wallet.points,20);
console.log('Native WeChat flow passed: first bind, password rejection, cookie/CSRF rotation, quick login, unbind, rebind and existing wallet preservation. Fake WeChat exchange only.');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(dir,{recursive:true,force:true}));
