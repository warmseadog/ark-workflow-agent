(() => {
  'use strict';
  const byId=id=>document.getElementById(id),admin=document.body.dataset.supportAdmin==='true';
  const form=byId('support-form'),status=byId('support-status'),history=byId('support-history');
  const node=(tag,text)=>{const el=document.createElement(tag);if(text)el.textContent=text;return el;};
  let page=1,pages=1,busy=false;
  const endpoint=admin?'/api/admin/support/requests':'/api/support/requests';
  async function api(path,method='GET',body){return window.accountUI.readJSON(await fetch(path,{method,cache:'no-store',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined}));}
  function itemCard(item){
    const card=node('article');card.className='support-ticket';card.dataset.supportId=item.id;
    const date=new Date(item.created_at),heading=node('h3',(item.status==='resolved'?'已处理':'待处理')+' · '+(item.task_name||'使用反馈'));
    card.append(heading,node('p',Number.isNaN(date.getTime())?'':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）'));
    if(admin && item.username)card.append(node('p','提交人：'+item.username));
    const message=node('p',item.message);message.className='support-ticket-message';card.append(message);
    const id=window.supportUI.safeId(item.request_id);if(id)card.append(node('p','问题编号：'+id));
    if(item.reply){const reply=node('div');reply.className='support-reply';reply.append(node('strong','管理员回复'),node('p',item.reply));card.append(reply);}
    if(admin){
      const edit=node('form'),replyLabel=node('label','管理员回复'),reply=node('textarea');reply.id='reply-'+item.id;replyLabel.htmlFor=reply.id;reply.rows=4;reply.maxLength=4000;reply.value=item.reply||'';
      const stateLabel=node('label','处理状态'),select=node('select');select.id='state-'+item.id;stateLabel.htmlFor=select.id;select.append(new Option('待处理','open'),new Option('已处理','resolved'));select.value=item.status;
      const save=node('button','保存回复');save.type='submit';save.className='support-button support-primary';
      edit.append(replyLabel,reply,stateLabel,select,save);card.append(edit);
      edit.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;status.textContent='正在保存…';try{await api(endpoint+'/'+encodeURIComponent(item.id),'PATCH',{reply:reply.value,status:select.value});await refresh();status.textContent='回复与处理状态已保存，用户可在“我的反馈”中查看。';}catch(error){status.textContent=error.message;}finally{save.disabled=false;}});
    }
    return card;
  }
  async function refresh(target=page){
    if(busy)return;busy=true;byId('support-previous').disabled=byId('support-next').disabled=true;
    try{const data=await api(endpoint+'?page='+target+'&page_size=10'+(admin&&byId('support-filter').value?'&status='+byId('support-filter').value:''));page=data.page||target;pages=data.pages||1;history.replaceChildren(...data.items.map(itemCard));if(!data.items.length)history.append(node('p','暂无反馈。'));byId('support-page-number').textContent=page+' / '+pages;}
    catch(error){status.textContent='反馈读取失败：'+error.message;}
    finally{busy=false;byId('support-previous').disabled=page<=1;byId('support-next').disabled=page>=pages;}
  }
  if(form){
    window.supportUI.getConfig().then(config=>{
      const limits=config.limits||{};
      for(const [id,key] of [['support-message','message_max_chars'],['support-task-name','task_name_max_chars'],['support-request-id','request_id_max_chars']])if(Number.isInteger(limits[key])&&limits[key]>0)byId(id).maxLength=limits[key];
      byId('support-form-limit').textContent=[limits.message_max_chars?'问题描述最多 '+limits.message_max_chars+' 字。':'',limits.open_requests_max?'每个账户最多保留 '+limits.open_requests_max+' 条待处理反馈，请勿重复提交同一问题。':''].filter(Boolean).join(' ');
    }).catch(()=>{byId('support-form-limit').textContent='当前反馈限制暂未读取，请稍后重试。';});
    const query=new URLSearchParams(location.search);
    byId('support-task-name').value=(query.get('task_name')||'').slice(0,160);
    byId('support-request-id').value=window.supportUI.safeId(query.get('problem_id'));
    const timestamp=query.get('occurred_at'),task=window.supportUI.safeId(query.get('task_id'));
    if(timestamp && /^\d{4}-\d{2}-\d{2}T[\d:.+Z-]+$/.test(timestamp))byId('support-message').value='发生时间：'+timestamp.slice(0,40)+'\n';
    if(task)byId('support-message').value+='任务编号：'+task+'\n';
    form.addEventListener('submit',async event=>{
      event.preventDefault();const submit=form.querySelector('[type=submit]');if(submit.disabled)return;
      const requestId=byId('support-request-id').value.trim();
      if(requestId && !window.supportUI.safeId(requestId)){status.textContent='问题编号格式不正确，请复制错误提示中的编号，或留空。';return;}
      submit.disabled=true;status.textContent='正在提交反馈…';
      try{await api(endpoint,'POST',{message:byId('support-message').value.trim(),task_name:byId('support-task-name').value.trim(),request_id:requestId});byId('support-message').value='';await refresh(1);status.textContent='反馈已提交，请在“我的反馈”查看管理员回复。';}
      catch(error){status.textContent='反馈未提交：'+error.message;}
      finally{submit.disabled=false;}
    });
  }
  byId('support-refresh').addEventListener('click',()=>refresh());
  byId('support-previous').addEventListener('click',()=>refresh(page-1));byId('support-next').addEventListener('click',()=>refresh(page+1));
  byId('support-filter')?.addEventListener('change',()=>refresh(1));
  window.accountReady.then(async account=>{
    if(!account.auth_enabled){
      const config=await window.supportUI.getConfig();
      if(config.support?.login_required!==false){if(!admin)byId('support-unavailable').hidden=false;return;}
      byId('support-local').hidden=false;
    }else{
      if(!account.user){if(!admin)byId('support-login').hidden=false;return;}
      if(admin&&account.user.role!=='admin'){status.textContent='仅管理员可以处理反馈。';return;}
    }
    if(form)form.hidden=false;byId('support-history-section').hidden=false;await refresh();
  }).catch(error=>{status.textContent=error.message;});
})();
