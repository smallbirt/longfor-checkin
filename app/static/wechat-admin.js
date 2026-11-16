(() => {
  const form = document.getElementById('wechat-login-form');
  if (!form) return;
  const field = id => document.getElementById('wechat-login-' + id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  async function call(data) {
    const response = await fetch('/api/admin/wechat-login', data ? {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(data)
    } : {});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '微信登录配置读取失败。');
    return result;
  }
  function render(value) {
    field('appid').value = value.appid;
    field('enabled').checked = value.enabled;
    field('secret').value = '';
    field('status').textContent = value.enabled ? '微信登录已开启，首次使用需绑定本站账号。' :
      value.configured ? '密钥已保存，微信登录暂未开启。' : value.can_reuse_push_secret ?
      '可复用同一小程序的推送密钥，勾选开关并保存即可开启。' : '尚未配置，请填写小程序 AppID 和 AppSecret。';
  }
  call().then(render).catch(error => {field('status').textContent = error.message;});
  form.addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter;
    button.disabled = true; field('feedback').textContent = '';
    try {
      const result = await call({appid: field('appid').value.trim(), secret: field('secret').value, enabled: field('enabled').checked});
      render(result); field('feedback').textContent = result.message;
    } catch (error) { field('feedback').textContent = error.message; }
    finally { button.disabled = false; }
  });
})();
