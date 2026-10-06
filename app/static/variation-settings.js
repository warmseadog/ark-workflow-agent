(() => {
  'use strict';
  const form=document.getElementById('variation-form'); if(!form)return;
  const fields=form.querySelector('fieldset'), status=document.getElementById('variation-status'), retry=document.getElementById('variation-reload');
  const note=(text,error=false)=>{status.textContent=text;status.hidden=!text;status.dataset.error=String(error);};
  async function request(method='GET',values){
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch('/api/variation-settings',{method,cache:'no-store',signal:controller.signal,headers:values?{'Content-Type':'application/json'}:{},body:values?JSON.stringify(values):undefined});
      const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'配置请求失败');return data.config;
    } finally {clearTimeout(timer);}
  }
  function fill(c){
    for(const key of ['model','base_url','timeout_seconds','template','skill'])form.elements[key].value=c[key];
    form.elements.enabled.checked=c.enabled;form.elements.api_key.value='';form.elements.clear_api_key.checked=false;
    document.getElementById('variation-key-status').textContent=c.has_api_key?'已保存':'未配置';
    document.getElementById('variation-skill-version').textContent=c.skill_version.slice(0,12);
  }
  async function load(){
    retry.hidden=true;fields.disabled=true;
    try{fill(await request());fields.disabled=false;note('');}
    catch(e){note('无法加载换拍法配置，请重试。',true);retry.hidden=false;}
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(fields.disabled||!form.reportValidity())return;
    const values=Object.fromEntries(new FormData(form));values.enabled=form.elements.enabled.checked;values.clear_api_key=form.elements.clear_api_key.checked;values.timeout_seconds=Number(values.timeout_seconds);
    fields.disabled=true;note('正在保存…');
    try{const c=await request('PUT',values);fill(c);note('换拍法配置已保存，用于新任务。'+c.problem);}
    catch(e){note(e.name==='AbortError'?'保存超时，请重新加载核对结果。':e.message,true);}
    finally{fields.disabled=false;}
  });
  retry.addEventListener('click',load);
  window.VariationSettings={load};
  if(document.body.classList.contains('operations-page'))void load();
})();
