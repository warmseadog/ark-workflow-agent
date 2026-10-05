/* Shared support context. Never put raw API errors, URLs or credentials in feedback links. */
(() => {
  'use strict';
  const node = (tag, text) => { const el=document.createElement(tag); if(text)el.textContent=text; return el; };
  const safeId = value => typeof value==='string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(value) ? value : '';
  function helpLink(context={}) {
    const query=new URLSearchParams();
    const values={task_id:context.id,task_name:context.name,occurred_at:context.created_at,problem_id:safeId(context.request_id)};
    for(const [key,value] of Object.entries(values))if(typeof value==='string'&&value)query.set(key,value.slice(0,key==='task_name'?160:80));
    return '/help'+(query.size?'?'+query:'')+'#feedback';
  }
  function appendContext(holder, context={}) {
    const id=safeId(context.request_id),tools=node('div');tools.className='support-context';
    if(id){
      const label=node('span','问题编号：'+id),copy=node('button','复制问题编号');copy.type='button';
      copy.addEventListener('click',async()=>{
        try { await navigator.clipboard.writeText(id); copy.textContent='已复制问题编号'; }
        catch (_) { copy.textContent='请长按或选中编号复制'; }
      });
      tools.append(label,copy);
    }
    const link=node('a','联系管理员');link.href=helpLink(context);tools.append(link);holder.append(tools);
  }
  function reportProblem(id) {
    if(!safeId(id))return;
    const show=()=>{
      let panel=document.querySelector('[data-support-problem]');
      if(!panel){panel=node('aside');panel.dataset.supportProblem='';panel.className='support-problem';panel.setAttribute('role','status');}
      (document.querySelector('dialog[open]')||document.body).append(panel);
      panel.replaceChildren(node('p','请求未完成。重试后仍有问题，可将问题编号交给管理员。'));
      appendContext(panel,{request_id:id,created_at:new Date().toISOString()});
      const close=node('button','关闭提示');close.type='button';close.addEventListener('click',()=>panel.remove());panel.append(close);
    };
    if(document.body)show();else document.addEventListener('DOMContentLoaded',show,{once:true});
  }
  let configRequest=null;
  function getConfig(){
    if(!configRequest)configRequest=fetch('/api/support/config',{cache:'no-store'}).then(async response=>{if(!response.ok)throw new Error('要求读取失败');return response.json();}).catch(error=>{configRequest=null;throw error;});
    return configRequest;
  }
  async function renderRequirements(root=document){
    const hints=[...root.querySelectorAll('[data-input-requirement]')],billing=[...root.querySelectorAll('[data-billing-notice]')];
    if(!hints.length&&!billing.length)return;
    try{
      const config=await getConfig();
      for(const hint of hints)hint.textContent=config.input_requirements?.[hint.dataset.inputRequirement]?.text||'当前要求未读取，请到帮助页重新读取后选择素材。';
      for(const input of root.querySelectorAll('[data-requirement-input]')){const accept=config.input_requirements?.[input.dataset.requirementInput]?.accept;if(accept)input.accept=accept;}
      for(const note of billing)note.textContent=config.billing_notice||'费用规则暂未读取，请先向管理员确认。';
    }catch(_){for(const hint of hints)hint.textContent='当前要求读取失败，请在帮助页重试。';for(const note of billing)note.textContent='费用说明暂未读取，请先向管理员确认。';}
  }
  window.supportUI={safeId,helpLink,appendContext,reportProblem,getConfig,renderRequirements};
  document.addEventListener('DOMContentLoaded',()=>{
    renderRequirements();
    for(const button of document.querySelectorAll('[data-reload-support-config]'))button.addEventListener('click',()=>{configRequest=null;renderRequirements();});
  });
  for(const id of window.pendingSupportProblems||[])reportProblem(id);
  window.pendingSupportProblems=[];
})();
