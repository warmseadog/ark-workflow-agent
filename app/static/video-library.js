(() => {
  'use strict';
  const byId=id=>document.getElementById(id),grid=byId('video-library-grid'),status=byId('library-status'),more=byId('library-more');
  const dialog=byId('library-player'),video=byId('library-video'),playerStatus=byId('library-player-status');
  const prev=byId('library-prev'),next=byId('library-next');
  const items=[],seen=new Set();let page=0,pages=1,loading=false,selected=-1,controller=null,serial=0,switching=false;
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
  async function api(path,options={}){return window.accountUI.readJSON(await fetch('/api/production'+path,{cache:'no-store',...options}));}
  function card(item,index){
    const article=node('article',null,'library-card'),cover=node('button',null,'library-cover');cover.type='button';cover.setAttribute('aria-label','播放 '+(item.name||'视频作品'));
    const fallback=node('span',null,'library-cover-placeholder');fallback.append(node('b','▤'),node('span','视频封面'));cover.append(fallback);
    const img=node('img');img.loading='lazy';img.decoding='async';img.alt='';img.src=item.poster_url;img.addEventListener('error',()=>img.remove(),{once:true});cover.append(img);
    const play=node('span',null,'library-cover-play');play.append(node('b','▶'),node('span','观看'));cover.append(play);
    if(Number(item.duration)>0)cover.append(node('span',item.duration+' 秒','library-duration'));
    cover.addEventListener('click',()=>open(index));article.append(cover);
    const title=node('h2',item.name||'视频作品');title.title=item.name||'视频作品';article.append(title);
    const date=new Date(item.created_at);article.append(node('p',Number.isNaN(date.getTime())?'已生成':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})));
    return article;
  }
  async function load(){
    if(loading||page>=pages)return false;
    loading=true;more.disabled=true;status.textContent=page?'正在加载更多…':'正在读取视频…';
    try{
      const data=await api('/videos?page='+(page+1)+'&page_size=12');
      page=data.page;pages=data.pages;byId('library-count').textContent=data.total+' 部';
      for(const item of data.items){if(seen.has(item.id))continue;seen.add(item.id);items.push(item);grid.append(card(item,items.length-1));}
      status.textContent='';byId('library-empty').hidden=items.length>0;more.hidden=page>=pages;byId('library-end').hidden=!items.length||page<pages;
      return true;
    }catch(error){status.textContent='视频读取失败：'+error.message;more.hidden=false;more.textContent='重试加载';return false;}
    finally{loading=false;more.disabled=false;}
  }
  function release(){serial++;controller?.abort();controller=null;video.pause();video.removeAttribute('src');video.load();}
  async function open(index){
    if(!items[index])return;
    release();selected=index;const token=serial,item=items[index];controller=new AbortController();
    byId('library-player-title').textContent=item.name||'视频作品';byId('library-download').href=item.download_url;
    video.poster=item.poster_url;prev.disabled=index===0;next.disabled=index===items.length-1&&page>=pages;
    playerStatus.textContent='正在准备播放…';if(!dialog.open)dialog.showModal();
    try{
      const playback=await api('/runs/'+encodeURIComponent(item.id)+'/playback',{signal:controller.signal});
      if(token!==serial||!dialog.open)return;
      video.src=playback.smooth_url||playback.original_url||item.download_url;
      playerStatus.textContent=(index+1)+' / '+items.length+' · '+(playback.smooth_url?'流畅播放':'原画播放');
      video.play().catch(()=>{if(token===serial&&dialog.open)playerStatus.textContent='点击视频播放按钮开始观看。';});
    }catch(error){if(token!==serial||!dialog.open)return;playerStatus.textContent='暂时无法播放：'+error.message;}
  }
  async function step(direction){
    if(switching)return;switching=true;
    const origin=selected,token=serial;
    try{if(direction>0&&selected===items.length-1&&page<pages)await load();if(dialog.open&&token===serial&&items[origin+direction])await open(origin+direction);}
    finally{switching=false;}
  }
  more.addEventListener('click',load);
  // Only the next page is requested near the bottom. Images stay native-lazy.
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)&&!more.hidden)load();},{rootMargin:'250px'});
  observer.observe(more);load();
  byId('library-close').addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>{release();video.removeAttribute('poster');selected=-1;});
  prev.addEventListener('click',()=>step(-1));next.addEventListener('click',()=>step(1));
  dialog.addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown'].includes(event.key)&&event.target!==video){event.preventDefault();step(event.key==='ArrowDown'?1:-1);}});
  let wheelTime=0,touchY=null;
  dialog.addEventListener('wheel',event=>{if(Math.abs(event.deltaY)<20)return;event.preventDefault();if(Date.now()-wheelTime<650)return;wheelTime=Date.now();step(event.deltaY>0?1:-1);},{passive:false});
  dialog.addEventListener('touchstart',event=>{touchY=event.touches.length===1?event.touches[0].clientY:null;},{passive:true});
  dialog.addEventListener('touchend',event=>{if(touchY===null)return;const delta=touchY-event.changedTouches[0].clientY;touchY=null;if(Math.abs(delta)>65)step(delta>0?1:-1);},{passive:true});
  video.addEventListener('error',()=>{if(dialog.open&&video.hasAttribute('src'))playerStatus.textContent='视频暂时无法播放，请重新打开或下载原片。';});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)video.pause();});
  window.addEventListener('pagehide',()=>{release();observer.disconnect();});
})();
