// Visual-only preview. Original production scripts are not loaded.
(()=>{
 const dialog=document.querySelector('#mira-preview-info');
 let restore;
 function explain(name){restore=document.activeElement;document.querySelector('#mira-preview-detail').textContent=`「${name||'这项功能'}」的生产服务尚未接入。本页只预览现有布局和统一后的 mira 样式，不上传、不生成、不修改账号。`;dialog.showModal();}
 document.addEventListener('submit',e=>{e.preventDefault();explain('提交')});
 document.addEventListener('click',e=>{
  const el=e.target.closest('a,button,input[type=file]');if(!el)return;
  if(el.id==='mira-preview-close'){dialog.close();return;}
  if(el.closest('#mira-preview-info'))return;
  if(el.matches('a[href="mira-cover.html"],a[href="mira-login.html"]'))return;
  if(el.classList.contains('workspace-toggle')||el.classList.contains('workspace-backdrop')){const open=el.classList.contains('workspace-backdrop')?false:!document.body.hasAttribute('data-sidebar-open');document.body.toggleAttribute('data-sidebar-open',open);document.querySelector('.workspace-toggle').setAttribute('aria-expanded',String(open));document.querySelector('.workspace-backdrop').hidden=!open;return;}
  e.preventDefault();explain(el.textContent.trim()||el.getAttribute('aria-label')||'选择素材');
 },true);
 dialog.addEventListener('close',()=>restore?.focus());
 const header=document.querySelector('.production-header');
 if(header){const label=document.createElement('a');label.href='mira-login.html';label.className='local-label';label.textContent='mira 本地视觉预览 · 返回登录页';header.replaceChildren(label);}
 const previewStates={'draft-task-name':'未命名作品','generation-dock-summary':'生成设置 · 预览模式','generation-readiness':'尚未接入生成服务','draft-save-status':'本地视觉预览 · 不会保存任务','prompt-template-status':'模板服务尚未接入','runs-status':'任务服务尚未接入'};
 for(const[id,text]of Object.entries(previewStates)){const el=document.getElementById(id);if(el)el.textContent=text;}
 document.querySelectorAll('select:empty').forEach(el=>{const option=document.createElement('option');option.value='';option.textContent='尚未接入';el.append(option);el.disabled=true;});
})();
