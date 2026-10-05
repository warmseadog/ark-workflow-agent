/* Compact paginated task summaries; details and one independent player load on demand. */
window.createProductionRuns = ({api,changeDraft,retryWithoutAudio,accessoryLabels,media}) => {
  const list=document.getElementById('production-run-list'), status=document.getElementById('runs-status');
  const previous=document.getElementById('runs-previous'), next=document.getElementById('runs-next'), pageLabel=document.getElementById('runs-page');
  const userControls=document.getElementById('runs-user-controls'), userFilter=document.getElementById('runs-user-filter');
  const isAdmin=()=>['admin','super_admin'].includes(window.currentAccount?.role);
  const delegatedUser=()=>window.delegatedEditor?.userId || '';
  const rowKey=item=>(item.user_id||'')+':'+item.id;
  const readRun=(item,suffix='',options={})=>item.user_id
    ? api('/'+encodeURIComponent(item.user_id)+'/'+encodeURIComponent(item.id)+suffix,'GET',undefined,{...options,adminRecords:true})
    : api('/runs/'+encodeURIComponent(item.id)+suffix,'GET',undefined,options);
  const names={queued:'排队中',running:'生成中',succeeded:'已完成',failed:'失败',cancelled:'已取消',needs_attention:'需要处理',defaced:'预览完成'};
  let page=1,pages=1,total=0,active=0,sequence=0,loading=false,pollTimer=null,items=[];
  const observedPreparationPeople=new Set();
  const audioRetries=new Set();
  let refreshingPeople=false, suspended=false;
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
  const button=(label,fn,cls='run-text-button')=>{const el=node('button',label,cls);el.type='button';el.addEventListener('click',fn);return el;};
  const notice=error=>{status.textContent=error.message || String(error);};
  const comparison=window.createVideoComparison?.({readRun,onError:notice});
  const restoreKeys=new Map(),restoringTasks=new Set();
  async function restoreTask(item){
    const key='production-restore:'+String(window.currentAccount?.id||'local')+':'+rowKey(item);
    if(restoringTasks.has(key))return;
    restoringTasks.add(key);
    try{
      let id=restoreKeys.get(key);
      if(!id){try{id=localStorage.getItem(key);}catch(_){}id ||= crypto.randomUUID();restoreKeys.set(key,id);}
      try{localStorage.setItem(key,id);}catch(_){}
      await changeDraft(async()=>{
        const response=await fetch(item.restore_url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({idempotency_key:id})});
        const data=await response.json();
        if(!response.ok){
          if(response.status<500){restoreKeys.delete(key);try{localStorage.removeItem(key);}catch(_){}}
          throw new Error(typeof data.detail==='string'?data.detail:'恢复草稿失败，请重试。');
        }
        restoreKeys.delete(key);try{localStorage.removeItem(key);}catch(_){}
        return data;
      });
    }finally{restoringTasks.delete(key);}
  }
  function elapsed(seconds){
    if(seconds==null || !Number.isFinite(seconds))return '未记录';
    const value=Math.max(0,Math.floor(seconds)),hours=Math.floor(value/3600),minutes=Math.floor(value%3600/60),rest=value%60;
    return (hours?hours+' 小时 ':'')+(hours||minutes?minutes+' 分 ':'')+rest+' 秒';
  }
  function wallTime(item){
    const timing=item.timing;
    if(!timing?.available)return '耗时未记录';
    const label=timing.is_live||item.status==='needs_attention'?'已耗时 ':'总耗时 ';
    return label+elapsed(timing.total_seconds)+(item.status==='needs_attention'?'（待处理）':'');
  }
  const phaseLabels={waiting:'排队',masking:'打码',upload:'上传',model:'模型生成',other:'其他处理'};
  function phaseTime(phase){
    if(phase?.cached)return '复用缓存'+(phase.seconds>0?'（'+elapsed(phase.seconds)+'）':'');
    if(!phase||phase.status==='unknown'||phase.seconds==null)return '未记录';
    if(phase.status==='pending')return '尚未执行';
    return elapsed(phase.seconds)+(phase.status==='running'?'（进行中）':'');
  }
  function phaseSummary(item){
    if(!item.timing?.phases)return '';
    return ['masking','model'].map(key=>phaseLabels[key]+' '+phaseTime(item.timing.phases[key])).join(' · ');
  }
  function timingDetails(holder,item){
    holder.replaceChildren();
    const timing=item.timing;
    if(!timing?.available){holder.append(node('p','历史记录未采集耗时。'));return;}
    holder.append(node('p',wallTime(item)),node('p','排队 '+elapsed(timing.queue_seconds)+' · 执行 '+elapsed(timing.execution_seconds)+' · 等待处理 '+elapsed(timing.paused_seconds)));
    const phases=node('ul','','run-phase-details');
    for(const [key,label] of Object.entries(phaseLabels))phases.append(node('li',label+'：'+phaseTime(timing.phases?.[key])));
    holder.append(phases);
    const date=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
    if(timing.started_at)holder.append(node('p','首次开始（北京时间）：'+date(timing.started_at)));
    if(timing.finished_at)holder.append(node('p','结束（北京时间）：'+date(timing.finished_at)));
    if(timing.paused_at)holder.append(node('p','等待处理起始（北京时间）：'+date(timing.paused_at)));
    if(timing.interrupted)holder.append(node('p','运行曾中断，执行耗时无法准确计算；总耗时包含中断间隔。'));
  }
  function updateTiming(row,item){
    row.querySelector('.run-wall-time').textContent=wallTime(item);
    row.querySelector('.run-phase-summary').textContent=phaseSummary(item);
    const details=row.querySelector('.run-timing-details');if(details)timingDetails(details,item);
    // Open detail/media panels survive polls, while their state and timing stay current.
    row.dataset.state=item.status;
    const state=row.querySelector('.run-state');
    state.textContent=(names[item.status]||item.status)+(['queued','running'].includes(item.status)?' '+(item.progress||0)+'%':'');
    state.title=item.message||'';
    if(item.person_preparation?.message)state.append(node('small',item.person_preparation.message,'run-preparation-message'));

  }
  function schedule(){if(suspended)return;clearTimeout(pollTimer);pollTimer=setTimeout(()=>{if(!document.hidden&&!loading)refresh();else schedule();},active?5000:30000);}
  function refreshPreparedPeople(){
    const directory=window.portraitPeople;if(!directory||refreshingPeople)return;
    const known=new Set(directory.items.map(person=>person.id));
    const missing=items.filter(item=>delegatedUser() ? item.user_id===delegatedUser() : !item.read_only).map(item=>item.person_preparation?.person_id).filter(id=>id&&!known.has(id)&&!observedPreparationPeople.has(id));
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
      await window.accountReady;if(suspended||token!==sequence)return;
      const owner=delegatedUser();
      if(userControls)userControls.hidden=!isAdmin() || Boolean(owner);
      const data=await api((isAdmin()?'':'/runs')+'?page='+target+'&page_size=10'+(isAdmin()?'&user_id='+encodeURIComponent(owner || userFilter?.value || ''):''),'GET',undefined,{adminRecords:isAdmin()});if(suspended||token!==sequence||list.querySelector('.run-name-edit'))return;
      if(isAdmin()&&userFilter&&!owner){
        const selected=userFilter.value,version=JSON.stringify(data.users||[]);
        if(userFilter.dataset.version!==version){
          userFilter.replaceChildren(new Option('全部用户',''),...(data.users||[]).map(user=>new Option(user.username+(user.enabled?'':'（已停用）'),user.id)));
          userFilter.value=selected;userFilter.dataset.version=version;
        }
      }
      items=owner ? (data.items||[]).filter(item=>item.user_id===owner).map(item=>({...item,read_only:true})) : data.items||[];
      page=data.page||target;pages=data.pages||1;total=data.total??items.length;active=data.active_count??items.filter(x=>['queued','running'].includes(x.status)).length;
      render();refreshPreparedPeople();status.textContent=(owner?'当前代操作用户：':'')+(total?'共 '+total+' 个任务，最新创建的排在前面。':'还没有生成任务。');
    } catch(error){if(!suspended&&token===sequence&&error.name!=='AbortError')notice(error);}
    finally{if(token===sequence){loading=false;previous.disabled=page<=1;next.disabled=page>=pages;schedule();}}
  }
  function render(){
    const old=new Map([...list.children].map(row=>[row.dataset.runKey,row]));
    for(const row of [...list.children])if(!items.some(item=>rowKey(item)===row.dataset.runKey)){row.querySelectorAll('video').forEach(media.releaseVideo);row.remove();}
    items.forEach((item,index)=>{
      let row=old.get(rowKey(item));const fingerprint=JSON.stringify(item);
      if(!row || (row.dataset.version!==fingerprint && !row.querySelector('.run-name-edit') && !row.querySelector('.run-menu[open]') && !row.querySelector('.run-detail-panel[open]'))){
        const replacement=createRow(item);if(row)row.replaceWith(replacement);row=replacement;row.dataset.version=fingerprint;
      }
      updateTiming(row,item);
      if(list.children[index]!==row)list.insertBefore(row,list.children[index]||null);
    });
    pageLabel.textContent=page+' / '+pages;document.getElementById('runs-pagination').hidden=!total;
  }
  function createRow(item){
    const row=node('article','','production-run run-row');row.dataset.runId=item.id;row.dataset.runKey=rowKey(item);row.dataset.state=item.status;
    const title=node('div','','run-title-cell'), text=node('span',item.name||'视频任务','run-name-text');
    const pencil=button('✎',()=>editName(item,row,title),'run-rename');pencil.setAttribute('aria-label','修改任务名称');
    title.append(text);if(!item.read_only)title.append(pencil);
    if(item.username)title.append(node('small',item.username,'run-owner'));
    const date=new Date(item.created_at);const time=node('time',Number.isNaN(date.getTime())?'—':date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}),'run-time');
    const state=node('span',(names[item.status]||item.status)+(['queued','running'].includes(item.status)?' '+(item.progress||0)+'%':''),'run-state');state.title=item.message||'';
    if(item.person_preparation?.message)state.append(node('small',item.person_preparation.message,'run-preparation-message'));
    const model=node('span',window.generationOptions?.label?.(item.model)||item.model||'未记录','run-model');
    const duration=node('span','','run-duration');
    duration.append(node('span',item.target_duration ? `目标 ${item.target_duration} 秒` : item.source_clip ? `片段 ${item.source_clip.start}–${Number((item.source_clip.start + item.source_clip.duration).toFixed(3))} 秒 · ${item.source_clip.duration} 秒` : item.duration===-1?'视频跟随原片':item.duration>0?'视频 '+item.duration+' 秒':'视频时长未记录'),node('small',wallTime(item),'run-wall-time'));
    duration.append(node('small',phaseSummary(item),'run-phase-summary'));
    const actions=node('div','','run-actions');
    if(item.download_url){const play=button('播放',()=>openPlayer(item));play.dataset.runAction='play';actions.append(play);if(comparison){const compare=button('对比',()=>{if(dialog.open)dialog.close();comparison.open(item);});compare.dataset.runAction='comparison';actions.append(compare);}const download=node('a','下载','run-text-button');download.href=item.download_url;download.download='';actions.append(download);}
    const more=node('details','','run-menu');more.append(node('summary','更多'));
    const menu=node('div','','run-menu-items');more.append(menu);
    const panel=node('details','','run-detail-panel');panel.append(node('summary','任务详情'));panel.hidden=true;
    let detailLoaded=false, detailToken=0, detailRead=null;
    panel.addEventListener('toggle',()=>{
      if(!panel.open){detailToken++;detailRead?.abort();panel.querySelectorAll('video').forEach(media.releaseVideo);}
    });
    async function showDetails(){more.open=false;panel.hidden=false;panel.open=!panel.open;if(!panel.open)return;
      if(!detailLoaded){const token=++detailToken;detailRead?.abort();detailRead=new AbortController();const loading=node('p','正在读取详情…');panel.append(loading);
        try{const full=await readRun(item,'',{signal:detailRead.signal});loading.remove();if(suspended||token!==detailToken||!panel.open||!panel.isConnected)return;fillDetails(panel,full);detailLoaded=true;}
        catch(error){if(error.name==='AbortError'||token!==detailToken)loading.remove();else loading.textContent=error.message;}
      }
    }
    const add=(action,label,fn,disabled=false)=>{const b=button(label,async()=>{more.open=false;try{await fn();}catch(error){notice(error);}});b.dataset.runAction=action;b.disabled=disabled;menu.append(b);};
    add('details','查看详情',showDetails);
    if(item.can_restore_draft&&item.restore_url)add('restore','恢复为新草稿',()=>restoreTask(item));
    if(!item.read_only){
    add('copy','复制为草稿',()=>changeDraft(()=>api('/runs/'+encodeURIComponent(item.id)+'/copy','POST',{})));
    if(item.can_cancel)add('cancel','取消排队',async()=>{await api('/runs/'+item.id+'/cancel','POST',{});await refresh();});
    if(item.can_resume)add('resume','继续查询 / 下载',async()=>{await api('/runs/'+item.id+'/resume','POST',{});await refresh();});
    if(item.can_retry_preparation)add('retry-preparation','重新检查人物准备',async()=>{await api('/runs/'+item.id+'/person-preparation/retry','POST',{});await refresh();});
    add('delete','删除任务',async()=>{
      if(!window.confirm('删除这条任务记录？草稿和原始视频文件会保留。'+(item.status==='needs_attention'?' 此操作不会取消服务商端的任务。':'')))return;
      await api('/runs/'+item.id,'DELETE');if(playerRun?.id===item.id)dialog.close();if(comparison?.isOpen(item))comparison.close();await refresh();
    },item.can_delete===false);
    }
    actions.append(more);row.append(title,time,state,model,duration,actions);
    if(item.status==='needs_attention' && item.error_kind==='submission_uncertain'){
      const warning=node('div','','run-support-warning');warning.dataset.supportWarning='';
      warning.append(node('p','提交结果待确认：不要重复提交或重新生成。请联系管理员核对服务商的原任务记录。'));
      window.supportUI?.appendContext(warning,item);row.append(warning);
    }
    if(item.error_kind==='audio_copyright' && item.status==='failed'){
      const help=node('div','','run-audio-help');
      help.append(node('strong','生成声音未通过版权检查'),node('p','服务商认为生成的音频可能涉及版权限制。'+(item.can_retry_without_audio?'可以关闭声音后，重新生成无声视频。':'请检查参考素材，或联系服务商核查。')));
      const tools=node('div','','run-audio-actions');
      if(!item.read_only && item.can_retry_without_audio && retryWithoutAudio){
        const retry=button('关闭声音并重试',async()=>{
          if(audioRetries.has(item.id))return;
          audioRetries.add(item.id);retry.disabled=true;retry.textContent='正在提交…';
          try{await retryWithoutAudio(item.id);}catch(error){notice(error);}
          finally{audioRetries.delete(item.id);retry.disabled=false;retry.textContent='关闭声音并重试';await refresh();}
        },'secondary run-audio-retry');
        retry.disabled=audioRetries.has(item.id);retry.dataset.runAction='retry-without-audio';tools.append(retry);
      }
      tools.append(button('查看错误详情',async()=>{if(!panel.open)await showDetails();const detail=panel.querySelector('.run-error-detail');if(detail)detail.open=true;}));
      help.append(tools);
      if(item.can_retry_without_audio)help.append(node('small','将创建新的无声生成任务。'));
      row.append(help);
    }
    row.append(panel);return row;
  }
  function editName(item,row,title){
    if(title.querySelector('input'))return;
    const edit=node('div','','run-name-edit'),input=node('input');input.value=item.name||'';input.maxLength=120;input.setAttribute('aria-label','任务名称');
    const error=node('small','','run-name-error');error.setAttribute('role','status');
    const cancel=()=>{title.replaceChildren(node('span',item.name||'视频任务','run-name-text'));const pen=button('✎',()=>editName(item,row,title),'run-rename');pen.setAttribute('aria-label','修改任务名称');title.append(pen);if(item.username)title.append(node('small',item.username,'run-owner'));};
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
    const timing=node('div','','run-timing-details');timingDetails(timing,item);panel.append(timing);
    panel.append(node('p','生成声音：'+(item.snapshot?.model?.generate_audio === false ? '关闭（无声视频）' : '开启')));
    panel.append(node('p',item.message||names[item.status]||''));
    if(item.continuation?.base_ready){
      panel.append(node('p',item.continuation.complete ? '基础片与剧情续写已完成' : '基础片已保存；恢复任务将继续续写阶段'));
      if(item.base_url){
        const base=node('a','查看已保存的基础片');base.href=item.base_url;base.target='_blank';base.rel='noopener';panel.append(base);
      }
      if(item.continuation.plan){
        const extension=node('details');
        extension.append(node('summary','查看自动续写提示词'),node('p',item.continuation.plan.continuation_prompt));
        extension.append(node('small',`LLM：${item.continuation.llm_model} · Skill：${item.continuation.skill_version}`));
        panel.append(extension);
      }
    }
    if(item.person_preparation){
      const preparation=node('div','','run-person-preparation');
      preparation.append(node('p',item.person_preparation.message||'正在准备人物素材'));
      if(item.person_preparation.person_id)preparation.append(node('small','人物编号：'+item.person_preparation.person_id));
      panel.append(preparation);
    }
    if(item.error){const error=node('details','','run-error-detail');error.append(node('summary','错误详情'),node('p',item.error));window.supportUI?.appendContext(error,item);panel.append(error);}
    const assets=item.snapshot?.assets||[],materials=node('div','','run-detail-assets');
    const labels={video:'动作参考视频',person_video:'人物参考视频',face:'人物参考图',clothing:'衣服参考图',hairstyle:'发型参考图',scene:'场景参考图',...accessoryLabels};
    for(const [kind,label] of Object.entries(labels)){
      const sources=assets.filter(x=>x.kind===kind);if(!sources.length)continue;
      const figure=node('figure');figure.dataset.sourceKind=kind;figure.append(node('figcaption',label));
      const inactive=(kind==='face'&&item.snapshot.person_reference_mode==='video')||(kind==='person_video'&&item.snapshot.person_reference_mode!=='video')||(['hairstyle','scene',...Object.keys(accessoryLabels)].includes(kind)&&!item.snapshot[kind+'_enabled']);
      if(inactive)figure.append(node('small','本次未使用'));
      const referenceLabel=accessoryLabels[kind] ? label+'参考图' : label;
      const rejected=item.error_kind==='material_rejected' && !inactive && (item.error||'').includes(referenceLabel);
      if(rejected){
        figure.tabIndex=-1;figure.classList.add('run-source-problem');
        figure.append(node('small','此素材未通过检查'));
        const locate=button('查看问题素材：'+referenceLabel,()=>{figure.scrollIntoView({block:'center'});figure.focus({preventScroll:true});});
        locate.dataset.locateSource=kind;panel.append(locate);
      }
      for(const asset of sources){const isVideo=['video','person_video'].includes(kind),element=node(isVideo?'video':'img');figure.append(element);if(isVideo){media.lazyVideo(element,asset.url,asset.poster_url||asset.thumbnail_url||'');}else{element.src=media.thumbnailFor(asset);element.alt=label;element.loading='lazy';}}materials.append(figure);
    }
    panel.append(materials);if(item.snapshot?.scene_description)panel.append(node('p',item.snapshot.scene_description));
    const windowMedia=media;
    if(item.defaced_url){const holder=node('div','','run-redacted-preview');holder.dataset.redactedPreview='';holder.hidden=true;const media=node('video');media.controls=true;media.preload='none';media.playsInline=true;holder.append(media);
      const toggle=button('预览打码效果',()=>{holder.hidden=!holder.hidden;toggle.textContent=holder.hidden?'预览打码效果':'收起打码预览';toggle.setAttribute('aria-expanded',String(!holder.hidden));if(!holder.hidden)windowMedia.lazyVideo(media,item.defaced_url);if(holder.hidden)windowMedia.releaseVideo(media);});toggle.dataset.runAction='preview-redacted';toggle.setAttribute('aria-expanded','false');panel.append(toggle,holder);
      panel.addEventListener('toggle',()=>{if(!panel.open){windowMedia.releaseVideo(media);holder.hidden=true;toggle.textContent='预览打码效果';toggle.setAttribute('aria-expanded','false');}});
    }
    panel.addEventListener('toggle',()=>{if(!panel.open)panel.querySelectorAll('video').forEach(media.releaseVideo);});
  }
  const dialog=document.getElementById('run-player-dialog'), video=document.getElementById('run-player-video'),playerTitle=document.getElementById('run-player-title'),playerStatus=document.getElementById('run-player-status');
  let playerRun=null,playback=null,playerToken=0,playbackTimer=null,quality='smooth',playerRead=null;
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
      const data=await readRun(playerRun,'/playback',{signal:playerRead.signal});
      if(token!==playerToken||!dialog.open)return;playback=data;
      qualityButtons[0].disabled=!data.smooth_url;
      playerStatus.textContent=data.smooth_url?'流畅预览适合在线观看，下载保留原始画质。':data.status==='processing'?'流畅版本准备中，当前播放原片。':'当前播放原片；流畅版本暂不可用。';
      if(initial){quality=data.smooth_url?'smooth':'original';playQuality(quality);}
      if(data.status==='processing')playbackTimer=setTimeout(()=>checkPlayback(token),3000);
    }catch(error){if(token!==playerToken||!dialog.open)return;playerStatus.textContent='预览状态读取失败，当前播放原片。';if(initial)playQuality('original');}
  }
  async function openPlayer(item){
    comparison?.close();
    clearTimeout(playbackTimer);playerRead?.abort();playerRead=new AbortController();playerRun=item;playback=null;quality='smooth';const token=++playerToken;playerTitle.textContent=item.name||'视频播放';playerStatus.textContent='正在准备播放…';
    video.pause();video.removeAttribute('src');video.load();qualityButtons.forEach(b=>b.disabled=true);
    document.getElementById('run-player-download').href=item.download_url;dialog.showModal();
    qualityButtons[1].disabled=false;await checkPlayback(token,true);
  }
  qualityButtons.forEach(b=>b.addEventListener('click',()=>playQuality(b.dataset.playQuality,true)));
  if(comparison){
    const switchComparison=button('对比原片',()=>{const item=playerRun;if(!item)return;dialog.close();comparison.open(item);});
    switchComparison.id='run-player-comparison';dialog.querySelector('.run-player-tools').append(switchComparison);
  }
  document.getElementById('run-player-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{playerToken++;playerRead?.abort();clearTimeout(playbackTimer);video.pause();video.removeAttribute('src');video.load();playerRun=null;});
  video.addEventListener('error',()=>{if(dialog.open)playerStatus.textContent='视频暂时无法播放，可以切换原画或下载查看。';});
  previous.addEventListener('click',()=>refresh(page-1));next.addEventListener('click',()=>refresh(page+1));
  document.getElementById('runs-refresh').addEventListener('click',()=>refresh());
  userFilter?.addEventListener('change',()=>{
    if(list.querySelector('.run-name-edit')){userFilter.value=userFilter.dataset.applied||'';notice('请先保存或取消正在修改的名称。');return;}
    userFilter.dataset.applied=userFilter.value;if(dialog.open)dialog.close();
    comparison?.close();
    list.querySelectorAll('video').forEach(media.releaseVideo);list.replaceChildren();
    status.textContent='正在读取任务…';refresh(1);
  });
  document.addEventListener('click',event=>{list.querySelectorAll('.run-menu[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;});});
  return {refresh,submitted:()=>refresh(1),suspend(){suspended=true;sequence++;clearTimeout(pollTimer);playerToken++;playerRead?.abort();clearTimeout(playbackTimer);if(dialog.open)dialog.close();comparison?.close();list.querySelectorAll('video').forEach(media.releaseVideo);}};
};
