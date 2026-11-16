/* Tests the exact native transport against the isolated preview service.
 * No real user credentials, SMS, email, or native check-in requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const esbuild = require('esbuild');
const project = path.resolve(__dirname, '..');
const base = process.env.CHECKIN_TEST_URL || 'http://127.0.0.1:18091';
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw Error('Tests require an isolated localhost preview.');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'checkin-native-test-'));
const mock = `
const fs = require('node:fs');
const storage = new Map();
module.exports = {
 getStorageSync: k => storage.get(k), setStorageSync: (k, v) => storage.set(k, structuredClone(v)),
 getAccountInfoSync: () => ({miniProgram:{envVersion:global.__nativeEnv || 'develop'}}),
 request: async o => {
  global.__nativeCalls=(global.__nativeCalls||0)+1;
  if(global.__nativeFail) throw Error('deliberate transport failure');
  const r = await fetch(o.url, {method:o.method,headers:o.header,body:o.method==='POST'?JSON.stringify(o.data):undefined});
  return {statusCode:r.status,data:await r.text(),cookies:r.headers.getSetCookie(),header:Object.fromEntries(r.headers)};
 },
 uploadFile: async o => {
  const form=new FormData(); for(const [k,v] of Object.entries(o.formData)) form.set(k,v);
  form.set(o.name,new Blob([fs.readFileSync(o.filePath)]), 'example.har');
  const r=await fetch(o.url,{method:'POST',headers:o.header,body:form});
  return {statusCode:r.status,data:await r.text(),header:Object.fromEntries(r.headers)};
 }, reLaunch:o=>o.complete?.()
};`;
fs.writeFileSync(path.join(directory, 'taro.cjs'), mock);
const checks=[];
function check(v,label){assert.ok(v,label);checks.push(label);}
(async()=>{
 await esbuild.build({entryPoints:[path.join(project,'src-checkin/shared/api.ts')],outfile:path.join(directory,'client.cjs'),bundle:true,platform:'node',format:'cjs',define:{__CHECKIN_API_BASE_URL__:JSON.stringify(base)},plugins:[{name:'native-mock',setup(build){build.onResolve({filter:/^@tarojs\/taro$/},()=>({path:path.join(directory,'taro.cjs')}));}}]});
 const client=require(path.join(directory,'client.cjs'));
 const first=await client.session(); check(first.preview===true,'isolated simulated backend confirmed');
 await assert.rejects(()=>client.login('demo','wrong')); checks.push('invalid password rejects');
 await client.login('demo','demo-password-2026');
 const original=await client.state(); check(original.user.username==='demo','existing Web platform account logs in through native transport');
 const created=[];
 for(const alias of ['Native 主号','Native 家人号']) created.push((await client.api('/api/accounts',{channel:'app',alias})).account);
 check(created[0].id!==created[1].id,'two instances of same program have independent identifiers');
 await client.api('/api/accounts/'+created[0].id,{alias:'Native 修改后的主号',sign_time:'10:25',check_time:'18:10'});
 const current=await client.state();
 check(current.accounts.find(a=>a.id===created[1].id).sign_time==='09:10','editing first account leaves second plan unchanged');
 await assert.rejects(()=>client.api('/api/accounts/'+created[0].id,{enabled:true})); checks.push('unbound account cannot enable schedule');
 await assert.rejects(()=>client.api('/api/accounts/'+created[0].id,{check_time:'08:00'})); checks.push('invalid review time rejected');
 // HAR fixtures come from the backend public test helpers, supplied by the caller.
 const good=process.env.CHECKIN_TEST_HAR;
 if(!good) throw Error('Set CHECKIN_TEST_HAR to a fake fixture from backend tests.');
 await client.upload(created[0].id,'app',good);
 check((await client.state()).accounts.find(a=>a.id===created[0].id).configured,'native multipart upload validates and saves selected HAR');
 check(!(await client.state()).accounts.find(a=>a.id===created[1].id).configured,'second same-type account credential unchanged');
 await assert.rejects(()=>client.upload(created[0].id,'mini',good)); checks.push('wrong program upload rejected without overwriting');
 await client.api('/api/accounts/'+created[0].id,{enabled:true});
 check((await client.state()).accounts.find(a=>a.id===created[0].id).enabled,'verified account can explicitly enable independent schedule');
 const prefs=original.notification_events;
 await client.api('/api/notifications',{success:false,failure:true,afternoon:true});
 check((await client.state()).notification_events.success===false,'notification preferences persist in shared backend');
 await client.api('/api/notifications',prefs);
 await client.api('/api/run',{kind:'manual',account_id:created[0].id});
 for(let n=0;n<20;n++){const s=await client.state(created[0].id);if(!s.running)break;await new Promise(r=>setTimeout(r,100));}
 const signed=await client.state(created[0].id);
 check(signed.accounts.find(a=>a.id===created[0].id).status.state==='signed','native manual execution updates selected account');
 const history=await client.api('/api/jobs?account_id='+created[0].id+'&state=signed');
 check(history.jobs.length && history.jobs.every(j=>j.results.every(r=>r.account_id===created[0].id)),'history filters by exact account instance');
 const foreign=created[0].id;
 await client.api('/api/session/logout',{});
 const name='native_'+Date.now().toString(36);
 await client.login(name,'native-test-password',true);
 check((await client.state()).accounts.length===0,'new platform user has isolated empty workspace');
 await assert.rejects(()=>client.state(foreign));checks.push('foreign account cannot be accessed by native client');
 await client.api('/api/session/logout',{});await client.login('demo','demo-password-2026');
 for(const a of created) await client.api('/api/accounts/'+a.id+'/remove',{});
 check((await client.state()).accounts.length===original.accounts.length,'test accounts removed without touching original five');
 const start=global.__nativeCalls;global.__nativeFail=true;
 await assert.rejects(()=>client.api('/api/run',{kind:'manual'}));
 check(global.__nativeCalls===start+1,'uncertain mutation is never automatically retried');global.__nativeFail=false;
 global.__nativeEnv='release';const before=global.__nativeCalls;
 await assert.rejects(()=>client.state(),/HTTPS/);check(before===global.__nativeCalls,'release build blocks insecure HTTP before sending credentials');
 console.log(JSON.stringify({passed:checks.length,checks},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>fs.rmSync(directory,{recursive:true,force:true}));
