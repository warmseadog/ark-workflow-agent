/* mira homepage: local-only preview, original media, bilingual chrome. */
(()=>{'use strict';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const film=$('#film'),poster=$('#poster'),cover=$('.cover');
const reduced=matchMedia('(prefers-reduced-motion: reduce)'),narrow=matchMedia('(max-width:700px)'),fine=matchMedia('(hover:hover) and (pointer:fine)');
const words={
 zh:{pageTitle:'mira — 氛围感 AI 时尚总监',tagline:'「你的氛围感 AI 时尚总监」',brandLine:'YOUR AI\nFASHION DIRECTOR',login:'登录',menu:'打开全屏菜单',close:'关闭',language:'选择语言',previous:'上一个作品',next:'下一个',nextLabel:'下一个作品',play:'播放',pause:'暂停',fullscreen:'网页全屏',exitFullscreen:'退出全屏',fullscreenError:'当前浏览器不支持网页全屏，请在浏览器菜单中开启全屏。',soundOn:'开启声音',soundOff:'静音',crop:'原片全屏裁切预览',local:'本地素材预览 · 未上传',film:'MIRA / FASHION FILM',still:'MIRA / FILM STILL',empty:'让衣服，拥有自己的氛围。',emptyNote:'在预览设置中添加 MV 或海报',city:'街头，自有态度。',dusk:'暮色，自成氛围。',daylight:'日光，慢一点。',palace:'朱红映冬。',windowlight:'窗边时光。',noticeLabel:'最新动态',notice:'首页交互更新 · 中英文与品牌光标',privacy:'隐私说明',contact:'联系',more:'更多',copyright:'© 2026 mira',settings:'预览设置',settingsTitle:'替换封面素材',settingsBody:'选择多张海报或视频后，可以左右切换。文件仅在当前浏览器使用，不上传。',files:'选择 MV 或海报（可多选）',fileLimit:'图片最大 20 MB，视频最大 200 MB。页码按真实数量显示。',reset:'恢复首页 MV',settingsNote:'当前为视觉预览，正式登录与生成服务尚未接入。',loginTitle:'回到你的创作现场。',loginBody:'继续制作，管理你的素材与作品。',username:'用户名',password:'密码',userPlaceholder:'正式登录服务尚未接入',passwordPlaceholder:'此预览不收集账号或密码',workspacePreview:'查看工作台预览 →',loginNote:'当前仅为登录框视觉预览，不会发送账号或密码。',menuTitle:'网站导航',menuMira:'MIRA',menuStudio:'创作',menuFilms:'作品',menuSupport:'支持',about:'关于 mira',idea:'产品理念',updates:'更新说明',create:'进入工作台',guide:'创作流程',models:'模型与画质',cityFilm:'街头篇',duskFilm:'暮色篇',daylightFilm:'日光篇',palaceFilm:'朱红篇',windowFilm:'窗景篇',allFilms:'返回作品展示',credits:'素材与署名',begin:'开始创作',backFilms:'返回作品',menuNote:'氛围感 AI 时尚总监。',ok:'知道了',loading:'正在读取本地素材…',readError:'以下文件未读入，请检查格式或大小：',videoError:'视频无法播放，请在预览设置中更换素材。',frameCount:(i,n)=>`第 ${i} 个，共 ${n} 个。`},
 en:{pageTitle:'mira — Your AI Fashion Director',tagline:'Your AI fashion director',brandLine:'YOUR AI\nFASHION DIRECTOR',login:'Log in',menu:'Open full-screen menu',close:'Close',language:'Choose language',previous:'Previous film',next:'NEXT',nextLabel:'Next film',play:'Play',pause:'Pause',fullscreen:'Full screen page',exitFullscreen:'Exit full screen',fullscreenError:'Page fullscreen is unavailable here. Use the fullscreen option in your browser menu.',soundOn:'Sound on',soundOff:'Mute',crop:'Original footage · full-bleed crop',local:'Local preview · not uploaded',film:'MIRA / FASHION FILM',still:'MIRA / FILM STILL',empty:'Give every garment its own atmosphere.',emptyNote:'Add a film or poster in Preview settings',city:'An attitude of its own.',dusk:'After the light.',daylight:'A slower kind of light.',palace:'Winter in vermilion.',windowlight:'By the window.',noticeLabel:'NOTICE',notice:'Homepage update · languages & brand cursor',privacy:'Privacy note',contact:'Contact',more:'More',copyright:'© 2026 mira',settings:'Preview settings',settingsTitle:'Choose cover media',settingsBody:'Select multiple films or posters to browse between them. Files stay in this browser and are not uploaded.',files:'Choose films or posters',fileLimit:'Images up to 20 MB; videos up to 200 MB. The counter reflects the actual number of files.',reset:'Restore cover films',settingsNote:'Visual preview only. Authentication and generation services are not connected.',loginTitle:'Back to your creative space.',loginBody:'Continue creating. Keep your materials and work together.',username:'Username',password:'Password',userPlaceholder:'Authentication is not connected',passwordPlaceholder:'This preview does not collect passwords',workspacePreview:'Preview the workspace →',loginNote:'Visual preview only. No account details or passwords are sent.',menuTitle:'Site navigation',menuMira:'MIRA',menuStudio:'STUDIO',menuFilms:'FILMS',menuSupport:'SUPPORT',about:'About mira',idea:'Our approach',updates:'What’s new',create:'Enter the workspace',guide:'Creative workflow',models:'Models & resolution',cityFilm:'City film',duskFilm:'Dusk film',daylightFilm:'Daylight film',palaceFilm:'Vermilion film',windowFilm:'Window film',allFilms:'Back to the films',credits:'Media & credits',begin:'START CREATING',backFilms:'BACK TO FILMS',menuNote:'Your AI fashion director.',ok:'Got it',loading:'Reading local media…',readError:'Could not read these files. Check their format or size: ',videoError:'This video cannot play. Choose another file in Preview settings.',frameCount:(i,n)=>`Film ${i} of ${n}.`}
};
const info={
 zh:{privacy:['隐私说明','本地选择的封面文件仅在当前浏览器中读取，不上传。语言偏好保存在本机浏览器。\n当前登录框不收集账号或密码，工作台为视觉预览，未接入生产服务。正式服务上线前须另行提供对应隐私政策。'],contact:['联系 mira','正式联系邮箱与业务咨询渠道尚未配置。此处保留联系入口，不会将你的信息发送给任何第三方。'],about:['关于 mira','mira 是面向女装店、红人线上店、供应链、品牌与线下服装店主的氛围感 AI 时尚总监。\n从一件衣服和人物参考出发，寻找适合的表现方式，制作有氛围的服装视频。'],idea:['产品理念','让技术留在后台，让创作回到衣服本身。\n你可以带着自己的参考开始，也可以从推荐参考中选择。人物状态、动作、场景与衣服的匹配，是判断的核心。'],updates:['首页更新说明','已加入品牌导航、中英文切换、m 标识光标、全屏菜单和原地登录弹窗。\n当前 MV 为原片全屏裁切展示，不是原生横屏重生成；真实登录与生成服务尚未接入此预览。'],guide:['创作流程','准备参考视频、衣服与人物素材，按需要选择场景，再选择模型、清晰度与时长。\n当前工作台仅供查看视觉与布局，不会上传或生成。'],models:['模型与画质','产品计划提供模型、清晰度与时长选择；可选项必须以实际接入模型的能力为准。\n此预览尚未连接模型服务，不展示虚构的模型价格或生成能力。'],credits:['素材与署名','首页视频由项目所有者提供，用于本项目封面展示。mira 字标沿用本项目选定资源。\n工作台线性图标使用 Lucide，许可证随代码保留。']},
 en:{privacy:['Privacy note','Cover files selected locally are read only in this browser and are not uploaded. Your language preference is saved in this browser.\nThis login preview does not collect credentials. The workspace is a visual preview, not a connected production service. A service-specific privacy policy must be supplied before launch.'],contact:['Contact mira','A contact email and business enquiry channel have not been configured. This placeholder does not send your information to a third party.'],about:['About mira','mira is an AI fashion director for womenswear stores, creator-led shops, suppliers, brands and independent retailers.\nStart with a garment and a model reference, find the right expression, and create atmospheric fashion films.'],idea:['Our approach','Keep the technology behind the scenes. Put the garment at the center of the creative decision.\nBring your own reference or choose from recommendations. The relationship between the garment, performance, movement and setting guides the work.'],updates:['Homepage update','Brand navigation, Chinese/English switching, an m cursor, a full-screen menu and in-place login are now included.\nThe films use full-bleed crops of the originals, not regenerated landscape footage. Authentication and generation are not connected to this preview.'],guide:['Creative workflow','Prepare a reference film, garment images and model references. Choose a setting if needed, then a model, resolution and duration.\nThe current workspace previews the layout and visual design only. It does not upload or generate media.'],models:['Models & resolution','The product is designed to offer model, resolution and duration controls. Available options must come from the connected models’ actual capabilities.\nNo generation service is connected here. This preview does not invent prices or model capabilities.'],credits:['Media & credits','The project owner supplied the homepage films for this project’s cover. The mira wordmark is the selected project asset.\nWorkspace icons use Lucide; its license is included with the code.']}
};
let lang='zh';try{lang=localStorage.getItem('mira-language')==='en'?'en':'zh'}catch{}
const t=key=>words[lang][key];
Object.assign(words.zh,{menuCases:'案例',backCases:'← 案例',backFilms:'返回首页',caseRoster:'MIRA · 虚拟人',caseLabel:'虚拟人作品集',caseDescription:'影像、图集，以及她的线上空间。',caseVideos:'视频作品',caseImages:'图集',caseLinks:'相关链接',casePending:'作品整理中',caseNoVideos:'视频作品将在确认素材归属后展示。',caseNoImages:'图集将在确认素材归属后展示。',caseNoLinks:'账号与作品链接将在确认后展示。'});
Object.assign(words.en,{menuCases:'CASES',backCases:'← Cases',backFilms:'BACK TO HOME',caseRoster:'MIRA · VIRTUAL TALENT',caseLabel:'Virtual talent portfolio',caseDescription:'Films, photographs, and her online world.',caseVideos:'Films',caseImages:'Gallery',caseLinks:'Links',casePending:'Portfolio in preparation',caseNoVideos:'Films will appear once their attribution is confirmed.',caseNoImages:'Photographs will appear once their attribution is confirmed.',caseNoLinks:'Verified profile and portfolio links will appear here.'});
const cases=window.MIRA_CASES||[];let activeCase=cases[0],caseFilter='videos';
const curated=[{url:'assets/cover/film-03-original.mp4',key:'daylight',video:true,position:'50% 50%',mobilePosition:'70% 50%'},{url:'assets/cover/film-04-original.mp4',key:'palace',video:true,position:'50% 50%',mobilePosition:'50% 50%'},{url:'assets/cover/film-city-original.mp4',key:'city',video:true,silent:true,poster:'assets/cover/still-city-original.png',position:'50% 20%'},{url:'assets/cover/film-dusk-original.mp4',key:'dusk',video:true,silent:true,poster:'assets/cover/still-dusk-original.png',position:'50% 40%'},{url:'assets/cover/film-05-original.mp4',key:'windowlight',video:true,position:'50% 50%',mobilePosition:'50% 50%'}];
let slides=[...curated],index=Math.max(0,curated.findIndex(s=>s.key===new URLSearchParams(location.search).get('film'))),revision=0,mediaFailed=false,currentInfo='about',errorState=null,userPaused=false,fullscreenFailed=false;
let loadTicket=0,playAttempt=null,advanceTimer=0,playbackBlocked=false,motionOptIn=false;
const failedSlides=new Set();
words.zh.videoError='这条 MV 暂时无法读取，可切换下一条或稍后重试。';
words.en.videoError='This film is temporarily unavailable. Try the next film or retry later.';
words.zh.autoplayBlocked='浏览器限制了自动播放，点播放即可继续。';
words.en.autoplayBlocked='Your browser restricted autoplay. Press play to continue.';
function pageIsFullscreen(){return !!(document.fullscreenElement||document.webkitFullscreenElement)}
function iconControl(id,label,path){const button=$('#'+id);button.setAttribute('aria-label',label);button.title=label;button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="'+path+'"/></svg>'}
function syncText(){
 document.documentElement.lang=lang==='zh'?'zh-CN':'en';document.title=t('pageTitle');
 $$('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));
 $$('[data-i18n-label]').forEach(el=>el.setAttribute('aria-label',t(el.dataset.i18nLabel)));
 $$('[data-i18n-placeholder]').forEach(el=>el.setAttribute('placeholder',t(el.dataset.i18nPlaceholder)));
 $('#language-code').textContent=lang==='zh'?'中':'EN';
 $$('[data-language]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.language===lang)));
 $('#title').textContent=slides[index]?(slides[index].key?t(slides[index].key):slides[index].name):t('empty');
 $('#kind').textContent=slides[index]?.video?t('film'):t('still');
 $('#preview-note').textContent=mediaFailed?t('videoError'):playbackBlocked?t('autoplayBlocked'):fullscreenFailed?t('fullscreenError'):'';$('#preview-note').hidden=!mediaFailed&&!playbackBlocked&&!fullscreenFailed;
 iconControl('play',film.paused||mediaFailed?t('play'):t('pause'),film.paused||mediaFailed?'M8 5l11 7-11 7Z':'M8 5v14M16 5v14');
 iconControl('sound',film.muted?t('soundOn'):t('soundOff'),film.muted?'M11 5 6 9H3v6h3l5 4ZM16 9l5 6m0-6-5 6':'M11 5 6 9H3v6h3l5 4ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14');
 iconControl('fullscreen',pageIsFullscreen()?t('exitFullscreen'):t('fullscreen'),pageIsFullscreen()?'M3 8h5V3m8 0v5h5M3 16h5v5m8 0v-5h5':'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5');
 $('#fullscreen').hidden=false;$('#fullscreen').setAttribute('aria-pressed',String(pageIsFullscreen()));
 $('#announcement').textContent=slides.length?t('frameCount')(index+1,slides.length):t('emptyNote');
 $('#info-title').textContent=info[lang][currentInfo][0];$('#info-copy').textContent=info[lang][currentInfo][1];
 $('#error').textContent=errorState==='loading'?t('loading'):Array.isArray(errorState)?t('readError')+errorState.join('、'):'';
 if($('#case-dialog').open)renderCase();
}
function setLanguage(value){lang=value==='en'?'en':'zh';try{localStorage.setItem('mira-language',lang)}catch{}syncText();closeLanguage();}
function positionMedia(){film.style.objectPosition=poster.style.objectPosition=narrow.matches?(slides[index]?.mobilePosition||'50% 35%'):(slides[index]?.position||'center')}
function canAdvance(){return !userPaused&&(!reduced.matches||motionOptIn)&&!document.hidden&&!$('#case-dialog').open}
function resumeFilm(){
 if(!slides[index]?.video||!canAdvance()||mediaFailed||!film.paused||playAttempt)return;
 const ticket=loadTicket,attempt=film.play();playAttempt=attempt;
 attempt.catch(error=>{
  // Switching sources or pausing cancels an old play promise; neither is a broken MV.
  if(ticket!==loadTicket||error.name==='AbortError')return;
  if(error.name==='NotAllowedError'){playbackBlocked=true;syncText()}
 }).finally(()=>{if(playAttempt===attempt)playAttempt=null});
}
function scheduleAdvance(delay){clearTimeout(advanceTimer);const ticket=loadTicket;advanceTimer=setTimeout(()=>{if(ticket===loadTicket&&canAdvance())advanceFilm()},delay)}
function advanceFilm(){
 if(!canAdvance()||slides.length<2)return;
 for(let offset=1;offset<=slides.length;offset++){
  const next=(index+offset)%slides.length;
  if(!failedSlides.has(slides[next].url)){index=next;render();return}
 }
 // Stop when all sources failed; do not spin through failed requests forever.
}
function render(){const s=slides[index];loadTicket++;clearTimeout(advanceTimer);playAttempt=null;mediaFailed=false;playbackBlocked=false;userPaused=false;film.pause();film.removeAttribute('poster');film.autoplay=!reduced.matches||motionOptIn;film.defaultMuted=true;film.muted=true;film.loop=false;document.body.classList.toggle('has-media',!!s);poster.hidden=!s||s.video;film.hidden=!s?.video;$('#play').hidden=!s?.video;$('#sound').hidden=!s?.video||!!s?.silent;
 $('#current').textContent=String(slides.length?index+1:0).padStart(2,'0');$('#total').textContent=String(slides.length).padStart(2,'0');$('#previous').disabled=$('#next').disabled=slides.length<2;
 if(s?.video){if(s.poster)film.poster=s.poster;film.src=s.url;film.load();resumeFilm();}else{film.removeAttribute('src');film.load();if(s){poster.src=s.url;poster.alt=s.key?t(s.key):s.name;scheduleAdvance(6000)}else poster.removeAttribute('src')}positionMedia();syncText();}
function navigate(delta){if(!slides.length)return;failedSlides.clear();index=(index+delta+slides.length)%slides.length;render()}
$('#next').onclick=()=>navigate(1);$('#previous').onclick=()=>navigate(-1);
$('#play').onclick=()=>{if(film.paused||mediaFailed){userPaused=false;motionOptIn=true;playbackBlocked=false;if(mediaFailed){failedSlides.delete(slides[index].url);render()}else resumeFilm()}else{userPaused=true;film.autoplay=false;clearTimeout(advanceTimer);film.pause()}};
film.addEventListener('play',syncText);film.addEventListener('pause',syncText);
film.addEventListener('loadeddata',resumeFilm);film.addEventListener('canplay',resumeFilm);
film.addEventListener('playing',()=>{mediaFailed=false;playbackBlocked=false;failedSlides.delete(slides[index].url);syncText()});
film.addEventListener('ended',advanceFilm);
film.addEventListener('error',()=>{if(film.getAttribute('src')&&film.error){mediaFailed=true;failedSlides.add(slides[index].url);syncText();scheduleAdvance(1800)}});
$('#sound').onclick=()=>{film.muted=!film.muted;syncText()};
// Fullscreen the document so the MV, logo, navigation and dialogs stay together.
$('#fullscreen').onclick=async()=>{
 hideCursor();fullscreenFailed=false;
 try{
  if(pageIsFullscreen()){if(document.exitFullscreen)await document.exitFullscreen();else document.webkitExitFullscreen()}
  else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();
  else if(document.documentElement.webkitRequestFullscreen)document.documentElement.webkitRequestFullscreen();
  else throw new Error('Page fullscreen unavailable');
 }catch{fullscreenFailed=true}
 syncText();if(fullscreenFailed)$('#announcement').textContent=t('fullscreenError');
};
document.addEventListener('fullscreenchange',syncText);
document.addEventListener('webkitfullscreenchange',syncText);
const languageList=$('#language-list'),languageTrigger=$('#language-trigger');
function closeLanguage(){languageList.hidden=true;languageTrigger.setAttribute('aria-expanded','false')}
languageTrigger.onclick=()=>{languageList.hidden=!languageList.hidden;languageTrigger.setAttribute('aria-expanded',String(!languageList.hidden))};
document.addEventListener('click',e=>{if(!e.target.closest('.language-control'))closeLanguage();const el=e.target.closest('[data-language]');if(el)setLanguage(el.dataset.language)});
languageList.addEventListener('keydown',e=>{if(e.key==='Escape'){closeLanguage();languageTrigger.focus()}});
const returnFocus=new WeakMap();let dialogSwitch=false;
function openDialog(id,origin=document.activeElement){closeLanguage();dialogSwitch=true;$$('dialog[open]').forEach(d=>d.close());dialogSwitch=false;const d=$('#'+id);returnFocus.set(d,origin);hideCursor();d.showModal();}
$$('dialog').forEach(d=>{let down=false;const outside=e=>{const r=d.getBoundingClientRect();return e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom};d.addEventListener('pointerdown',e=>down=e.target===d&&outside(e));d.addEventListener('click',e=>{if(down&&e.target===d&&outside(e))d.close();down=false});d.addEventListener('close',()=>{if(!dialogSwitch&&!document.querySelector('dialog[open]')){const target=returnFocus.get(d);if(target&&target.getClientRects().length)target.focus();else $('#menu-trigger').focus()}})});
function showInfo(key){currentInfo=key;syncText();openDialog('info-dialog')}
function safeCaseURL(value){try{const url=new URL(value,location.href);return ['http:','https:'].includes(url.protocol)?url.href:null}catch{return null}}
function renderCase(){
 if(!activeCase)return;
 $('#case-name').textContent=activeCase.name;
 $('.case-filters').setAttribute('aria-label',lang==='zh'?'作品类型':'Portfolio categories');
 $$('[data-case-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.caseFilter===caseFilter)));
 const content=$('#case-content');content.querySelectorAll('video').forEach(video=>video.pause());content.replaceChildren();
 const entries=(activeCase[caseFilter]||[]).filter(item=>safeCaseURL(item.src||item.url));
 if(!entries.length){
  const empty=document.createElement('div');empty.className='case-empty';const title=document.createElement('h3');title.textContent=t('casePending');const note=document.createElement('p');note.textContent=t({videos:'caseNoVideos',images:'caseNoImages',links:'caseNoLinks'}[caseFilter]);empty.append(title,note);content.append(empty);return;
 }
 const list=document.createElement('div');list.className=caseFilter==='links'?'case-links':'case-grid';
 for(const item of entries){
  if(caseFilter==='links'){
   const link=document.createElement('a');link.className='case-link';link.href=safeCaseURL(item.url);link.target='_blank';link.rel='noopener noreferrer';const name=document.createElement('span');name.textContent=item.title||new URL(link.href).hostname;const arrow=document.createElement('span');arrow.textContent='↗';arrow.setAttribute('aria-hidden','true');link.append(name,arrow);list.append(link);
  }else{
   const figure=document.createElement('figure'),media=document.createElement(caseFilter==='videos'?'video':'img');media.src=safeCaseURL(item.src);
   if(caseFilter==='videos'){media.controls=true;media.playsInline=true;media.preload='metadata';if(item.poster&&safeCaseURL(item.poster))media.poster=safeCaseURL(item.poster);media.addEventListener('play',()=>content.querySelectorAll('video').forEach(other=>{if(other!==media)other.pause()}))}
   else{media.alt=item.alt||item.title||activeCase.name;media.loading='lazy';const link=document.createElement('a');link.href=media.src;link.target='_blank';link.rel='noopener noreferrer';link.append(media);figure.append(link)}
   if(caseFilter==='videos')figure.append(media);if(item.title){const caption=document.createElement('figcaption');caption.textContent=item.title;figure.append(caption)}list.append(figure);
  }
 }
 content.append(list);
}
document.addEventListener('click',event=>{
 const personButton=event.target.closest('[data-case]');
 if(personButton){const person=cases.find(item=>item.id===personButton.dataset.case);if(!person)return;activeCase=person;caseFilter='videos';if(!$('#case-dialog').open)openDialog('case-dialog',personButton);film.pause();syncText()}
 const filterButton=event.target.closest('[data-case-filter]');
 if(filterButton){caseFilter=filterButton.dataset.caseFilter;renderCase()}
});
$('#case-dialog').addEventListener('close',()=>{$$('#case-content video').forEach(video=>video.pause());resumeCover()});
document.addEventListener('click',e=>{const el=e.target.closest('[data-action]');if(!el)return;const action=el.dataset.action;if(action==='login')openDialog('login-dialog');if(action==='menu')openDialog('site-menu');if(action==='settings')openDialog('settings-dialog');if(action==='close')el.closest('dialog').close();if(action==='films'){$$('dialog[open]').forEach(d=>d.close());$('#next').focus()}if(curated.some(s=>s.key===action)){$$('dialog[open]').forEach(d=>d.close());if(slides!==curated){dispose(slides);slides=[...curated]}index=curated.findIndex(s=>s.key===action);render();$('#next').focus()}if(action.startsWith('info:'))showInfo(action.slice(5))});
document.addEventListener('keydown',e=>{if(e.key==='Tab')hideCursor();if(e.key==='Escape'&&!languageList.hidden){closeLanguage();languageTrigger.focus()}if(document.querySelector('dialog[open]')||!languageList.hidden||e.target.closest('button,a,input,textarea,select'))return;if(e.key==='ArrowRight'){e.preventDefault();navigate(1)}if(e.key==='ArrowLeft'){e.preventDefault();navigate(-1)}});
// Touch swipes have the same result as the visible arrow buttons.
let touchStart=null;cover.addEventListener('pointerdown',e=>{if(e.pointerType==='touch'&&!e.target.closest('button,a'))touchStart={x:e.clientX,y:e.clientY}});
cover.addEventListener('pointerup',e=>{if(e.pointerType!=='touch'||!touchStart)return;const dx=e.clientX-touchStart.x,dy=e.clientY-touchStart.y;touchStart=null;if(Math.abs(dx)>65&&Math.abs(dx)>Math.abs(dy)*1.6)navigate(dx<0?1:-1)});cover.addEventListener('pointercancel',()=>touchStart=null);
function dispose(list){list.forEach(s=>{if(s.url.startsWith('blob:'))URL.revokeObjectURL(s.url)})}
$('#reset').onclick=()=>{revision++;dispose(slides);slides=[...curated];index=0;errorState=null;$('#files').value='';render();$('#settings-dialog').close()};
function readable(url,video){return new Promise(resolve=>{const node=document.createElement(video?'video':'img');let done=false;const timer=setTimeout(()=>finish(false),8000);function finish(ok){if(done)return;done=true;clearTimeout(timer);node.onload=node.onloadedmetadata=node.onerror=null;node.removeAttribute('src');if(video)node.load();resolve(ok)}node.onerror=()=>finish(false);if(video){node.preload='metadata';node.onloadedmetadata=()=>finish(true)}else node.onload=()=>finish(node.naturalWidth>0);node.src=url})}
$('#files').onchange=async e=>{const files=[...e.target.files];e.target.value='';if(!files.length)return;const ticket=++revision,accepted=[],errors=[];errorState='loading';syncText();for(const file of files){const video=file.type.startsWith('video/');if(!['image/jpeg','image/png','image/webp','video/mp4','video/webm','video/quicktime'].includes(file.type)||file.size>(video?200:20)*1024*1024){errors.push(file.name);continue}const url=URL.createObjectURL(file);if(await readable(url,video))accepted.push({url,name:file.name,video});else{URL.revokeObjectURL(url);errors.push(file.name)}}if(ticket!==revision){dispose(accepted);return}if(accepted.length){dispose(slides);slides=accepted;index=0;render()}errorState=errors.length?errors:null;syncText();if(!errors.length)$('#settings-dialog').close()};
// Only NEXT replaces the system pointer. No cursor effect overlays the MV.
const cursor=$('#mira-cursor'),nextButton=$('#next');let cursorFrame=0,cx=0,cy=0;
function hideCursor(){document.body.classList.remove('cursor-active');if(cursorFrame){cancelAnimationFrame(cursorFrame);cursorFrame=0}}
nextButton.addEventListener('pointermove',e=>{
 if(e.pointerType!=='mouse'||!fine.matches||reduced.matches||nextButton.disabled||document.querySelector('dialog[open]')){hideCursor();return}
 cx=e.clientX;cy=e.clientY;if(!cursorFrame)cursorFrame=requestAnimationFrame(()=>{cursorFrame=0;cursor.style.transform=`translate3d(${cx}px,${cy}px,0)`;document.body.classList.add('cursor-active')});
},{passive:true});
nextButton.addEventListener('pointerleave',hideCursor);
window.addEventListener('blur',hideCursor);fine.addEventListener('change',hideCursor);
reduced.addEventListener('change',()=>{hideCursor();motionOptIn=false;film.autoplay=!reduced.matches;if(reduced.matches){film.pause();clearTimeout(advanceTimer)}else resumeFilm()});narrow.addEventListener('change',positionMedia);
// Hidden tabs pause, visible tabs resume — unless the visitor explicitly paused.
function resumeCover(){if(!canAdvance())return;if(mediaFailed||film.ended)advanceFilm();else if(slides[index]?.video)resumeFilm();else scheduleAdvance(6000)}
document.addEventListener('visibilitychange',()=>{if(document.hidden){film.pause();clearTimeout(advanceTimer);hideCursor()}else resumeCover()});
window.addEventListener('pagehide',()=>{film.pause();hideCursor()});
window.addEventListener('pageshow',resumeCover);window.addEventListener('focus',resumeCover);
// Some mobile browsers require a user gesture before allowing playback.
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#play'))resumeFilm()},{passive:true});
render();
})();
