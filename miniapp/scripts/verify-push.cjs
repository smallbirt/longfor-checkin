/* Deterministic checks of the production subscription helper. No real WeChat calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const esbuild = require('esbuild');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'checkin-push-test-'));
const intent = { nonce: 'server-issued-nonce', template_id: 'chosen-template', account_id: '*', scope: 'user', appid: 'wx5442ffd1e656a143' };
global.__pushCalls = [];
global.__pushResult = 'accept';
global.__pushIntent = intent;
(async () => {
  try {
    await esbuild.build({ entryPoints: [path.resolve(__dirname, '../src-checkin/shared/push.ts')], outfile: path.join(directory, 'push.cjs'), bundle: true, platform: 'node', format: 'cjs', plugins: [{ name: 'local-fakes', setup(build) {
      build.onResolve({ filter: /^@tarojs\/taro$/ }, () => ({ path: 'taro', namespace: 'fake' }));
      build.onResolve({ filter: /^\.\/api$/ }, () => ({ path: 'api', namespace: 'fake' }));
      build.onLoad({ filter: /.*/, namespace: 'fake' }, args => ({ contents: args.path === 'taro' ? `
        export default {
          login: async () => { global.__pushCalls.push('login'); return {code:'fresh-code'}; },
          requestSubscribeMessage: options => {
            global.__pushCalls.push({native:options});
            if(global.__pushResult==='throw') throw Error('native unavailable');
            return Promise.resolve({[global.__pushIntent.template_id]:global.__pushResult});
          }
        };` : `export async function api(path,payload){global.__pushCalls.push({path,payload});if(global.__pushSaveFails&&path.endsWith('/subscribe'))throw Error('offline');return path.endsWith('/prepare')?global.__pushIntent:{message:'saved'};}` }));
    } }] });
    const client = require(path.join(directory, 'push.cjs'));
    const prepared = await client.prepareSubscription();
    assert.deepEqual(prepared, intent);
    assert.deepEqual(global.__pushCalls, ['login', { path: '/api/mini-push/prepare', payload: { code: 'fresh-code' } }]);
    global.__pushCalls = [];
    const native = client.requestSubscription(intent);
    assert.equal(global.__pushCalls.length, 1, 'native authorization runs synchronously inside tap');
    assert.deepEqual(global.__pushCalls[0], { native: { tmplIds: [intent.template_id] } });
    assert.equal(await native, intent.nonce);
    assert.equal(global.__pushCalls.length, 1, 'native success alone does not forge a saved server subscription');
    await client.saveSubscription(intent.nonce);
    assert.deepEqual(global.__pushCalls[1], { path: '/api/mini-push/subscribe', payload: { nonce: intent.nonce, accepted: true } });
    for (const result of ['reject', 'ban', undefined, 'throw']) {
      global.__pushResult = result; global.__pushCalls = [];
      await assert.rejects(client.requestSubscription(intent));
      assert.equal(global.__pushCalls.length, 1, 'rejected permissions do not reach the server');
    }
    global.__pushResult = 'accept'; global.__pushCalls = [];
    const nonce = await client.requestSubscription(intent);
    global.__pushSaveFails = true;
    await assert.rejects(client.saveSubscription(nonce));
    global.__pushSaveFails = false;
    await client.saveSubscription(nonce);
    assert.equal(global.__pushCalls.filter(c => c.native).length, 1, 'save retry does not request another native permission');
    assert.deepEqual(global.__pushCalls.filter(c => c.path).map(c => c.payload.nonce), [nonce, nonce]);
    console.log('Native push checks passed: tap ordering, WeChat identity exchange, accepted/rejected consent, same-nonce save retry.');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
