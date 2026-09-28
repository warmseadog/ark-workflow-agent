/* Compact paginated task summaries; details and one independent player load on demand. */
window.createProductionRuns = ({api,changeDraft,accessoryLabels}) => {
  const list=document.getElementById('production-run-list'), status=document.getElementById('runs-status');
  const previous=document.getElementById('runs-previous'), next=document.getElementById('runs-next'), pageLabel=document.getElementById('runs-page');
  const names={queued:'排队中',running:'生成中',succeeded:'已完成',failed:'失败',cancelled:'已取消',needs_attention:'需要处理',defaced:'预览完成'};
  let page=1,pages=1,total=0,active=0,sequence=0,loading=false,pollTimer=null,items=[];
  const observedPreparationPeople=new Set();
  let refreshingPeople=false;
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
  const button=(label,fn,cls='run-text-button')=>{const el=node('button',label,cls);el.type='button';el.addEventListener('click',fn);return el;};
  const notice=error=>{status.textContent=error.message || String(error);};
  function schedule(){clearTimeout(pollTimer);pollTimer=setTimeout(()=>{if(!document.hidden&&!loading)refresh();else schedule();},active?5000:30000);}
  function refreshPreparedPeople(){
    const directory=window.portraitPeople;if(!directory||refreshingPeople)return;
    const known=new Set(directory.items.map(person=>person.id));
    const missing=items.map(item=>item.person_preparation?.person_id).filter(id=>id&&!known.has(id)&&!observedPreparationPeople.has(id));
    if(!missing.length)return;
    refreshingPeople=true;let succeeded=false;
    // One directory read for newly resolved identities; never select or change a draft.
    Promise.resolve(directory.ready).then(async()=>{
      const loaded=new Set(directory.items.map(person=>person.id));
      if(missing.some(id=>!loaded.has(id)))await directory.refresh();
      missing.forEach(id=>observedPreparationPeople.add(id));succeeded=true;
    }).catch(()=>{}).finally(()=>{
      refreshingPeople=false;
      // Failed reads retry on the next run refresh, never in an immediate loop.
      if(succeeded)refreshPreparedPeople();
    });
  }
  async function refresh(target=page){
    if(list.querySelector('.run-name-edit')){if(target!==page)status.textContent='请先保存或取消正在修改的名称。';schedule();return;}
    const token=++sequence;loading=true;previous.disabled=next.disabled=true;
    try {
      const data=await api('/runs?page='+target+'&page_size=10');if(token!==sequence||list.querySelector('.run-name-edit'))return;
      items=data.items||[];page=data.page||target;pages=data.pages||1;total=data.total??items.length;active=data.active_count??items.filter(x=>['queued','running'].includes(x.status)).length;
      render();refreshPreparedPeople();status.textContent=total?'共 '+total+' 个任务，最新创建的排在前面。':'还没有生成任务。';
    } catch(error){if(token===sequence)notice(error);}
    finally{if(token===sequence){loading=false;previous.disabled=page<=1;next.disabled=page>=pages;schedule();}}
  }
  function render(){
    const old=new Map([...list.children].map(row=>[row.dataset.runId,row]));
    for(const row of [...list.children])if(!items.some(item=>item.id===row.dataset.runId))row.remove();
    items.forEach((item,index)=>{
      let row=old.get(item.id);const fingerprint=JSON.stringify(item);
      if(!row || (row.dataset.version!==fingerprint && !row.querySelector('.run-name-edit') && !row.querySelector('.run-menu[open]') && !row.querySelector('.run-detail-panel[open]'))){
        const replacement=createRow(item);if(row)row.replaceWith(replacement);row=replacement;row.dataset.version=fingerprint;
      }
      if(list.children[index]!==row)list.insertBefore(row,list.children[index]||null);
    });
    pageLabel.textContent=page+' / '+pages;document.getElementById('runs-pagination').hidden=!total;
  }
  function createRow(item){
    const row=node('article','','production-run run-row');row.dataset.runId=item.id;row.dataset.state=item.status;
    const title=node('div','','run-title-cell'), text=node('span',item.name||'视频任务','run-name-text');
    const pencil=button('✎',()=>editName(item,row,title),'run-rename');pencil.setAttribute('aria-label','修改任务名称');
    title.append(text,pencil);
    const date=new Date(item.created_at);const time=node('time',Number.isNaN(date.getTime())?'—':date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),'run-time');
    const state=node('span',(names[item.status]||item.status)+(['queued','running'].includes(item.status)?' '+(item.progress||0)+'%':''),'run-state');state.title=item.message||'';
    if(item.person_preparation?.message)state.append(node('small',item.person_preparation.message,'run-preparation-message'));
    const duration=node('span',item.duration>0?item.duration+' 秒':'—','run-duration');
    const actions=node('div','','run-actions');
    if(item.download_url){const play=button('播放',()=>openPlayer(item));play.dataset.runAction='play';actions.append(play);const download=node('a','下载','run-text-button');download.href=item.download_url;download.download='';actions.append(download);}
    const more=node('details','','run-menu');more.append(node('summary','更多'));
    const menu=node('div','','run-menu-items');more.append(menu);
    const panel=node('details','','run-detail-panel');panel.append(node('summary','任务详情'));panel.hidden=true;
    let detailLoaded=false;
    async function showDetails(){more.open=false;panel.hidden=false;panel.open=!panel.open;if(!panel.open)return;
      if(!detailLoaded){const loading=node('p','正在读取详情…');panel.append(loading);
        try{const full=await api('/runs/'+encodeURIComponent(item.id));loading.remove();fillDetails(panel,full);detailLoaded=true;}
        catch(error){loading.textContent=error.message;}
      }
    }
    const add=(action,label,fn,disabled=false)=>{const b=button(label,async()=>{more.open=false;try{await fn();}catch(error){notice(error);}});b.dataset.runAction=action;b.disabled=disabled;menu.append(b);};
    add('details','查看详情',showDetails);
    add('copy','复制为草稿',()=>changeDraft(()=>api('/runs/'+encodeURIComponent(item.id)+'/copy','POST',{})));
    if(item.can_cancel)add('cancel','取消排队',async()=>{await api('/runs/'+item.id+'/cancel','POST',{});await refresh();});
    if(item.can_resume)add('resume','继续查询 / 下载',async()=>{await api('/runs/'+item.id+'/resume','POST',{});await refresh();});
    if(item.can_retry_preparation)add('retry-preparation','重新检查人物准备',async()=>{await api('/runs/'+item.id+'/person-preparation/retry','POST',{});await refresh();});
    add('delete','删除任务',async()=>{
      if(!window.confirm('删除这条任务记录？草稿和原始视频文件会保留。'+(item.status==='needs_attention'?' 此操作不会取消服务商端的任务。':'')))return;
      await api('/runs/'+item.id,'DELETE');if(playerRun?.id===item.id)dialog.close();await refresh();
    },item.can_delete===false);
    actions.append(more);row.append(title,time,state,duration,actions,panel);return row;
  }
  function editName(item,row,title){
    if(title.querySelector('input'))return;
    const edit=node('div','','run-name-edit'),input=node('input');input.value=item.name||'';input.maxLength=120;input.setAttribute('aria-label','任务名称');
    const error=node('small','','run-name-error');error.setAttribute('role','status');
    const cancel=()=>{title.replaceChildren(node('span',item.name||'视频任务','run-name-text'));const pen=button('✎',()=>editName(item,row,title),'run-rename');pen.setAttribute('aria-label','修改任务名称');title.append(pen);};
    let saving=false;
    const save=async()=>{if(saving)return;const name=input.value.trim();if(!name){error.textContent='请输入任务名称';return;}saving=true;saveButton.disabled=true;
      try{const data=await api('/runs/'+item.id+'/name','PUT',{name});item.name=data.name;const current=items.find(x=>x.id===item.id);if(current)current.name=data.name;cancel();if(playerRun?.id===item.id){playerRun.name=data.name;playerTitle.textContent=data.name;}await refresh();}
      catch(e){error.textContent=e.message;}finally{saving=false;saveButton.disabled=false;}
    };
    const saveButton=button('保存',save),cancelButton=button('取消',()=>{if(!saving)cancel();});
    input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();save();}if(event.key==='Escape'){event.preventDefault();if(!saving)cancel();}});
    edit.append(input,saveButton,cancelButton);title.replaceChildren(edit,error);input.focus();input.select();
  }
  function fillDetails(panel,item){
    panel.append(node('p',item.message||names[item.status]||''));
    if(item.person_preparation){
      const preparation=node('div','','run-person-preparation');
      preparation.append(node('p',item.person_preparation.message||'正在准备人物素材'));
      if(item.person_preparation.person_id)preparation.append(node('small','人物编号：'+item.person_preparation.person_id));
      panel.append(preparation);
    }
    if(item.error){const error=node('details','','run-error-detail');error.append(node('summary','错误详情'),node('p',item.error));panel.append(error);}
    const assets=item.snapshot?.assets||[],materials=node('div','','run-detail-assets');
    const labels={video:'动作参考视频',person_video:'人物参考视频',face:'人物参考图',clothing:'衣服参考图',hairstyle:'发型参考图',scene:'场景参考图',...accessoryLabels};
    for(const [kind,label] of Object.entries(labels)){
      const sources=assets.filter(x=>x.kind===kind);if(!sources.length)continue;
      const figure=node('figure');figure.dataset.sourceKind=kind;figure.append(node('figcaption',label));
      const inactive=(kind==='face'&&item.snapshot.person_reference_mode==='video')||(kind==='person_video'&&item.snapshot.person_reference_mode!=='video')||(['hairstyle','scene',...Object.keys(accessoryLabels)].includes(kind)&&!item.snapshot[kind+'_enabled']);
      if(inactive)figure.append(node('small','本次未使用'));
      for(const asset of sources){const video=['video','person_video'].includes(kind),media=node(video?'video':'img');media.src=asset.url;if(video){media.controls=true;media.preload='none';media.playsInline=true;}else{media.alt=label;media.loading='lazy';}figure.append(media);}materials.append(figure);
    }
    panel.append(materials);if(item.snapshot?.scene_description)panel.append(node('p',item.snapshot.scene_description));
    if(item.defaced_url){const holder=node('div','','run-redacted-preview');holder.dataset.redactedPreview='';holder.hidden=true;const media=node('video');media.controls=true;media.preload='none';media.playsInline=true;holder.append(media);
      const toggle=button('预览打码效果',()=>{holder.hidden=!holder.hidden;toggle.textContent=holder.hidden?'预览打码效果':'收起打码预览';toggle.setAttribute('aria-expanded',String(!holder.hidden));if(!holder.hidden&&!media.src)media.src=item.defaced_url;if(holder.hidden)media.pause();});toggle.dataset.runAction='preview-redacted';toggle.setAttribute('aria-expanded','false');panel.append(toggle,holder);
      panel.addEventListener('toggle',()=>{if(!panel.open){media.pause();holder.hidden=true;toggle.textContent='预览打码效果';toggle.setAttribute('aria-expanded','false');}});
    }
    panel.addEventListener('toggle',()=>{if(!panel.open)panel.querySelectorAll('video').forEach(v=>v.pause());});
  }
  const dialog=document.getElementById('run-player-dialog'), video=document.getElementById('run-player-video'),playerTitle=document.getElementById('run-player-title'),playerStatus=document.getElementById('run-player-status');
  let playerRun=null,playback=null,playerToken=0,playbackTimer=null,quality='smooth';
  const qualityButtons=[...dialog.querySelectorAll('[data-play-quality]')];
  function playQuality(selected,preserve=false){
    quality=selected;qualityButtons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.playQuality===selected)));
    const url=playback?.[selected+'_url']||playback?.original_url||playerRun.download_url;
    const time=preserve?video.currentTime:0,paused=preserve?video.paused:false;
    if(video.getAttribute('src')===url)return;
    video.src=url;
    video.onloadedmetadata=()=>{if(!dialog.open)return;if(time&&time<video.duration)video.currentTime=time;if(!paused)video.play().catch(()=>{playerStatus.textContent='点击视频中的播放按钮即可观看。';});};
  }
  async function checkPlayback(token,initial=false){
    try{
      const data=await api('/runs/'+playerRun.id+'/playback');
      if(token!==playerToken||!dialog.open)return;playback=data;
      qualityButtons[0].disabled=!data.smooth_url;
      playerStatus.textContent=data.smooth_url?'流畅预览适合在线观看，下载保留原始画质。':data.status==='processing'?'流畅版本准备中，当前播放原片。':'当前播放原片；流畅版本暂不可用。';
      if(initial){quality=data.smooth_url?'smooth':'original';playQuality(quality);}
      if(data.status==='processing')playbackTimer=setTimeout(()=>checkPlayback(token),3000);
    }catch(error){if(token!==playerToken||!dialog.open)return;playerStatus.textContent='预览状态读取失败，当前播放原片。';if(initial)playQuality('original');}
  }
  async function openPlayer(item){
    clearTimeout(playbackTimer);playerRun=item;playback=null;quality='smooth';const token=++playerToken;playerTitle.textContent=item.name||'视频播放';playerStatus.textContent='正在准备播放…';
    video.pause();video.removeAttribute('src');video.load();qualityButtons.forEach(b=>b.disabled=true);
    document.getElementById('run-player-download').href=item.download_url;dialog.showModal();
    qualityButtons[1].disabled=false;await checkPlayback(token,true);
  }
  qualityButtons.forEach(b=>b.addEventListener('click',()=>playQuality(b.dataset.playQuality,true)));
  document.getElementById('run-player-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{playerToken++;clearTimeout(playbackTimer);video.pause();video.removeAttribute('src');video.load();playerRun=null;});
  video.addEventListener('error',()=>{if(dialog.open)playerStatus.textContent='视频暂时无法播放，可以切换原画或下载查看。';});
  previous.addEventListener('click',()=>refresh(page-1));next.addEventListener('click',()=>refresh(page+1));
  document.getElementById('runs-refresh').addEventListener('click',()=>refresh());
  document.addEventListener('click',event=>{list.querySelectorAll('.run-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
  return {refresh,submitted:()=>refresh(1)};
};
