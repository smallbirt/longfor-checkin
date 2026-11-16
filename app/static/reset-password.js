(() => {
  const form=document.getElementById('reset-form'),feedback=document.getElementById('reset-feedback'),button=document.getElementById('reset-submit'),token=location.hash.slice(1),csrf=document.querySelector('meta[name=csrf-token]').content;
  // Keep the bearer token out of request URLs, referrers and subsequent browser history.
  history.replaceState(null,'','/reset-password');
  if(!token){feedback.textContent='重置链接无效，请联系管理员重新生成。';button.disabled=true;}
  form.addEventListener('submit',async e=>{e.preventDefault();if(button.disabled)return;button.disabled=true;feedback.textContent='正在保存…';try{const fields=new FormData(form),response=await fetch('/api/reset-password',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({token,password:fields.get('password'),confirm_password:fields.get('confirm_password')})}),value=await response.json();if(!response.ok)throw Error(value.error||'重置失败。');form.reset();feedback.textContent=value.message;form.querySelectorAll('input').forEach(i=>i.disabled=true);}catch(error){feedback.textContent=error.message;button.disabled=false;}});
})();
