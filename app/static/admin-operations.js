(() => {
  const $=id=>document.getElementById(id), select=$('operations-template'), form=$('operations-template-form');
  const status=$('operations-template-status'), name=$('operations-template-name'), content=$('operations-template-content');
  let items=[], selected=null, version='exclusive-v2', busy=false;
  let baseline={name:'',content:''};
  const dirty=()=>name.value!==baseline.name || content.value!==baseline.content;
  const mayDiscard=()=>!dirty() || confirm('当前模板有未保存的修改，确定放弃这些修改？');
  function setBusy(value) {
    busy=value;form.setAttribute('aria-busy',String(value));
    form.querySelectorAll('button,input,textarea').forEach(control=>control.disabled=value);
    select.disabled=value;$('operations-template-delete').disabled=value || !selected;
  }
  async function request(url,method='GET',payload) {
    return window.accountUI.readJSON(await fetch(url,{method,headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined}));
  }
  function apply() {
    const item=items.find(item=>item.id===select.value); selected=item?.id || null;
    name.value=item?.name || '';content.value=item?.content || '';version=item?.rule_version || version;
    const labels={'legacy-v1':'早期素材联动','exclusive-v2':'独立参考优先','yoyo-v3':'独立参考优先，其余配饰跟随穿搭图'};
    $('operations-template-rule').textContent='规则：'+(labels[version] || version)+(item?.is_default?' · 当前默认，直接保存影响之后新建草稿':'');
    baseline={name:name.value,content:content.value};
    $('operations-template-delete').disabled=busy || !selected;
  }
  async function load(preferred=selected) {
    const data=await request('/api/admin/prompt-templates'); items=data.items;
    select.replaceChildren(...items.map(item=>new Option(item.name+(item.is_default?'（当前默认）':''),item.id)));
    if(items.some(item=>item.id===preferred))select.value=preferred;
    apply();
  }
  async function operate(action) {
    if(busy)return;
    setBusy(true);
    try {await action();status.textContent='已保存';}
    catch(error){status.textContent=error.message;}
    finally {setBusy(false);}
  }
  function save(copy) {
    if(busy || !form.reportValidity())return;
    return operate(async()=>{
      const values={name:name.value.trim(),content:content.value.trim(),rule_version:version};
      if (!copy && selected) {
        const latest=(await request('/api/admin/prompt-templates')).items.find(x=>x.id===selected);
        if(!latest)throw new Error('模板已删除，请重新选择；编辑内容仍保留。');
        for(const key of ['name','content']){
          if(values[key]===baseline[key])values[key]=latest[key];
          else if(latest[key]!==baseline[key]&&values[key]!==latest[key])throw new Error('模板已在其他编辑入口修改，请核对后重新选择；编辑内容仍保留。');
        }
      }
      const item=await request('/api/admin/prompt-templates'+(!copy&&selected?'/'+selected:''),!copy&&selected?'PUT':'POST',
        values);
      await load(item.id);
      await loadMechanism();
    });
  }
  form.addEventListener('submit',event=>{event.preventDefault();save(false);});
  select.addEventListener('change',()=>{
    if(busy || !mayDiscard()){select.value=selected || '';return;}
    apply();status.textContent='';
  });
  $('operations-template-copy').addEventListener('click',()=>save(true));
  $('operations-template-new').addEventListener('click',()=>{
    if(busy || !mayDiscard())return;
    selected=null;select.selectedIndex=-1;apply();status.textContent='';name.focus();
  });
  $('operations-template-delete').addEventListener('click',()=>{
    if(!busy && selected && confirm('确认删除这个共享模板？未保存的编辑将丢弃，现有草稿和任务保持不变。'))operate(async()=>{await request('/api/admin/prompt-templates/'+selected,'DELETE');await load(null);await loadMechanism();});
  });
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
  setBusy(true);
  window.accountReady.then(()=>load()).catch(error=>status.textContent=error.message).finally(()=>setBusy(false));
  async function loadMechanism() {
    const node=$('mechanism-load-status'); node.textContent='正在读取提示词机制…';
    try { await window.PromptMechanism.load(); await window.PromptWorkbench.load(); node.textContent=''; }
    catch(error) {
      node.textContent=error.message+' ';
      const retry=document.createElement('button');retry.type='button';retry.textContent='重试加载';
      retry.addEventListener('click',()=>void loadMechanism());node.append(retry);
    }
  }
  window.accountReady.then(loadMechanism).catch(error=>$('mechanism-load-status').textContent=error.message);
})();
