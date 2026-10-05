/* mira homepage: production homepage, original media, bilingual navigation. */
(()=>{'use strict';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const film=$('#film'),poster=$('#poster'),cover=$('.cover');
const reduced=matchMedia('(prefers-reduced-motion: reduce)'),narrow=matchMedia('(max-width:700px)'),fine=matchMedia('(hover:hover) and (pointer:fine)');
const words={
 zh:{pageTitle:'mira — 氛围感 AI 时尚总监',tagline:'「你的氛围感 AI 时尚总监」',brandLine:'YOUR AI\nFASHION DIRECTOR',login:'登录',menu:'打开全屏菜单',close:'关闭',language:'选择语言',previous:'上一个作品',next:'下一个',nextLabel:'下一个作品',play:'播放',pause:'暂停',fullscreen:'网页全屏',exitFullscreen:'退出全屏',fullscreenError:'当前浏览器不支持网页全屏，请在浏览器菜单中开启全屏。',soundOn:'开启声音',soundOff:'静音',crop:'原片全屏裁切预览',film:'MIRA / FASHION FILM',still:'MIRA / FILM STILL',empty:'让衣服，拥有自己的氛围。',emptyNote:'作品暂时无法加载，请稍后重试',city:'街头，自有态度。',dusk:'暮色，自成氛围。',daylight:'日光，慢一点。',palace:'朱红映冬。',windowlight:'窗边时光。',noticeLabel:'最新动态',notice:'首页交互更新 · 中英文与品牌光标',privacy:'隐私说明',contact:'联系',more:'更多',copyright:'© 2026 mira',loginTitle:'回到你的创作现场。',loginBody:'继续制作，管理你的素材与作品。',username:'用户名',password:'密码',loginNote:'使用管理员分配的账号登录。需要账号或重置密码，请联系管理员。',menuTitle:'网站导航',menuMira:'MIRA',menuStudio:'创作',menuFilms:'作品',menuSupport:'支持',about:'关于 mira',idea:'产品理念',updates:'更新说明',create:'进入工作台',guide:'创作流程',models:'模型与画质',cityFilm:'街头篇',duskFilm:'暮色篇',daylightFilm:'日光篇',palaceFilm:'朱红篇',windowFilm:'窗景篇',allFilms:'返回作品展示',credits:'素材与署名',begin:'开始创作',backFilms:'返回作品',menuNote:'氛围感 AI 时尚总监。',ok:'知道了',videoError:'视频暂时无法播放，请切换作品或稍后重试。',frameCount:(i,n)=>`第 ${i} 个，共 ${n} 个。`},
 en:{pageTitle:'mira — Your AI Fashion Director',tagline:'Your AI fashion director',brandLine:'YOUR AI\nFASHION DIRECTOR',login:'Log in',menu:'Open full-screen menu',close:'Close',language:'Choose language',previous:'Previous film',next:'NEXT',nextLabel:'Next film',play:'Play',pause:'Pause',fullscreen:'Full screen page',exitFullscreen:'Exit full screen',fullscreenError:'Page fullscreen is unavailable here. Use the fullscreen option in your browser menu.',soundOn:'Sound on',soundOff:'Mute',crop:'Original footage · full-bleed crop',film:'MIRA / FASHION FILM',still:'MIRA / FILM STILL',empty:'Give every garment its own atmosphere.',emptyNote:'Films are temporarily unavailable. Please try again later.',city:'An attitude of its own.',dusk:'After the light.',daylight:'A slower kind of light.',palace:'Winter in vermilion.',windowlight:'By the window.',noticeLabel:'NOTICE',notice:'Homepage update · languages & brand cursor',privacy:'Privacy note',contact:'Contact',more:'More',copyright:'© 2026 mira',loginTitle:'Back to your creative space.',loginBody:'Continue creating. Keep your materials and work together.',username:'Username',password:'Password',loginNote:'Use the account provided by your administrator. Contact them for access or a password reset.',menuTitle:'Site navigation',menuMira:'MIRA',menuStudio:'STUDIO',menuFilms:'FILMS',menuSupport:'SUPPORT',about:'About mira',idea:'Our approach',updates:'What’s new',create:'Enter the workspace',guide:'Creative workflow',models:'Models & resolution',cityFilm:'City film',duskFilm:'Dusk film',daylightFilm:'Daylight film',palaceFilm:'Vermilion film',windowFilm:'Window film',allFilms:'Back to the films',credits:'Media & credits',begin:'START CREATING',backFilms:'BACK TO FILMS',menuNote:'Your AI fashion director.',ok:'Got it',videoError:'This film cannot play right now. Choose another film or try again later.',frameCount:(i,n)=>`Film ${i} of ${n}.`}
};
const info={
 zh:{privacy:['隐私说明','语言偏好保存在当前浏览器。账号用于登录工作台，上传的素材和任务由账号权限控制。请仅上传你有权使用的素材。'],contact:['联系 mira','通过帮助与反馈页提交站内反馈，管理员将在本站回复。不会发送外部邮件或消息。'],about:['关于 mira','mira 是面向女装店、红人线上店、供应链、品牌与线下服装店主的氛围感 AI 时尚总监。\n从一件衣服和人物参考出发，寻找适合的表现方式，制作有氛围的服装视频。'],idea:['产品理念','让技术留在后台，让创作回到衣服本身。\n你可以带着自己的参考开始，也可以从推荐参考中选择。人物状态、动作、场景与衣服的匹配，是判断的核心。'],updates:['首页更新说明','已加入品牌导航、中英文切换、m 标识光标、全屏菜单和原地登录弹窗。\n首页展示五条原始影片，登录后可进入创作工作台。'],guide:['创作流程','准备参考视频、衣服与人物素材，按需要选择场景，再选择模型、清晰度与时长。\n登录后可上传素材、保存草稿并提交视频任务。'],models:['模型与画质','进入工作台后，按已开通模型选择清晰度和时长。可选范围以工作台显示的实际模型能力为准。'],credits:['素材与署名','首页视频由项目所有者提供，用于本项目封面展示。mira 字标沿用本项目选定资源。\n工作台线性图标使用 Lucide，许可证随代码保留。']},
 en:{privacy:['Privacy note','Language preferences are saved in this browser. Your account controls access to the workspace, uploaded materials and tasks. Upload only materials you are authorized to use.'],contact:['Contact mira','Submit a support request on the Help page. An administrator replies in the workspace; no external email or message is sent.'],about:['About mira','mira is an AI fashion director for womenswear stores, creator-led shops, suppliers, brands and independent retailers.\nStart with a garment and a model reference, find the right expression, and create atmospheric fashion films.'],idea:['Our approach','Keep the technology behind the scenes. Put the garment at the center of the creative decision.\nBring your own reference or choose from recommendations. The relationship between the garment, performance, movement and setting guides the work.'],updates:['Homepage update','Brand navigation, Chinese/English switching, an m cursor, a full-screen menu and in-place login are now included.\nThe homepage includes five original films. Log in to start creating in the workspace.'],guide:['Creative workflow','Log in, upload your reference video, clothing and person materials, then choose a model and video settings. Save a draft or submit a video task.'],models:['Models & resolution','Available models, resolutions and durations follow the capabilities enabled in your workspace.'],credits:['Media & credits','The project owner supplied the homepage films for this project’s cover. The mira wordmark is the selected project asset.\nWorkspace icons use Lucide; its license is included with the code.']}
};
let lang='zh';try{lang=localStorage.getItem('mira-language')==='en'?'en':'zh'}catch{}
const t=key=>words[lang][key];
const curated=[{url:'/static/mira/cover/film-04-original.mp4',key:'palace',video:true,position:'50% 50%',mobilePosition:'50% 50%'},{url:'/static/mira/cover/film-city-original.mp4',key:'city',video:true,silent:true,poster:'/static/mira/cover/still-city-original.png',position:'50% 20%'},{url:'/static/mira/cover/film-dusk-original.mp4',key:'dusk',video:true,silent:true,poster:'/static/mira/cover/still-dusk-original.png',position:'50% 40%'},{url:'/static/mira/cover/film-03-original.mp4',key:'daylight',video:true,position:'50% 50%',mobilePosition:'70% 50%'},{url:'/static/mira/cover/film-05-original.mp4',key:'windowlight',video:true,position:'50% 50%',mobilePosition:'50% 50%'}];
let slides=[...curated],index=Math.max(0,curated.findIndex(s=>s.key===new URLSearchParams(location.search).get('film'))),mediaFailed=false,currentInfo='about',userPaused=false,fullscreenFailed=false;
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
 $('#preview-note').textContent=mediaFailed?t('videoError'):fullscreenFailed?t('fullscreenError'):'';$('#preview-note').hidden=!mediaFailed&&!fullscreenFailed;
 iconControl('play',film.paused?t('play'):t('pause'),film.paused?'M8 5l11 7-11 7Z':'M8 5v14M16 5v14');
 iconControl('sound',film.muted?t('soundOn'):t('soundOff'),film.muted?'M11 5 6 9H3v6h3l5 4ZM16 9l5 6m0-6-5 6':'M11 5 6 9H3v6h3l5 4ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14');
 iconControl('fullscreen',pageIsFullscreen()?t('exitFullscreen'):t('fullscreen'),pageIsFullscreen()?'M3 8h5V3m8 0v5h5M3 16h5v5m8 0v-5h5':'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5');
 $('#fullscreen').hidden=false;$('#fullscreen').setAttribute('aria-pressed',String(pageIsFullscreen()));
 $('#announcement').textContent=slides.length?t('frameCount')(index+1,slides.length):t('emptyNote');
 $('#info-title').textContent=info[lang][currentInfo][0];$('#info-copy').textContent=info[lang][currentInfo][1];

}
function setLanguage(value){lang=value==='en'?'en':'zh';try{localStorage.setItem('mira-language',lang)}catch{}syncText();closeLanguage();}
function positionMedia(){film.style.objectPosition=poster.style.objectPosition=narrow.matches?(slides[index]?.mobilePosition||'50% 35%'):(slides[index]?.position||'center')}
function resumeFilm(){if(!document.querySelector('dialog[open]')&&slides[index]?.video&&!userPaused&&!reduced.matches&&!document.hidden&&film.paused)film.play().catch(()=>syncText())}
function render(){const s=slides[index];mediaFailed=false;userPaused=false;film.pause();film.removeAttribute('src');film.removeAttribute('poster');film.load();document.body.classList.toggle('has-media',!!s);poster.hidden=!s||s.video;film.hidden=!s?.video;$('#play').hidden=!s?.video;$('#sound').hidden=!s?.video||!!s?.silent;
 $('#current').textContent=String(slides.length?index+1:0).padStart(2,'0');$('#total').textContent=String(slides.length).padStart(2,'0');$('#previous').disabled=$('#next').disabled=slides.length<2;
 if(s?.video){film.src=s.url;if(s.poster)film.poster=s.poster;film.muted=true;resumeFilm();}else if(s){poster.src=s.url;poster.alt=s.key?t(s.key):s.name}else poster.removeAttribute('src');positionMedia();syncText();}
function navigate(delta){if(!slides.length)return;index=(index+delta+slides.length)%slides.length;render()}
$('#next').onclick=()=>navigate(1);$('#previous').onclick=()=>navigate(-1);
$('#play').onclick=()=>{if(film.paused){userPaused=false;film.play().catch(()=>{mediaFailed=true;syncText()})}else{userPaused=true;film.pause()}};
film.addEventListener('play',syncText);film.addEventListener('pause',syncText);film.addEventListener('error',()=>{if(film.getAttribute('src')){mediaFailed=true;syncText()}});
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
function openDialog(id,origin=document.activeElement){closeLanguage();dialogSwitch=true;$$('dialog[open]').forEach(d=>d.close());dialogSwitch=false;const d=$('#'+id);returnFocus.set(d,origin);hideCursor();film.pause();d.showModal();if(id==='login-dialog')$('#login-username').focus();}
$$('dialog').forEach(d=>{let down=false;const outside=e=>{const r=d.getBoundingClientRect();return e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom};d.addEventListener('pointerdown',e=>down=e.target===d&&outside(e));d.addEventListener('click',e=>{if(down&&e.target===d&&outside(e))d.close();down=false});d.addEventListener('close',()=>{if(d.id==='login-dialog')$('#login-password').value='';resumeFilm();if(!dialogSwitch&&!document.querySelector('dialog[open]')){const target=returnFocus.get(d);if(target&&target.getClientRects().length)target.focus();else $('#menu-trigger').focus()}})});
function showInfo(key){if(key==='contact'){window.location.assign('/help#feedback');return;}currentInfo=key;syncText();openDialog('info-dialog')}
document.addEventListener('click',e=>{const el=e.target.closest('[data-action]');if(!el)return;const action=el.dataset.action;if(action==='login')openDialog('login-dialog');if(action==='menu')openDialog('site-menu');if(action==='close')el.closest('dialog').close();if(action==='films'){$$('dialog[open]').forEach(d=>d.close());$('#next').focus()}if(curated.some(s=>s.key===action)){$$('dialog[open]').forEach(d=>d.close());if(slides!==curated){slides=[...curated]}index=curated.findIndex(s=>s.key===action);render();$('#next').focus()}if(action.startsWith('info:'))showInfo(action.slice(5))});
document.addEventListener('keydown',e=>{if(e.key==='Tab')hideCursor();if(e.key==='Escape'&&!languageList.hidden){closeLanguage();languageTrigger.focus()}if(document.querySelector('dialog[open]')||!languageList.hidden||e.target.closest('button,a,input,textarea,select'))return;if(e.key==='ArrowRight'){e.preventDefault();navigate(1)}if(e.key==='ArrowLeft'){e.preventDefault();navigate(-1)}});
// Touch swipes have the same result as the visible arrow buttons.
let touchStart=null;cover.addEventListener('pointerdown',e=>{if(e.pointerType==='touch'&&!e.target.closest('button,a'))touchStart={x:e.clientX,y:e.clientY}});
cover.addEventListener('pointerup',e=>{if(e.pointerType!=='touch'||!touchStart)return;const dx=e.clientX-touchStart.x,dy=e.clientY-touchStart.y;touchStart=null;if(Math.abs(dx)>65&&Math.abs(dx)>Math.abs(dy)*1.6)navigate(dx<0?1:-1)});cover.addEventListener('pointercancel',()=>touchStart=null);
// Only NEXT replaces the system pointer. No cursor effect overlays the MV.
const cursor=$('#mira-cursor'),nextButton=$('#next');let cursorFrame=0,cx=0,cy=0;
function hideCursor(){document.body.classList.remove('cursor-active');if(cursorFrame){cancelAnimationFrame(cursorFrame);cursorFrame=0}}
nextButton.addEventListener('pointermove',e=>{
 if(e.pointerType!=='mouse'||!fine.matches||reduced.matches||nextButton.disabled||document.querySelector('dialog[open]')){hideCursor();return}
 cx=e.clientX;cy=e.clientY;if(!cursorFrame)cursorFrame=requestAnimationFrame(()=>{cursorFrame=0;cursor.style.transform=`translate3d(${cx}px,${cy}px,0)`;document.body.classList.add('cursor-active')});
},{passive:true});
nextButton.addEventListener('pointerleave',hideCursor);
window.addEventListener('blur',hideCursor);fine.addEventListener('change',hideCursor);
reduced.addEventListener('change',()=>{hideCursor();if(reduced.matches)film.pause();else resumeFilm()});narrow.addEventListener('change',positionMedia);
// Hidden tabs pause, visible tabs resume — unless the visitor explicitly paused.
document.addEventListener('visibilitychange',()=>{if(document.hidden){film.pause();hideCursor()}else resumeFilm()});
window.addEventListener('pagehide',()=>{film.pause();hideCursor();$('#login-password').value=''});
window.addEventListener('pageshow',resumeFilm);window.addEventListener('focus',resumeFilm);
// Some mobile browsers require a user gesture before allowing playback.
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#play'))resumeFilm()},{passive:true});
window.accountReady.catch(()=>openDialog('login-dialog',$('#open-login')));
render();
})();
