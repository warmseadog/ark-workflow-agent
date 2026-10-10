/* One task-scoped comparison: the generated result is the shared playback clock. */
window.createVideoComparison = ({readRun,onError=()=>{}}) => {
  const node=(tag,text,id)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(id)el.id=id;return el;};
  const dialog=node('dialog',null,'video-comparison-dialog');dialog.className='video-comparison-dialog';
  dialog.setAttribute('aria-labelledby','comparison-title');
  const heading=node('div');heading.className='comparison-heading';
  const title=node('h3','视频对比','comparison-title'),close=node('button','关闭','comparison-close');close.type='button';
  heading.append(title,close);
  const grid=node('div');grid.className='comparison-grid';
  const source=node('video',null,'comparison-source'),result=node('video',null,'comparison-result');
  for(const [video,label] of [[source,'原始动作视频'],[result,'生成结果']]){
    video.playsInline=true;video.preload='metadata';video.setAttribute('aria-label',label);
    const figure=node('figure');figure.append(node('figcaption',label),video);grid.append(figure);
  }
  source.muted=true;
  const controls=node('div');controls.className='comparison-controls';
  const play=node('button','同步播放','comparison-play');play.type='button';play.disabled=true;
  const seek=node('input',null,'comparison-seek');seek.type='range';seek.min='0';seek.max='0';seek.step='.01';seek.value='0';seek.disabled=true;seek.setAttribute('aria-label','对比播放进度');
  const time=node('output','0:00 / 0:00','comparison-time');
  const speed=node('select',null,'comparison-speed');speed.setAttribute('aria-label','对比播放速度');
  for(const value of [.5,1,1.5,2])speed.add(new Option(value+'×',String(value)));speed.value='1';
  const audio=node('select',null,'comparison-audio');audio.setAttribute('aria-label','对比播放声音');
  for(const [value,label] of [['result','生成结果声音'],['source','原片声音'],['mute','静音']])audio.add(new Option(label,value));
  const download=node('button','下载对比视频','comparison-download');download.type='button';download.disabled=true;
  controls.append(play,seek,time,speed,audio,download);
  const status=node('p','','comparison-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const exportStatus=node('p','','comparison-export-status');exportStatus.setAttribute('role','status');exportStatus.setAttribute('aria-live','polite');
  dialog.append(heading,grid,controls,status,exportStatus);document.body.append(dialog);
  let token=0,read=null,snapshot=null,ready=false,buffering=false,timer=null,frame=null,activeItem=null,exportRead=null,exportUrl=null,exportName='视频';
  const finite=value=>Number.isFinite(value)&&value>0;
  const duration=()=>finite(result.duration)?result.duration:0;
  const clock=value=>Math.floor(value/60)+':'+String(Math.floor(value%60)).padStart(2,'0');
  function bounds(){
    const clip=snapshot?.source_clip,start=Math.min(Math.max(0,Number(clip?.start)||0),source.duration||0);
    const remaining=Math.max(0,(source.duration||0)-start);
    const seconds=finite(clip?.duration)?clip.duration:remaining;
    const scale=clip?.retime==='slow'?Math.min(1,remaining/seconds):1;
    return {start,length:Math.min(seconds*scale,remaining),timeline:Math.min(seconds,remaining/(scale||1)),scale};
  }
  function setAudio(){source.muted=audio.value!=='source';result.muted=audio.value!=='result';}
  function paint(){
    if(!ready||!dialog.open)return;
    const range=bounds(),position=Math.max(0,result.currentTime),ended=position>=range.timeline;
    // Seek just inside the final frame: setting exactly duration may clear the image.
    const expected=Math.min(range.start+range.length-.025,range.start+position*range.scale);
    if(Math.abs(source.currentTime-Math.max(range.start,expected))>.12)source.currentTime=Math.max(range.start,expected);
    source.playbackRate=result.playbackRate*range.scale;
    if(result.paused||ended||buffering)source.pause();
    else if(source.paused)void source.play().catch(()=>{status.textContent='原片暂时无法同步播放，请暂停后重试。';});
    seek.value=String(position);seek.max=String(duration());time.textContent=clock(position)+' / '+clock(duration());
    play.textContent=result.paused?'同步播放':'同步暂停';speed.value=String(result.playbackRate);
    status.textContent=ended?'原片片段已结束，左侧停留在最后一帧，右侧继续播放生成结果。':range.start?'按原片第 '+range.start+' 秒起的片段同步对比。':'左右同步播放；只播放所选一侧的声音。';
  }
  function stopClock(){clearInterval(timer);timer=null;if(frame!==null&&result.cancelVideoFrameCallback)result.cancelVideoFrameCallback(frame);frame=null;}
  function tick(){paint();if(dialog.open&&!result.paused&&result.requestVideoFrameCallback)frame=result.requestVideoFrameCallback(tick);}
  function startClock(){stopClock();paint();timer=setInterval(paint,150);if(result.requestVideoFrameCallback)frame=result.requestVideoFrameCallback(tick);}
  function release(){
    token++;read?.abort();read=null;stopClock();ready=false;buffering=false;snapshot=null;activeItem=null;
    exportRead?.abort();exportRead=null;exportUrl=null;download.disabled=true;download.textContent='下载对比视频';exportStatus.textContent='';
    for(const video of [source,result]){video.pause();video.removeAttribute('src');video.load();}
    play.disabled=seek.disabled=true;
  }
  function loaded(){
    if(!snapshot||!finite(source.duration)||!finite(result.duration)||!dialog.open)return;
    ready=true;play.disabled=seek.disabled=false;paint();
  }
  for(const video of [source,result])video.addEventListener('loadedmetadata',loaded);
  result.addEventListener('play',startClock);
  result.addEventListener('waiting',()=>{buffering=true;source.pause();});
  result.addEventListener('playing',()=>{buffering=false;paint();});
  result.addEventListener('pause',()=>{stopClock();paint();});
  result.addEventListener('ended',()=>{stopClock();paint();});
  for(const event of ['seeking','seeked','ratechange','timeupdate'])result.addEventListener(event,paint);
  play.addEventListener('click',()=>{
    if(!ready)return;
    if(!result.paused){result.pause();source.pause();stopClock();paint();return;}
    if(result.ended)result.currentTime=0;
    void result.play().catch(()=>{status.textContent='视频暂时无法播放，请重试或下载后查看。';});
  });
  seek.addEventListener('input',()=>{if(ready){result.currentTime=Number(seek.value);paint();}});
  speed.addEventListener('change',()=>{result.playbackRate=Number(speed.value);paint();});
  audio.addEventListener('change',setAudio);
  download.addEventListener('click',async()=>{
    if(!exportUrl||exportRead)return;
    const current=token,controller=new AbortController();exportRead=controller;
    download.disabled=true;download.textContent='正在导出…';exportStatus.textContent='正在合成左右并排的视频，首次导出需要一些时间…';
    try{
      const url=new URL(exportUrl,location.href);url.searchParams.set('audio',audio.value);
      const response=await fetch(url,{signal:controller.signal,credentials:'same-origin'});
      if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'对比视频导出失败，请稍后重试。');}
      if(!response.headers.get('content-type')?.includes('video/mp4'))throw new Error('未能取得对比视频，请刷新页面后重试。');
      const blob=await response.blob();
      if(current!==token||!dialog.open)return;
      const href=URL.createObjectURL(blob),link=node('a');link.href=href;link.download=exportName+'-对比.mp4';
      document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(href),60000);
      exportStatus.textContent='对比视频已导出，下载已开始。';
    }catch(error){if(current===token&&error.name!=='AbortError'){exportStatus.textContent=error.message;onError(error);}}
    finally{if(current===token){exportRead=null;download.disabled=!exportUrl;download.textContent='下载对比视频';}}
  });
  for(const video of [source,result])video.addEventListener('error',()=>{
    if(dialog.open&&video.getAttribute('src')){result.pause();status.textContent='对比视频暂时无法读取，请关闭后重试或下载查看。';}
  });
  function closeComparison(){release();if(dialog.open)dialog.close();}
  close.addEventListener('click',closeComparison);
  dialog.addEventListener('close',()=>{if(!dialog.open)release();});
  window.addEventListener('pagehide',closeComparison);
  return {
    async open(item){
      release();activeItem=item;const current=token;read=new AbortController();
      title.textContent=(item.name||'视频任务')+' · 原片与结果对比';status.textContent='正在读取本次任务视频…';
      if(!dialog.open)dialog.showModal();
      try{
        const full=await readRun(item,'',{signal:read.signal});
        if(current!==token||!dialog.open)return;
        const original=full.snapshot?.assets?.find(asset=>asset.id===full.snapshot.source_asset_id&&asset.kind==='video');
        if(!original?.url||!full.download_url)throw new Error('此任务的原始动作视频或生成结果不可用，无法对比。');
        snapshot=full.snapshot;source.playbackRate=result.playbackRate=1;speed.value='1';audio.value='result';setAudio();
        exportUrl=full.comparison_url||null;exportName=(full.name||item.name||'视频').replace(/[\\/:*?"<>|]/g,'_');download.disabled=!exportUrl;
        source.src=original.url;result.src=full.download_url;source.load();result.load();
      }catch(error){if(current!==token||error.name==='AbortError')return;status.textContent=error.message;onError(error);}
    },
    close:closeComparison,
    isOpen(item){return dialog.open&&activeItem?.id===item.id&&activeItem?.user_id===item.user_id;}
  };
};
