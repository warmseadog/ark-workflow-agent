// Visual-only preview. Original production scripts are not loaded.
(()=>{
 const dialog=document.querySelector('#mira-preview-info');
 const viewer=document.querySelector('#material-viewer');
 let viewerTrigger;
 const materials=new Map();
 const duration=document.querySelector('#generation-duration-slider');
 let lastDuration=1;
 function syncDuration(){
  const min=Number(duration.min),max=Number(duration.max);
  if(!duration.disabled){const value=duration.value===''?lastDuration:Number(duration.value);duration.value=String(Math.min(max,Math.max(min,Math.round(Number.isFinite(value)?value:lastDuration))));lastDuration=Number(duration.value);}
  document.querySelector('#generation-duration-note').textContent=duration.disabled?'待选择视频':`${duration.value} 秒`;
  duration.style.setProperty('--duration-fill',`${duration.disabled?0:100*(Number(duration.value)-min)/(max-min)}%`);
  duration.setAttribute('aria-valuetext',duration.disabled?'待选择视频':`${duration.value} 秒`);
 }
 duration.addEventListener('input',syncDuration);
 duration.addEventListener('change',syncDuration);
 duration.addEventListener('blur',syncDuration);
 duration.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();syncDuration();}});
 const makeButton=(label,action)=>{const button=document.createElement('button');button.type='button';button.textContent=label;button.dataset.previewControl='';button.addEventListener('click',action);return button;};
 function choose(input,index){
  if(index===undefined){input.click();return;}
  const picker=document.createElement('input');picker.type='file';picker.accept=input.accept;picker.dataset.previewControl='';
  picker.addEventListener('change',()=>{if(picker.files[0])acceptFiles(input,[picker.files[0]],index);},{once:true});picker.click();
 }
 function acceptFiles(input,files,index){
  const box=input.closest('.asset-picker');
  let error=box.parentElement.querySelector('.material-error');
  if(!error){error=document.createElement('p');error.className='material-error';error.setAttribute('role','status');box.after(error);}
  const isVideo=input.accept.includes('video');
  const valid=files.filter(file=>file.type.startsWith(isVideo?'video/':'image/'));
  error.textContent=valid.length===files.length?'':'请选择可读取的图片或视频文件。';
  if(!valid.length)return;
  let entries=materials.get(input)||[];
  const incoming=valid.map(file=>({name:file.name,url:URL.createObjectURL(file),isVideo}));
  if(index!==undefined){if(entries[index])URL.revokeObjectURL(entries[index].url);entries.splice(index,1,incoming[0]);}
  else if(input.multiple){entries.push(...incoming);}
  else {entries.forEach(entry=>URL.revokeObjectURL(entry.url));entries=incoming.slice(0,1);}
  materials.set(input,entries);render(input);input.value='';
 }
 function render(input){
  const entries=materials.get(input)||[],box=input.closest('.asset-picker'),list=box.querySelector('.asset-thumbnails');
  list.replaceChildren();box.classList.toggle('has-media',entries.length>0);list.classList.toggle('single-image',entries.length===1);
  entries.forEach((entry,index)=>{
   const frame=document.createElement('span');frame.className=entry.isVideo?'reference-video':'reference-thumb';
   const badge=document.createElement('span');badge.className='reference-index';badge.textContent=index===0?'主参考':`参考 ${index+1}`;
   const media=document.createElement(entry.isVideo?'video':'img');media.src=entry.url;
   if(entry.isVideo){media.controls=true;media.playsInline=true;media.preload='metadata';media.setAttribute('aria-label',entry.name);if(input.id==='studio-source-video')media.addEventListener('loadedmetadata',()=>{if(!media.isConnected||!Number.isFinite(media.duration)||media.duration<=0)return;duration.disabled=false;duration.value=String(Math.round(media.duration));syncDuration();document.querySelector('#generation-source-duration').textContent=`原片 ${Number(media.duration.toFixed(1))} 秒 · 本地预览`;});}
   else {media.alt=entry.name;media.tabIndex=0;media.setAttribute('role','button');media.setAttribute('aria-label',`放大查看 ${entry.name}`);const open=()=>{viewerTrigger=media;viewer.querySelector('img').src=entry.url;viewer.querySelector('h2').textContent=entry.name;viewer.showModal();};media.addEventListener('click',open);media.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});}
   media.addEventListener('error',()=>{let message=frame.querySelector('.material-error');if(!message){message=document.createElement('span');message.className='material-error';frame.append(message);}message.textContent='无法预览此格式，请替换为浏览器支持的图片或视频。';});
   const caption=document.createElement('span');caption.className='reference-caption';caption.textContent=entry.name;caption.title=entry.name;
   const actions=document.createElement('span');actions.className='reference-actions';
   actions.append(makeButton('替换',()=>choose(input,index)),makeButton('移除',()=>{URL.revokeObjectURL(entry.url);entries.splice(index,1);materials.set(input,entries);render(input);}));
   frame.append(badge,media,caption,actions);list.append(frame);
  });
  const footer=box.parentElement.querySelector('.asset-footer');
  if(footer){const name=footer.querySelector('span');if(name)name.textContent=entries.length?`已选 ${entries.length} ${input.accept.includes('video')?'条':'张'} · 仅在本机预览`:'未选择素材';}
  const count=document.querySelector('#reference-count');
  if(count)count.textContent=`本地参考图 ${[...materials.values()].flat().filter(entry=>!entry.isVideo).length} 张`;
  if(input.id==='studio-face-image')document.querySelector('#person-photo-status').textContent=entries.length?'图片已在本机打开 · 未进行身份校验':'选择清晰的人物参考图。';
  if(input.id==='studio-source-video'){duration.disabled=true;duration.value='';syncDuration();document.querySelector('#generation-source-duration').textContent=entries.length?'正在读取原片时长…':'选择视频后设置时长';}
 }
 document.querySelectorAll('.asset-picker input[type=file]').forEach(input=>{
  input.addEventListener('change',()=>acceptFiles(input,Array.from(input.files)));
  const box=input.closest('.asset-picker');
  const footer=box.parentElement.querySelector('.asset-footer');
  footer?.querySelectorAll('button').forEach(button=>{if(button.id==='toggle-video-url'||button.id==='remove-source-video')return;button.dataset.previewControl='';button.addEventListener('click',()=>choose(input));});
 });
 document.querySelector('#scene-enabled')?.addEventListener('change',e=>{document.querySelector('#scene-custom-fields').hidden=!e.target.checked;document.querySelector('#scene-reference-state').textContent=e.target.checked?'更换场景':'保留原视频场景';});
 document.querySelectorAll('.accessory-buttons button').forEach(button=>{button.dataset.previewControl='';button.addEventListener('click',()=>{const panel=document.getElementById(button.id.replace('-add','-references'));panel.hidden=false;panel.querySelector('input[type=file]').click();});});
 document.querySelector('#material-viewer-close').addEventListener('click',()=>viewer.close());
 viewer.addEventListener('close',()=>{viewer.querySelector('img').removeAttribute('src');viewerTrigger?.focus();});
 window.addEventListener('pagehide',e=>{if(!e.persisted)materials.forEach(entries=>entries.forEach(entry=>URL.revokeObjectURL(entry.url)));});
 let restore;
 function explain(name){restore=document.activeElement;document.querySelector('#mira-preview-detail').textContent=`「${name||'这项功能'}」的生产服务尚未接入。本页只预览现有布局和统一后的 mira 样式，不上传、不生成、不修改账号。`;dialog.showModal();}
 document.addEventListener('submit',e=>{e.preventDefault();explain('提交')});
 document.addEventListener('click',e=>{
  const el=e.target.closest('a,button,input[type=file]');if(!el)return;
  if(el.id==='mira-preview-close'){dialog.close();return;}
  if(el.closest('#mira-preview-info'))return;
  if(el.closest('#material-viewer')||el.hasAttribute('data-preview-control')||el.matches('.asset-picker input[type=file]'))return;
  if(el.matches('a[href="mira-cover.html"],a[href="mira-login.html"]'))return;
  if(el.classList.contains('workspace-toggle')||el.classList.contains('workspace-backdrop')){const open=el.classList.contains('workspace-backdrop')?false:!document.body.hasAttribute('data-sidebar-open');document.body.toggleAttribute('data-sidebar-open',open);document.querySelector('.workspace-toggle').setAttribute('aria-expanded',String(open));document.querySelector('.workspace-backdrop').hidden=!open;return;}
  e.preventDefault();explain(el.textContent.trim()||el.getAttribute('aria-label')||'选择素材');
 },true);
 dialog.addEventListener('close',()=>restore?.focus());
 // Keep the approved A-style brand header. Preview status lives in the intro,
 // not in a replacement header that erases the product identity.
 const previewStates={'draft-task-name':'未命名作品','generation-dock-summary':'生成设置 · 预览模式','generation-readiness':'尚未接入生成服务','draft-save-status':'本地视觉预览 · 不会保存任务','prompt-template-status':'模板服务尚未接入','runs-status':'任务服务尚未接入'};
 for(const[id,text]of Object.entries(previewStates)){const el=document.getElementById(id);if(el)el.textContent=text;}
 document.querySelectorAll('select:empty').forEach(el=>{const option=document.createElement('option');option.value='';option.textContent='尚未接入';el.append(option);el.disabled=true;});
})();
