// Runs in the dedicated SDK page. The SDK is pinned; its relay forwards no cookies.
(async()=>{
 const config=document.body.dataset;
 try{
  if(!window._dx||typeof window._dx.ConstID!=='function')throw Error();
  const token=await window._dx.ConstID({appId:config.appId,server:config.server});
  if(typeof token!=='string'||!/^[A-Za-z0-9+/=_-]{40}$/.test(token))throw Error();
  parent.postMessage({type:'device-ready',nonce:config.nonce,token},config.parent);
 }catch{
  parent.postMessage({type:'device-error',nonce:config.nonce},config.parent);
 }
})();
