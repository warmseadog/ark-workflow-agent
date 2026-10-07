(() => {
  const sourceForm = document.getElementById('studio-job-form');
  const generationForm = document.getElementById('studio-generate-form');
  const promptEditable = Boolean(document.getElementById('generation-prompt'));
  const promptInput = document.getElementById('generation-prompt') || document.createElement('textarea');
  const source = sourceForm.elements.namedItem('video');
  const sourceUrl = sourceForm.elements.namedItem('video_url');
  const importButton = document.getElementById('confirm-video-url');
  const linkStatus = document.getElementById('video-link-status');
  const face = generationForm.elements.namedItem('face_image');
  const clothing = generationForm.elements.namedItem('clothing_image');
  const preview = document.getElementById('studio-preview');
  const previewVideo = document.getElementById('studio-defaced-video');
  const previewButton = document.getElementById('studio-preview-submit');
  const previewStatus = document.getElementById('studio-preview-status');
  const generateButton = document.getElementById('studio-generate-submit');
  const variationEditor = document.getElementById('variation-editor');
  const variationSubmit = document.getElementById('variation-submit');
  const variationInput = document.getElementById('variation-inspiration');
  const variationClear = document.getElementById('variation-clear');
  const inspirationAssist = document.getElementById('inspiration-assist');
  const inspirationStatus = document.getElementById('inspiration-status');
  let inspirationBusy = false, inspirationSequence = 0, inspirationEdit = 0, inspirationController = null;
  function inspirationNote(text, error = false) {
    if (!inspirationStatus) return;
    inspirationStatus.textContent = text; inspirationStatus.hidden = !text;
    inspirationStatus.dataset.error = String(error);
  }
  function invalidateInspiration() {
    inspirationSequence++;
    inspirationController?.abort(); inspirationController = null; inspirationBusy = false;
    if (inspirationAssist) { inspirationAssist.textContent = 'AI 润色'; inspirationAssist.removeAttribute('aria-busy'); }
    inspirationNote('');
  }
  function resetVariation() {
    if (!variationEditor) return;
    invalidateInspiration(); inspirationEdit++;
    variationInput.value = ''; variationEditor.hidden = false;
  }
  variationInput?.addEventListener('input', () => {
    inspirationEdit++;
    if (!inspirationBusy) inspirationNote('');
    updateButtons();
  });
  variationClear?.addEventListener('click', () => {
    resetVariation(); updateButtons(); variationInput.focus();
  });
  inspirationAssist?.addEventListener('click', async () => {
    if (inspirationAssist.disabled || inspirationBusy || pageInactive || !variationInput.value.trim()) return;
    const sequence = ++inspirationSequence, edit = inspirationEdit, draftId = draft?.id;
    const controller = new AbortController(); inspirationController = controller;
    // Backend allows at most 30 seconds; this is only a browser/network safety net.
    const timer = setTimeout(() => controller.abort(), 35000);
    inspirationBusy = true; inspirationAssist.textContent = '正在润色…';
    inspirationAssist.setAttribute('aria-busy', 'true'); inspirationNote('正在润色你的灵感…'); updateButtons();
    const current = () => sequence === inspirationSequence && draft?.id === draftId && !pageInactive;
    try {
      const data = await api('/inspiration-assist', 'POST', {inspiration: variationInput.value.trim()}, {signal: controller.signal});
      if (!current()) return;
      if (edit !== inspirationEdit) { inspirationNote('你已修改内容，AI 建议未覆盖当前输入。'); return; }
      if (typeof data.inspiration !== 'string' || !data.inspiration.trim() || data.inspiration.length > 500) throw new Error('未获取到有效灵感，请重试。');
      variationInput.value = data.inspiration; inspirationEdit++;
      inspirationNote('润色已完成，可继续修改，点击“按灵感生成”制作视频。');
    } catch (error) {
      if (current()) inspirationNote(error.name === 'AbortError' ? '润色超时，请重试。原内容已保留。' : error.message, true);
    } finally {
      clearTimeout(timer);
      if (current()) {
        inspirationBusy = false; inspirationController = null;
        inspirationAssist.textContent = 'AI 润色'; inspirationAssist.removeAttribute('aria-busy'); updateButtons();
      }
    }
  });
  const status = document.getElementById('production-status');
  generationForm.dataset.promptRuleVersion = 'legacy-v1';
  let promptPreviewKey = '', promptPreviewPromise = null, promptPreviewResult = '', promptPreviewTimer;
  const assetFiles = new WeakMap();
  const uploadingFiles = new WeakMap();
  // Arrays contain real local Files or stored descriptors. A stored asset never
  // masquerades as a File: IDs are sufficient for saves and submissions.
  let sourceAsset = null;
  let pageReads = new AbortController(), draftReads = new AbortController(), pageInactive = false;
  const assetFor = item => item instanceof File ? assetFiles.get(item) : item?.id ? item : null;
  const sourceItem = () => source.files[0] || sourceAsset;
  const sourceTabs = [...document.querySelectorAll('[data-source-mode]')];
  function setSourceMode(mode, focus = false) {
    if (!sourceTabs.length) return;
    document.getElementById('source-local-panel').hidden = mode !== 'local';
    document.getElementById('video-url-entry').hidden = mode !== 'link';
    sourceTabs.forEach(tab => {
      const selected = tab.dataset.sourceMode === mode;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
  }
  sourceTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => { if (!busy && !sourceImportBusy) setSourceMode(tab.dataset.sourceMode); });
    tab.addEventListener('keydown', event => {
      if (busy || sourceImportBusy || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? sourceTabs.length - 1 : (index + 1) % sourceTabs.length;
      setSourceMode(sourceTabs[next].dataset.sourceMode, true);
    });
  });
  const thumbnailFor = asset => asset.thumbnail_url || '/api/production/assets/' + encodeURIComponent(asset.id) + '/thumbnail';
  const mediaButtons = new WeakMap();
  const videoCovers = new WeakMap();
  let openReference = null;
  const referenceDialog = document.createElement('dialog');
  referenceDialog.id = 'reference-video-dialog'; referenceDialog.className = 'run-player-dialog';
  referenceDialog.setAttribute('aria-label', '查看参考视频');
  const referenceHeading = document.createElement('div'); referenceHeading.className = 'run-player-heading';
  const referenceTitle = document.createElement('h3'); referenceTitle.textContent = '查看视频';
  const referenceClose = document.createElement('button'); referenceClose.id = 'reference-video-close';
  referenceClose.type = 'button'; referenceClose.textContent = '×'; referenceClose.setAttribute('aria-label', '关闭视频');
  const referencePlayer = document.createElement('video'); referencePlayer.id = 'reference-video-player';
  referencePlayer.controls = true; referencePlayer.playsInline = true; referencePlayer.preload = 'none';
  const referenceError = document.createElement('p'); referenceError.className = 'field-hint'; referenceError.hidden = true;
  referenceError.setAttribute('role', 'status');
  referenceHeading.append(referenceTitle, referenceClose); referenceDialog.append(referenceHeading, referencePlayer, referenceError);
  document.body.append(referenceDialog);
  function closeReference() {
    referencePlayer.pause(); referencePlayer.removeAttribute('src'); referencePlayer.load();
    openReference = null;
    if (referenceDialog.open) referenceDialog.close();
  }
  referenceClose.addEventListener('click', closeReference);
  referenceDialog.addEventListener('close', () => { if (!referenceDialog.open) closeReference(); });
  referenceDialog.addEventListener('cancel', event => { event.preventDefault(); closeReference(); });
  referencePlayer.addEventListener('error', () => {
    if (openReference) { referenceError.textContent = '预览暂不可用，请关闭后重试；人物视频请同时查看下方的素材状态。'; referenceError.hidden = false; }
  });
  referencePlayer.addEventListener('canplay', () => { referenceError.hidden = true; });
  referencePlayer.addEventListener('loadedmetadata', () => {
    if (openReference) { openReference.dataset.seconds = referencePlayer.duration; updateButtons(); }
  });
  function coverVideo(video, url, poster = '', local = false) {
    if (video.dataset.mediaSource !== url) {
      releaseVideo(video); delete video.dataset.seconds; video.dataset.mediaSource = url || '';
    }
    video.hidden = true; video.controls = false; video.preload = 'none';
    let cover = videoCovers.get(video);
    if (!cover) {
      cover = document.createElement('button'); cover.type = 'button'; cover.className = 'video-cover';
      cover.dataset.videoCover = ''; cover.setAttribute('aria-label', '查看视频');
      const img = document.createElement('img'); img.alt = '视频封面'; img.decoding = 'async';
      img.addEventListener('error', () => { img.hidden = true; });
      const label = document.createElement('span'); label.textContent = '查看视频';
      cover.append(img, label);
      cover.addEventListener('click', () => {
        if (!video.dataset.mediaSource || pageInactive) return;
        closeReference(); openReference = video; referenceError.hidden = true;
        if (video.dataset.mediaSource.includes('/preview')) {
          referenceError.textContent = '正在准备兼容预览，首次打开请稍候…'; referenceError.hidden = false;
        }
        referencePlayer.src = video.dataset.mediaSource;
        referenceDialog.showModal();
        referencePlayer.play().catch(() => {});
      });
      videoCovers.set(video, cover);
    }
    if (video.parentNode && cover.parentNode !== video.parentNode) video.after(cover);
    cover.hidden = !url;
    const img = cover.querySelector('img'); img.hidden = !poster;
    if (poster) img.src = poster; else img.removeAttribute('src');
    // A newly selected local File can provide one frame without a network read.
    // Persisted videos only fetch the small authenticated thumbnail endpoint.
    if (local && url && !video.getAttribute('src')) {
      video.onloadeddata = () => {
        if (pageInactive || !video.isConnected || video.dataset.mediaSource !== url || !video.videoWidth) return;
        const canvas = document.createElement('canvas'), scale = Math.min(320 / video.videoWidth, 320 / video.videoHeight, 1);
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        img.src = canvas.toDataURL('image/jpeg', 0.75); img.hidden = false;
        video.dataset.seconds = video.duration; video.pause(); video.removeAttribute('src'); video.load(); updateButtons();
      };
      video.src = url; video.preload = 'auto';
    }
  }
  function releaseVideo(video) {
    if (openReference === video) closeReference();
    video.pause(); video.removeAttribute('src'); video.load();
    const button = mediaButtons.get(video);
    if (button) button.hidden = !video.dataset.mediaSource || video.hidden;
  }
  function lazyVideo(video, url, poster = '') {
    video.preload = 'none'; video.controls = true; video.playsInline = true;
    if (video.dataset.mediaSource !== url) {
      releaseVideo(video); video.dataset.mediaSource = url || '';
    }
    if (poster) video.poster = poster; else video.removeAttribute('poster');
    let button = mediaButtons.get(video);
    if (!button) {
      button = document.createElement('button'); button.type = 'button';
      button.textContent = '播放视频'; button.dataset.mediaPlay = ''; button.className = 'secondary';
      button.addEventListener('click', () => {
        if (!video.dataset.mediaSource || pageInactive) return;
        video.src = video.dataset.mediaSource; button.hidden = true;
        video.play().catch(() => { if (video.isConnected && !pageInactive) button.hidden = false; });
      });
      mediaButtons.set(video, button);
    }
    if (video.parentNode && button.parentNode !== video.parentNode) video.after(button);
    button.hidden = !url || Boolean(video.getAttribute('src')) || video.hidden;
  }
  const media = {lazyVideo, releaseVideo, thumbnailFor};
  function mediaDuration(item, video) {
    const seconds = assetFor(item)?.duration;
    return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0 ? seconds : Number(video?.dataset.seconds) || video?.duration;
  }
  let hairPreviewVersion = 0, hairPreviewTimer = null;
  let sessionReady = false, restoring = false;
  window.productionSession = true;
  const mediaUrls = new Map();
  const accessoryLabels = {bag:'包包',hat:'帽子',watch:'手表',shoes:'鞋子',necklace:'项链',glasses:'眼镜',earrings:'耳环',scarf:'围巾',hand_jewelry:'手饰'};
  const extraKinds = ['hairstyle','scene',...Object.keys(accessoryLabels)];
  const imageFiles = Object.fromEntries(['face','clothing',...extraKinds].map(kind=>[kind,[]]));
  const imageInputs = Object.fromEntries(Object.keys(imageFiles).map(kind=>[kind,generationForm.elements.namedItem(kind+'_image')]));
  let personMode = 'image', personVideo = null, personInputPolicy = 'auto_virtual', personInputVersion = 0;
  const personVideoPreview = document.getElementById('person-video-preview');
  let busy = false;
  let sourceImportBusy = false;
  let personVideoCheck = null, personVideoCheckKey = '', personVideoCheckVersion = 0;
  const personVideoActions = document.createElement('div'); personVideoActions.className = 'asset-footer';
  const checkPersonVideoButton = document.createElement('button'); checkPersonVideoButton.type = 'button';
  checkPersonVideoButton.textContent = '重新检查状态'; checkPersonVideoButton.id = 'person-video-check';
  const choosePersonVideo = document.createElement('button'); choosePersonVideo.type = 'button';
  choosePersonVideo.textContent = '选择可用人物素材'; choosePersonVideo.id = 'person-video-choose';
  choosePersonVideo.addEventListener('click', () => {
    const menu = document.querySelector('summary[aria-label="从人物库选择"]');
    if (menu) { menu.parentElement.open = true; menu.scrollIntoView({block:'center'}); menu.focus(); }
  });
  checkPersonVideoButton.addEventListener('click', () => { personVideoCheckKey = ''; renderPersonVideo(); });
  personVideoActions.append(checkPersonVideoButton, choosePersonVideo);
  document.getElementById('person-video-status').after(personVideoActions);
  let job = null;
  let preparedSignature = '';
  let savedMask = {blur_style:'mosaic',mask_mode:'face',mask_scale:1.4,threshold:0.2,keep_audio:true,robust_tracking:false};

  function renderPersonVideo() {
    const automatic = personInputPolicy === 'auto_virtual';
    const url = personVideo ? '/api/production/assets/' + encodeURIComponent(personVideo.id) + '/preview' : '';
    coverVideo(personVideoPreview, url, personVideo ? thumbnailFor(personVideo) : '');
    if (personMode !== 'video') releaseVideo(personVideoPreview);
    document.getElementById('person-video-upload').hidden = Boolean(personVideo);
    document.getElementById('person-video-name').textContent = '';
    document.getElementById('person-video-replace').hidden = !personVideo;
    document.getElementById('person-video-remove').hidden = !personVideo;
    const videoStatus = document.getElementById('person-video-status');
    personVideoActions.hidden = !personVideo;
    const key = personVideo ? personVideo.id + ':' + personInputPolicy + ':' + personInputVersion : '';
    if (!personVideo) { personVideoCheckVersion++; personVideoCheckKey = ''; personVideoCheck = null; videoStatus.textContent = ''; videoStatus.dataset.error = 'false'; }
    else if (key !== personVideoCheckKey) {
      personVideoCheckKey = key; personVideoCheck = null;
      const version = ++personVideoCheckVersion, asset = personVideo;
      videoStatus.textContent = '已保存 · 正在核实素材状态…';
      videoStatus.dataset.error = 'false';
      checkPersonVideoButton.disabled = true;
      fetch('/api/production/assets/' + encodeURIComponent(asset.id) + '/reference-status', {signal:pageReads.signal})
        .then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.detail || '状态读取失败'); return value; })
        .then(value => {
          if (pageInactive || version !== personVideoCheckVersion) return;
          personVideoCheck = value;
          if (value.person_type === 'LivenessFace' && automatic && value.can_use) {
            personVideoCheck = {...value, can_use:false, message:'可预览 · 已有真人素材记录，请通过“选择可用人物素材”从人物库选用，以保留授权关联。'};
          }
          if (value.duration > 0) personVideo.duration = value.duration;
          videoStatus.textContent = personVideoCheck.message;
          videoStatus.dataset.error = String(!personVideoCheck.can_use);
          choosePersonVideo.hidden = personVideoCheck.can_use;
          updateButtons();
        }).catch(error => {
          if (pageInactive || version !== personVideoCheckVersion) return;
          personVideoCheck = {can_use:false};
          videoStatus.textContent = '素材状态暂未确认：' + error.message + '。请重新检查状态。';
          videoStatus.dataset.error = 'true'; choosePersonVideo.hidden = false; updateButtons();
        }).finally(() => { if (version === personVideoCheckVersion) checkPersonVideoButton.disabled = false; });
    }
    updateButtons();
  }
  function setPersonMode(mode) {
    if (personMode !== mode) personInputVersion++;
    personMode = mode;
    document.getElementById('person-image-panel').hidden = mode !== 'image';
    document.getElementById('person-video-panel').hidden = mode !== 'video';
    if (mode !== 'video') releaseVideo(personVideoPreview);
    document.querySelectorAll('[data-person-media]').forEach(button => {
      const selected = button.dataset.personMedia === mode;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    window.portraitPeople?.restore(window.portraitPeople.selected);
    renderPersonVideo(); syncReferencePrompt(); updateButtons();
  }
  document.querySelectorAll('[data-person-media]').forEach(button => button.addEventListener('click',() => {
    if (busy || !sessionReady || button.dataset.personMedia === personMode) return;
    setPersonMode(button.dataset.personMedia); syncImages('face'); clearResult(); refreshPhotoInputs(); changed();
  }));
  const personTabs = [...document.querySelectorAll('[data-person-media]')];
  personTabs.forEach((button, index) => button.addEventListener('keydown', event => {
    if (busy || !sessionReady || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? personTabs.length - 1 : (index + 1) % personTabs.length;
    personTabs[next].click(); personTabs[next].focus();
  }));
  document.getElementById('person-video-replace').addEventListener('click',() => {
    if (!busy && sessionReady) document.getElementById('person-video-file').click();
  });
  document.getElementById('person-video-remove').addEventListener('click',() => {
    if (busy) return;
    if (personInputPolicy !== 'auto_virtual') useUploadedPerson();
    personInputVersion++; personVideo = null; renderPersonVideo(); clearResult(); changed();
  });
  function useUploadedPerson() {
    // A local file is a new reference, never an implicit addition to a library person.
    // Drop the previous library set together so official/real bindings cannot leak.
    personInputVersion++; personInputPolicy = 'auto_virtual';
    window.portraitPeople?.restore(null);
    personVideo = null; imageFiles.face = [];
    renderPersonVideo();
  }
  document.getElementById('person-video-file').addEventListener('change',async event => {
    const input = event.currentTarget, file = input.files[0]; input.value = '';
    if (!file || busy || !sessionReady) return;
    if (!/\.(mp4|mov)$/i.test(file.name) || file.size > 50 * 1024 * 1024) { toast('人物视频支持 MP4 / MOV，50 MB 以内。'); return; }
    if (personInputPolicy !== 'auto_virtual') { useUploadedPerson(); syncImages('face'); }
    const version = ++personInputVersion, targetDraft = draft?.id, targetPerson = window.portraitPeople?.selected;
    const current = () => !pageInactive && version === personInputVersion && personMode === 'video' && personInputPolicy === 'auto_virtual' && draft?.id === targetDraft && window.portraitPeople?.selected === targetPerson;
    document.getElementById('person-video-status').textContent = '正在保存人物视频…';
    document.getElementById('person-video-status').dataset.error = 'false';
    try {
      const asset = await persistFile(file,'person_video');
      if (!current()) return;
      personVideo = asset; renderPersonVideo(); clearResult(); changed();
    } catch (error) {
      if (current()) {
        document.getElementById('person-video-status').textContent = '视频保存失败：' + error.message;
        document.getElementById('person-video-status').dataset.error = 'true';
      }
    }
  });
  personVideoPreview.addEventListener('loadedmetadata',updateButtons);
  personVideoPreview.addEventListener('error',() => {
    if (personVideo) {
      document.getElementById('person-video-status').textContent = '浏览器无法预览此视频，请尝试 H.264 编码的 MP4；生成前仍会检查素材。';
      document.getElementById('person-video-status').dataset.error = 'true';
    }
  });

  function maskValues() {
    return {...savedMask};
  }
  function applyMask(values) {
    savedMask = {...values};
  }
  savedMask = maskValues();
  window.productionSource = () => {
    const item = sourceItem();
    return {id:item instanceof File ? `${item.name}:${item.size}:${item.lastModified}` : item?.id,duration:mediaDuration(item,document.querySelector('#video-reference-preview video')),url:item instanceof File ? mediaUrls.get('video')?.[0] : item?.url};
  };
  function signature() {
    const file = sourceItem();
    return JSON.stringify([file ? [assetFor(file)?.id || null, file.name, file.size, file.lastModified] : null, file ? '' : sourceUrl.value.trim(), maskValues(), window.generationOptions?.sourceClip?.(), window.generationOptions?.get()?.ratio || 'adaptive']);
  }
  function hasSource() { return Boolean(sourceItem() || sourceUrl.value.trim() || job?.defaced_url); }
  function referenceTotal() { return (personMode === 'video' ? 0 : imageFiles.face.length) + imageFiles.clothing.length + extraKinds.reduce((n,k)=>n+(document.getElementById(k+'-enabled').checked ? imageFiles[k].length : 0),0); }
  function modelLimits() { return window.generationOptions?.limits() || {max_images:9,max_video_seconds:15}; }
  function referenceOverLimit() { const max = modelLimits().max_images; return max != null && referenceTotal()>max; }
  function focusSection(id) {
    const target = document.getElementById(id);
    if (!target) return;
    for (let parent = target; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
    target.tabIndex = -1;
    target.focus({preventScroll:true});
    target.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  }
  document.getElementById('generation-dock-summary').addEventListener('click', () => focusSection('generation-settings'));
  document.getElementById('generation-fix').addEventListener('click', event => focusSection(event.currentTarget.dataset.target));
  function updateReadiness(durationOver) {
    const present = {video:hasSource(), clothing:Boolean(imageFiles.clothing.length), person:Boolean(personMode === 'video' ? personVideo && (personInputPolicy === 'auto_virtual' || window.portraitPeople?.selected) : imageFiles.face.length)};
    let message = '素材已选齐，可以生成', target = '', action = '去检查';
    if (busy) message = '正在处理，请稍候…';
    else if (!sessionReady) { message = saveStatus?.dataset.error === 'true' ? '素材恢复失败，请重试' : '正在恢复素材…'; target = 'save-notice'; }
    else if (sourceImportBusy) message = '参考视频正在导入，可继续准备其他素材';
    else if (pendingSubmission) message = '上次提交结果待确认，可安全恢复';
    else if (!present.video) { message = '还缺参考视频'; target = 'flow-stage-source'; action = '去添加'; }
    else if (!present.clothing) { message = '还缺穿搭参考'; target = 'flow-stage-references'; action = '去添加'; }
    else if (!present.person) { message = '还缺人物参考'; target = 'person-reference-card'; action = '去添加'; }
    else if (saveStatus.dataset.error === 'true') { message = '素材尚未保存，请重试'; target = 'save-notice'; }
    else if (referenceOverLimit()) { message = '参考图超过模型上限'; target = 'reference-count'; }
    else if (durationOver) { message = '视频时长超出模型限制'; target = 'generation-settings'; }
    else if (personMode === 'video' && !personVideoCheck) { message = '正在核实人物视频'; target = 'person-reference-card'; }
    else if (personMode === 'video' && !personVideoCheck.can_use) { message = '人物视频暂不可用'; target = 'person-reference-card'; }
    else if (window.generationOptions?.available() === false) { message = '请检查模型与时长设置'; target = 'generation-settings'; }
    else if (status.textContent) message = status.textContent;
    const feedback = document.getElementById('generation-readiness');
    feedback.textContent = message;
    feedback.title = message;
    const fix = document.getElementById('generation-fix');
    fix.hidden = !target || busy; fix.dataset.target = target; fix.textContent = action;
    document.getElementById('generation-view-task').hidden = !status.textContent.startsWith('任务已加入队列');
    const selectedText = name => modelForm.querySelector(`[name="${name}"]`)?.selectedOptions?.[0]?.textContent;
    const audio = modelForm.querySelector('[name="generate_audio"]');
    document.getElementById('generation-dock-summary').textContent = [selectedText('model'), selectedText('resolution'), document.getElementById('generation-duration-note').textContent, audio?.checked ? '有声' : '无声'].filter(Boolean).join(' · ') + ' ›';
  }
  // The real submit button stays in its form; the dock only changes its layout.
  const dock = document.querySelector('.generation-dock');
  if (window.ResizeObserver) new ResizeObserver(() => {
    document.documentElement.style.setProperty('--generation-dock-height', `${Math.ceil(dock.getBoundingClientRect().height)}px`);
  }).observe(dock);
  function syncKeyboard() {
    const editing = document.activeElement?.matches('input:not([type=checkbox]):not([type=range]):not([type=file]), textarea');
    document.body.classList.toggle('production-keyboard-open', Boolean(editing && window.visualViewport && innerHeight - visualViewport.height > 150));
  }
  window.visualViewport?.addEventListener('resize', syncKeyboard);
  document.addEventListener('focusin', syncKeyboard);
  document.addEventListener('focusout', () => requestAnimationFrame(syncKeyboard));
  function updateButtons() {
    window.generationOptions?.refreshSource();
    const total = referenceTotal(), over = referenceOverLimit();
    const originalSeconds = mediaDuration(sourceItem(), document.querySelector('#video-reference-preview video'));
    const sourceSeconds = window.generationOptions?.effectiveDuration?.(originalSeconds) ?? originalSeconds;
    const personSeconds = mediaDuration(personVideo, personVideoPreview);
    const seconds = sourceSeconds + personSeconds, maxSeconds = modelLimits().max_video_seconds;
    const sourceLimited = modelLimits().person_video || modelLimits().follow_source;
    const durationOver = !Number.isFinite(sourceSeconds) || (sourceLimited && (sourceSeconds < 2 || sourceSeconds > maxSeconds)) || (personMode === 'video' && (!Number.isFinite(personSeconds) || personSeconds < 2 || seconds > maxSeconds));
    const durationHint = document.getElementById('person-video-duration');
    durationHint.textContent = Number.isFinite(seconds) && personVideo ? `合计 ${seconds.toFixed(1)} / ${maxSeconds} 秒` + (durationOver ? '，请缩短视频。' : '') : `动作 + 人物视频合计 ≤ ${maxSeconds} 秒`;
    durationHint.dataset.error = String(Boolean(durationOver));
    const sourceHint = document.getElementById('source-model-hint');
    sourceHint.hidden = !hasSource() || !sourceLimited;
    sourceHint.textContent = !Number.isFinite(sourceSeconds) ? '正在读取动作视频时长，完成后可生成。' : sourceSeconds < 2 || sourceSeconds > maxSeconds ? `动作视频为 ${sourceSeconds.toFixed(2)} 秒，当前模型要求 2–${maxSeconds} 秒，请缩短视频或选择支持该时长的模型。` : `参考视频合计不超过 ${maxSeconds} 秒。`;
    face.required = personMode === 'image' && !imageFiles.face.length;
    clothing.required = !imageFiles.clothing.length;
    const counter = document.getElementById('reference-count');
    counter.textContent = `本次参考图 ${total}${modelLimits().max_images == null ? '' : ' / '+modelLimits().max_images} 张` + (over ? ' · 请关闭部分可选项后生成' : '');
    counter.classList.toggle('over-limit',over);
    const hasReferences = hasSource() || Boolean(personVideo) || Boolean(window.portraitPeople?.selected) || Object.values(imageFiles).some(files => files.length) || Boolean(document.getElementById('scene-description').value.trim());
    document.getElementById('clear-reference-images').disabled = busy || sourceImportBusy || !sessionReady || !hasReferences;
    document.getElementById('clear-reference-images').hidden = !hasReferences;
    const sourceLocked = busy || sourceImportBusy;
    importButton.disabled = sourceLocked || !sessionReady || !sourceUrl.value.trim();
    source.disabled = sourceUrl.disabled = sourceLocked;
    sourceTabs.forEach(tab => { tab.disabled = sourceLocked; });
    ['replace-source-video', 'remove-source-video'].forEach(id => { document.getElementById(id).disabled = sourceLocked; });
    document.getElementById('draft-recover').disabled = sourceLocked || !sessionReady;
    previewButton.disabled = sourceLocked || !sessionReady || !hasSource() || window.generationOptions?.available() === false;
    generateButton.disabled = sourceLocked || !sessionReady || (!pendingSubmission && (window.generationOptions?.available() === false || referenceOverLimit() || durationOver || (personMode === 'video' && (!personVideoCheck || !personVideoCheck.can_use)) || !(hasSource() && (personMode === 'video' ? personVideo && (personInputPolicy === 'auto_virtual' || window.portraitPeople?.selected) : imageFiles.face.length) && imageFiles.clothing.length)));
    generateButton.formNoValidate = Boolean(pendingSubmission);
    if (variationSubmit) {
      const guided = Boolean(variationInput.value.trim());
      variationSubmit.textContent = guided ? '按灵感生成' : '🎲 灵感随机生成';
      variationSubmit.title = guided ? '按你填写的灵感直接生成视频，不额外加入随机创意' : '随机构思拍摄灵感并直接生成视频';
      variationSubmit.disabled = generateButton.disabled || Boolean(pendingSubmission);
      variationInput.disabled = sourceLocked || Boolean(pendingSubmission);
      if (variationClear) variationClear.disabled = sourceLocked || Boolean(pendingSubmission);
      if (inspirationAssist) inspirationAssist.disabled = sourceLocked || !sessionReady || Boolean(pendingSubmission) || inspirationBusy || !variationInput.value.trim();
    }
    for (const id of ['replace-source-video', 'remove-source-video']) document.getElementById(id).hidden = !hasSource();
    if (!busy) generateButton.textContent = pendingSubmission ? '确认上次提交结果' : window.delegatedEditor ? '为该用户重新生成 →' : '生成视频 →';
    updateReadiness(durationOver);
  }
  function lock(value) {
    busy = value;
    window.portraitPeople?.lock(value);
    generationForm.setAttribute('aria-busy', String(value));
    [...sourceForm.elements, ...generationForm.querySelectorAll('input,textarea,[data-prompt-template],[data-template-action],[data-asset-edit]')].forEach(input => input.disabled = value);
    window.generationOptions?.setLocked(value);
    ['draft-recover'].forEach(id => document.getElementById(id).disabled = value || !sessionReady);
    document.querySelectorAll('[data-person-media],#person-video-replace,#person-video-remove').forEach(button => button.disabled = value);
    updateButtons();
  }
  function clearResult() {
    const result = document.getElementById('generation-result');
    result.hidden = true;
    result.classList.remove('has-error');
    result.removeAttribute('role');
    const video = document.getElementById('studio-final-video');
    video.hidden = true; lazyVideo(video, ''); releaseVideo(video);
    document.getElementById('studio-download').hidden = true;
  }
  function invalidate() {
    job = null; preparedSignature = ''; preview.hidden = true;
    lazyVideo(previewVideo, ''); releaseVideo(previewVideo);
    previewStatus.textContent = '素材或设置已变化，点击重新预览';
    status.textContent = '';
    clearResult(); updateButtons(); changed();
  }
  function showFiles(input, kind) {
    (mediaUrls.get(kind) || []).forEach(URL.revokeObjectURL);
    const files = kind === 'video' ? (sourceItem() ? [sourceItem()] : []) : imageFiles[kind];
    const urls = files.map(file => file instanceof File ? URL.createObjectURL(file) : kind === 'video' ? file.url : thumbnailFor(file));
    mediaUrls.set(kind, urls.filter((_, index) => files[index] instanceof File));
    const target = document.getElementById(`${kind}-reference-preview`);
    target.classList.toggle('single-image', files.length === 1);
    target.querySelectorAll('video').forEach(releaseVideo);
    target.replaceChildren(...urls.map((url, index) => {
      const element = document.createElement(kind === 'video' ? 'video' : 'img');
      if (kind === 'video') {
        const holder = document.createElement('span'); holder.className = 'reference-video'; holder.append(element);
        const stored = assetFor(files[index]);
        coverVideo(element, url, stored ? thumbnailFor(stored) : '', files[index] instanceof File);
        if (stored && !stored.duration) {
          fetch('/api/production/assets/' + encodeURIComponent(stored.id) + '/reference-status', {signal:pageReads.signal})
            .then(async response => { if (!response.ok) throw new Error('读取时长失败'); return response.json(); })
            .then(value => {
              if (pageInactive || !element.isConnected || !(value.duration > 0)) return;
              stored.duration = value.duration; element.dataset.seconds = value.duration; updateButtons();
            }).catch(() => {
              if (!pageInactive && element.isConnected) status.textContent = '参考视频时长暂未读到，请刷新页面重试。';
            });
        }
        element.addEventListener('loadedmetadata',updateButtons); return holder;
      }
      element.src = url;
      element.alt = '参考图片';
      element.dataset.imagePreview = files[index] instanceof File ? url : (files[index].url || url);
      element.tabIndex = 0; element.setAttribute('role', 'button');
      element.setAttribute('aria-label', '放大查看参考图片');
      element.decoding = 'async';
      const item = document.createElement('span'); item.className = 'reference-thumb';
      const tag = document.createElement('span'); tag.className = 'reference-index';
      tag.textContent = index === 0 ? '主参考' : String(index + 1);
      const actions = document.createElement('span'); actions.className = 'reference-actions';
      for (const [label, action] of [['替换', () => replaceImage(kind, index)], [kind === 'face' ? '移除' : '删除', () => {
        imageFiles[kind].splice(index, 1); syncImages(kind);
        if (kind === 'face' && !imageFiles.face.length && personInputPolicy !== 'auto_virtual') { useUploadedPerson(); syncImages('face'); }
      }]]) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.dataset.assetEdit = ''; button.setAttribute('aria-label', `${label}第 ${index + 1} 张图片`);
        button.addEventListener('click', () => { if (!busy) action(); }); actions.append(button);
      }
      element.addEventListener('error', () => {
        element.hidden = true;
        const fallback = document.createElement('span'); fallback.className = 'reference-error';
        fallback.textContent = '无法预览，可替换图片'; item.prepend(fallback);
      }, {once: true});
      item.append(element, tag, actions);
      const portrait = !window.portraitPeople?.selected ? assetFor(files[index])?.portrait : null;
      if (kind === 'face' && portrait?.status === 'Active' && portrait.remote_asset_id) {
        const badge = document.createElement('span'); badge.className = 'portrait-badge';
        badge.textContent = '官方已授权'; badge.title = '此图片来自官方授权素材；生成时将重新校验授权状态。'; item.append(badge);
      }
      return item;
    }));
    document.getElementById(`${kind}-picker`).classList.toggle('has-media', urls.length > 0);
    const name = document.getElementById(kind === 'video' ? 'source-file-name' : `${kind}-file-name`);
    name.textContent = '';
  }
  function syncImages(kind) {
    const input = imageInputs[kind];
    const transfer = new DataTransfer();
    imageFiles[kind].filter(file => file instanceof File).forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    showFiles(input, kind); clearResult(); updateButtons(); changed();
    if (kind === 'face') renderPhotoStatus();
    if (kind === 'hairstyle') scheduleHairPreview();
    refreshExtraState();
  }
  document.getElementById('clear-reference-images').addEventListener('click', async () => {
    if (busy || sourceImportBusy || !sessionReady) return;
    if (!window.confirm('清空本次所有参考图片、动作参考视频、人物参考视频和场景描述？提示词和素材库会保留。')) return;
    lock(true); personInputVersion++;
    try {
      clearTimeout(photoTimer); photoTimer = null;
      source.value = ''; sourceAsset = null; sourceUrl.value = ''; linkStatus.textContent = '';
      showFiles(source, 'video');
      useUploadedPerson();
      document.getElementById('person-video-file').value = '';
      document.getElementById('source-clip-start').value = 0;
      document.getElementById('source-clip-duration').value = '';
      for (const kind of Object.keys(imageFiles)) imageFiles[kind] = [];
      for (const kind of extraKinds) document.getElementById(kind+'-enabled').checked = false;
      document.getElementById('scene-description').value = '';
      for (const kind of Object.keys(imageFiles)) syncImages(kind);
      refreshPhotoInputs(); invalidate();
      await flushDraft();
      toast('所有参考已清空，提示词和素材库已保留');
    } catch (error) { showSave('所有参考已清空，但保存失败：'+error.message+'，请重试保存。', true); }
    finally { lock(false); }
  });
  function validImage(file) {
    if (!(file.type.startsWith('image/') || (!file.type && /\.(png|jpe?g|webp|gif|bmp|tiff?|heic|heif|avif)$/i.test(file.name)))) {
      toast('请选择图片文件'); return false;
    }
    if (file.size > 20 * 1024 * 1024) { toast('单张图片请控制在 20 MB 以内'); return false; }
    return true;
  }
  function replaceImage(kind, index) {
    const chooser = document.createElement('input'); chooser.type = 'file'; chooser.accept = 'image/*';
    chooser.addEventListener('change', () => {
      const file = chooser.files[0];
      if (!busy && file && validImage(file)) {
        if (kind === 'face' && personInputPolicy !== 'auto_virtual') { useUploadedPerson(); imageFiles.face = [file]; }
        else imageFiles[kind][index] = file;
        syncImages(kind);
      }
    }, {once: true});
    chooser.click();
  }
  source.addEventListener('change', () => {
    if (busy || sourceImportBusy || !source.files.length) return;
    sourceAsset = null;
    sourceUrl.value = ''; linkStatus.textContent = '';
    setSourceMode('local');
    showFiles(source, 'video'); invalidate();
  });
  sourceUrl.addEventListener('input', () => {
    // Drafting a replacement link must not discard the selected video or preview.
    updateButtons();
  });
  document.getElementById('replace-source-video').addEventListener('click', () => { if (!busy && !sourceImportBusy) source.click(); });
  document.getElementById('remove-source-video').addEventListener('click', () => {
    if (busy || sourceImportBusy) return;
    source.value = ''; sourceAsset = null; sourceUrl.value = ''; showFiles(source, 'video');
    linkStatus.textContent = '';
    invalidate();
  });
  sourceUrl.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!importButton.disabled) importButton.click();
    }
  });
  importButton.addEventListener('click', async () => {
    if (busy || sourceImportBusy || !sessionReady || !sourceUrl.value.trim()) return;
    const text = sourceUrl.value.trim(), importingDraftId = draft.id;
    const panel = document.getElementById('video-url-entry');
    const loading = document.getElementById('video-import-loading');
    const loadingTitle = document.getElementById('video-import-loading-title');
    let imported = false;
    sourceImportBusy = true;
    panel.setAttribute('aria-busy', 'true');
    loadingTitle.textContent = '正在解析并下载视频';
    loading.hidden = false;
    updateButtons();
    importButton.textContent = '正在加载视频…';
    linkStatus.dataset.error = 'false';
    linkStatus.textContent = '';
    try {
      const response = await fetch('/api/production/assets/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : '视频导入失败，请稍后重试。');
      }
      const asset = await response.json();
      if (pageInactive || draft.id !== importingDraftId) return;
      source.value = ''; sourceAsset = asset;
      sourceUrl.value = '';
      imported = true;
      // Reuse the upload path without another paid link resolution.
      showFiles(source, 'video'); invalidate();
      loadingTitle.textContent = '正在保存视频';
      await flushDraft();
      if (pageInactive || draft.id !== importingDraftId) return;
      setSourceMode('local');
      document.getElementById('source-file-name').textContent = '';
      linkStatus.textContent = '视频已加载，可在上方播放，也可继续打码或生成。';
      const video = document.querySelector('#video-reference-preview video');
      video.playsInline = true;
      video.addEventListener('error', () => {
        if (!video.isConnected) return;
        linkStatus.dataset.error = 'true';
        linkStatus.textContent = '视频已导入，但浏览器无法播放此格式，请更换视频或尝试打码预览。';
      }, {once: true});
    } catch (error) {
      if (pageInactive) return;
      if (imported) setSourceMode('local');
      linkStatus.dataset.error = 'true';
      linkStatus.textContent = imported ? '视频已载入，但草稿保存失败，请点击重试保存。' : error.message || '网络异常，视频导入失败，请重试。';
    } finally {
      sourceImportBusy = false;
      panel.removeAttribute('aria-busy');
      loading.hidden = true;
      importButton.textContent = '导入视频';
      updateButtons();
    }
  });
  for (const [kind, input] of Object.entries(imageInputs)) input.addEventListener('change', () => {
    if (busy) return;
    const identity = file => JSON.stringify([file.name, file.size, file.lastModified]);
    if (extraKinds.includes(kind)) {
      const file = input.files[0];
      if (file && validImage(file)) { imageFiles[kind] = [file]; document.getElementById(kind+'-enabled').checked = true; }
      syncImages(kind); return;
    }
    const accepted = [...input.files].filter(validImage);
    if (kind === 'face' && accepted.length && personInputPolicy !== 'auto_virtual') useUploadedPerson();
    const seen = new Set(imageFiles[kind].map(identity));
    for (const file of accepted) {
      if (!seen.has(identity(file))) { imageFiles[kind].push(file); seen.add(identity(file)); }
    }
    syncImages(kind);
  });
  document.querySelectorAll('[data-add-images]').forEach(button => button.addEventListener('click', () => {
    if (!busy) imageInputs[button.dataset.addImages].click();
  }));
  function stripReferenceRules(text) {
    return text.replace(/\n*【素材联动】[\s\S]*?【联动结束】/g, '').trim();
  }
  function syncReferencePrompt() {
    if (!promptEditable) return;
    const input = promptInput;
    const exclusive = ['exclusive-v2','yoyo-v3'].includes(generationForm.dataset.promptRuleVersion);
    document.getElementById('exclusive-prompt-preview').hidden = !exclusive;
    if (exclusive) {
      const base = stripReferenceRules(input.value);
      if (base !== input.value) input.value = base;
      document.getElementById('prompt-reference-status').textContent = generationForm.dataset.promptRuleVersion === 'yoyo-v3' ? '独立参考优先，其余搭配跟随穿搭图' : '独立参考优先';
      schedulePromptPreview();
      return;
    }
    window.syncLegacyReferencePrompt({input, personMode, imageFiles, accessoryLabels, stripReferenceRules});
  }
  function exclusivePreviewPayload() {
    const faces = personMode === 'video' ? 0 : imageFiles.face.length, clothes = imageFiles.clothing.length;
    const roles = [...(faces ? ['人物'] : []), ...(clothes ? ['衣服'] : []),
      ...Array(Math.max(0,faces-1)).fill('人物补充'), ...Array(Math.max(0,clothes-1)).fill('衣服补充')];
    for (const [kind,label] of Object.entries({hairstyle:'发型',scene:'场景',...accessoryLabels})) {
      if (imageFiles[kind].length && document.getElementById(kind+'-enabled').checked) roles.push(label);
    }
    const model = publicModel();
    return {prompt:stripReferenceRules(promptInput.value),roles,
      person_video:personMode === 'video',rule_version:generationForm.dataset.promptRuleVersion,
      scene_description:document.getElementById('scene-enabled').checked ? document.getElementById('scene-description').value.trim() : '',
      model:Object.fromEntries(['model','duration','resolution','ratio','generate_audio'].filter(key=>key in model).map(key=>[key,model[key]]))};
  }
  function schedulePromptPreview() {
    if (!promptEditable) return;
    clearTimeout(promptPreviewTimer);
    if (!['exclusive-v2','yoyo-v3'].includes(generationForm.dataset.promptRuleVersion)) return;
    const hint = document.getElementById('exclusive-prompt-status');
    hint.textContent = '正在更新本次完整提示词…'; hint.dataset.error = 'false';
    document.getElementById('final-generation-prompt').value = '';
    promptPreviewTimer = setTimeout(() => ensurePromptPreview().catch(() => {}), 180);
  }
  async function ensurePromptPreview() {
    if (!promptEditable) return null;
    if (!['exclusive-v2','yoyo-v3'].includes(generationForm.dataset.promptRuleVersion)) return null;
    const payload = exclusivePreviewPayload(), key = JSON.stringify(payload);
    const hint = document.getElementById('exclusive-prompt-status');
    if (key !== promptPreviewKey || !promptPreviewPromise) {
      promptPreviewKey = key;
      promptPreviewPromise = api('/prompt-preview','POST',payload).then(result => result.prompt);
    }
    try {
      const text = await promptPreviewPromise;
      if (pageInactive || !['exclusive-v2','yoyo-v3'].includes(generationForm.dataset.promptRuleVersion)) return null;
      if (key !== JSON.stringify(exclusivePreviewPayload())) return ensurePromptPreview();
      promptPreviewResult = text;
      document.getElementById('final-generation-prompt').value = text;
      hint.textContent = '已按本次素材更新；生成时使用下方完整提示词。'; hint.dataset.error = 'false';
      return text;
    } catch (error) {
      if (key === promptPreviewKey) {
        promptPreviewPromise = null; promptPreviewResult = '';
        hint.textContent = '提示词预览失败：'+error.message+'；请重试，预览成功后才能生成。'; hint.dataset.error = 'true';
      }
      throw error;
    }
  }
  document.getElementById('retry-prompt-preview')?.addEventListener('click', () => ensurePromptPreview().catch(() => {}));
  window.productionPrompt = {strip:stripReferenceRules, sync:syncReferencePrompt,
    get ruleVersion() { return generationForm.dataset.promptRuleVersion; },
    get templateId() { return generationForm.dataset.promptTemplateId || null; },
    select(item) { generationForm.dataset.promptRuleVersion = item?.rule_version || 'legacy-v1'; generationForm.dataset.promptTemplateId = item?.id || ''; }
  };
  function refreshExtraState() {
    for (const kind of extraKinds) {
      const enabled = document.getElementById(kind+'-enabled');
      const present = imageFiles[kind].length > 0 || (kind==='scene' && document.getElementById('scene-description').value.trim());
      document.getElementById(kind+'-reference-state').textContent = !present ? '可选' : enabled.checked ? '已设置' : '未启用';
      if (kind === 'scene') {
        const description = document.getElementById('scene-description').value.trim();
        document.getElementById('scene-custom-fields').hidden = false;
        document.getElementById('scene-reference-state').textContent = !enabled.checked ? '保留原视频场景' : !present ? '更换场景 · 待添加参考' : `更换场景 · ${imageFiles.scene.length && description ? '图片和描述' : description ? '文字描述' : '已添加图片'}`;
      }
      if (accessoryLabels[kind]) {
        document.getElementById(kind+'-references').hidden = !imageFiles[kind].length;
        document.getElementById(kind+'-add').classList.toggle('has-reference',Boolean(imageFiles[kind].length));
      }
      document.getElementById(kind+'-file-name').textContent = '';
    }
    document.getElementById('accessory-count').textContent = `${Object.keys(accessoryLabels).filter(k=>imageFiles[k].length && document.getElementById(k+'-enabled').checked).length} 项已启用`;
    syncReferencePrompt(); updateButtons();
  }
  for (const kind of extraKinds) document.getElementById(kind+'-enabled').addEventListener('change', () => { refreshExtraState(); changed(); if (kind === 'hairstyle') scheduleHairPreview(); });
  document.getElementById('scene-description').addEventListener('input', () => { if (document.getElementById('scene-description').value.trim()) document.getElementById('scene-enabled').checked = true; refreshExtraState(); changed(); });


  function hairMaskValues() {
    return {mask_scale:Number(document.getElementById('hairstyle-mask-scale').value), threshold:Number(document.getElementById('hairstyle-mask-threshold').value)};
  }
  function scheduleHairPreview() {
    clearTimeout(hairPreviewTimer);
    const version = ++hairPreviewVersion, file = imageFiles.hairstyle[0];
    const panel = document.getElementById('hairstyle-mask-panel'), image = document.getElementById('hairstyle-mask-preview');
    const message = document.getElementById('hairstyle-mask-status'), retry = document.getElementById('hairstyle-mask-retry');
    image.hidden = true; image.removeAttribute('src'); retry.hidden = true;
    panel.hidden = !file || !document.getElementById('hairstyle-enabled').checked;
    if (panel.hidden || pageInactive) return;
    const params = hairMaskValues();
    document.getElementById('hairstyle-mask-summary').textContent = params.mask_scale.toFixed(2).replace(/0$/,'')+' 倍';
    if (!['hairstyle-mask-scale','hairstyle-mask-threshold'].every(id => document.getElementById(id).checkValidity())) {
      message.textContent = '请填写有效的打码参数。'; return;
    }
    message.textContent = '正在生成打码预览…'; message.dataset.error = 'false';
    hairPreviewTimer = setTimeout(async () => {
      try {
        const asset = await persistFile(file,'hairstyle');
        if (version !== hairPreviewVersion) return;
        const result = await api('/hairstyle/preview','POST',{asset_id:asset.id,settings:params});
        if (version !== hairPreviewVersion) return;
        image.src = result.url; image.hidden = false;
        message.textContent = result.faces_detected ? `已遮挡 ${result.faces_detected} 张人脸，请确认发型保留完整。` : '未检测到人脸，请检查预览；如有漏检可降低检测阈值后重试。';
      } catch (error) {
        if (version !== hairPreviewVersion) return;
        message.textContent = '预览未完成：'+error.message; message.dataset.error = 'true'; retry.hidden = false;
      }
    },400);
  }
  for (const id of ['hairstyle-mask-scale','hairstyle-mask-threshold']) document.getElementById(id).addEventListener('input',() => { changed(); scheduleHairPreview(); });
  document.getElementById('hairstyle-mask-retry').addEventListener('click',() => { if (!busy) scheduleHairPreview(); });

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, !options.method || options.method === 'GET' ? {...options, signal:pageReads.signal} : options);
    let payload;
    try { payload = await response.json(); } catch (_) { throw new Error('服务响应异常，请稍后重试。'); }
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '提交失败，请检查输入后重试。');
    return payload;
  }
  function render(current, isGeneration) {
    const message = current.error || current.message || '正在处理…';
    if (isGeneration) {
      document.getElementById('generation-result').hidden = false;
      document.getElementById('generation-message').textContent = message;
      document.getElementById('generation-percent').textContent = `${current.progress || 0}%`;
      document.getElementById('generation-bar').style.width = `${current.progress || 0}%`;
    } else previewStatus.textContent = message;
    if (current.defaced_url) {
      preview.hidden = false;
      lazyVideo(previewVideo, current.defaced_url);
      if (!document.getElementById('flow-stage-redaction').open) releaseVideo(previewVideo);
    }
    if (current.status === 'succeeded' && current.download_url) {
      document.getElementById('generation-result').hidden = false;
      const text = current.provider === 'mock' ? '本地演示完成（打码视频）' : '生成完成';
      document.getElementById('generation-message').textContent = text;
      const video = document.getElementById('studio-final-video'); video.hidden = false; lazyVideo(video, current.download_url);
      const link = document.getElementById('studio-download'); link.href = current.download_url; link.hidden = false;
      status.textContent = text;
    }
  }
  async function waitForJob(current, isGeneration) {
    const deadline = Date.now() + 35 * 60 * 1000;
    while (true) {
      job = current; render(current, isGeneration);
      if (current.status === 'failed') throw new Error(current.error || '处理失败，请重试。');
      if (!['queued', 'running'].includes(current.status)) return current;
      if (Date.now() > deadline) throw new Error('等待超时，请检查网络；已提交的模型任务可在服务商控制台查看。');
      await new Promise(resolve => setTimeout(resolve, 1200));
      current = await jsonRequest(`/api/previews/${encodeURIComponent(current.id)}`);
    }
  }
  async function prepareVideo(body, fingerprint, isGeneration) {
    if (job?.defaced_url && preparedSignature === fingerprint && (job.status === 'defaced' || (!isGeneration && job.status === 'succeeded'))) {
      render(job, false); previewStatus.textContent = '预览已就绪'; return job;
    }
    if (!sourceItem()) throw new Error(sourceUrl.value.trim() ? '请先确认并加载视频链接。' : '请重新选择参考视频。');
    previewStatus.textContent = '正在处理预览…';
    if (isGeneration) { generateButton.textContent = '正在准备视频…'; status.textContent = '正在准备视频'; }
    const saved = await flushDraft();
    const created = await jsonRequest('/api/previews', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({source_asset_id:saved.source_asset_id,mask:saved.mask,source_clip:saved.source_clip,
        ratio:window.generationOptions?.limits().follow_source ? saved.model.ratio || 'adaptive' : 'adaptive'})});
    const ready = await waitForJob(created, isGeneration);
    if (ready.status !== 'defaced' || !ready.defaced_url) throw new Error('视频预处理未完成，请重试。');
    preparedSignature = fingerprint; previewStatus.textContent = '预览已就绪';
    return ready;
  }
  function report(error, previewOnly) {
    if (previewOnly) previewStatus.textContent = error.message;
    else {
      const result = document.getElementById('generation-result');
      result.hidden = false;
      result.classList.add('has-error');
      result.setAttribute('role', 'alert');
      status.textContent = error.message;
      document.getElementById('generation-message').textContent = error.message;
      document.getElementById('generation-percent').textContent = '—';
      document.getElementById('generation-bar').style.width = '0%';
      result.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
  }
  previewButton.addEventListener('click', async () => {
    if (busy || sourceImportBusy || !sourceForm.reportValidity()) return;
    const body = new FormData(sourceForm), fingerprint = signature();
    for (const [name, value] of Object.entries(maskValues())) {
      body.set(name === 'style' ? 'blur_style' : name === 'shape' ? 'blur_shape' : name, value == null ? '' : String(value));
    }
    lock(true); previewButton.textContent = '正在生成预览…';
    try { await prepareVideo(body, fingerprint, false); }
    catch (error) { report(error, true); }
    finally { lock(false); previewButton.textContent = '预览打码视频'; }
  });
  generationForm.addEventListener('submit', async event => {
    event.preventDefault();
    const inspiration = variationInput?.value.trim() || '';
    const variationRequested = event.submitter === variationSubmit && Boolean(variationSubmit);
    if (busy || sourceImportBusy || !sessionReady || (!pendingSubmission && !sourceForm.reportValidity())) return;
    updateButtons();
    if (!pendingSubmission && generateButton.disabled) return;
    invalidateInspiration();
    clearResult(); lock(true); generateButton.textContent = '正在提交…';
    status.textContent = '正在保存草稿并加入任务队列';
    try {
      if (!pendingSubmission) {
        acceptTaskName();
        const saved = await flushDraft();
        if (!variationRequested) await ensurePromptPreview();
        pendingSubmission = {draft_id: saved.id, revision: saved.revision, idempotency_key: crypto.randomUUID()};
        if (variationRequested) pendingSubmission.variation = {inspiration, creation_mode: inspiration ? 'guided' : 'random'};
        // Persist before the request: a lost response must retry the identical submission.
        localStorage.setItem(pendingKey, JSON.stringify(pendingSubmission));
      }
      const submitted = await api('/runs', 'POST', pendingSubmission);
      pendingSubmission = null; localStorage.removeItem(pendingKey);
      runs.set(submitted.id, submitted); renderRuns();
      resetVariation();
      status.textContent = '任务已加入队列，可继续准备下一个视频';
    } catch (error) {
      // A definitive validation error cannot have accepted the task. Network/5xx remains uncertain.
      if (error.httpStatus && error.httpStatus < 500) {
        pendingSubmission = null; localStorage.removeItem(pendingKey);
      }
      report(error, false);
      if (pendingSubmission) status.textContent = '提交结果尚未确认。点击“确认上次提交结果”安全查询；不会重复创建任务。';
    } finally { lock(false); }
  });
  let currentKey = 'production-current-draft-v1', pendingKey = 'production-pending-submit-v1';
  const saveStatus = document.getElementById('draft-save-status');
  const retrySave = document.getElementById('draft-save-retry');
  const recoverDraft = document.getElementById('draft-recover');
  const modelForm = document.getElementById('model-settings-form');
  const publicModelFields = ['model','duration','resolution','generate_audio'];
  const drafts = new Map(), runs = new Map();
  let draft = null, draftName = null, dirtyVersion = 0, savedVersion = 0, saveTimer, savePromise = null, pendingSubmission = null;
  const templatesReady = new Promise(resolve => window.addEventListener('production-templates-ready', resolve, {once:true}));
  const modelsReady = new Promise(resolve => window.addEventListener('model-settings-loaded', resolve, {once:true}));
  const defaultMask = maskValues(), defaultPrompt = promptInput.value;

  async function api(path, method = 'GET', body, options = {}) {
    const response = await fetch((options.adminRecords ? '/api/admin/task-records' : '/api/production') + path, {
      method, cache: 'no-store', headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: method === 'GET' ? AbortSignal.any([pageReads.signal, ...(path.startsWith('/drafts/') ? [draftReads.signal] : []), ...(options.signal ? [options.signal] : [])]) : options.signal,
    });
    const data = await response.json().catch(error => { if (error.name === 'AbortError') throw error; return {}; });
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || '请求失败，请稍后重试。');
      error.httpStatus = response.status; throw error;
    }
    return data;
  }
  function showSave(text, error = false) {
    saveStatus.textContent = text; saveStatus.dataset.error = String(error);
    document.getElementById('save-notice').hidden = !error;
    retrySave.hidden = !error;
    updateButtons();
  }
  function changed() {
    if (!sessionReady || restoring || pageInactive) return;
    schedulePromptPreview();
    // Submission feedback belongs to the previous revision. Keep uncertain
    // submissions intact so their idempotency key can still be recovered.
    if (!busy && !pendingSubmission) status.textContent = '';
    queueMicrotask(() => { window.productionDraftModel = publicModel(); });
    dirtyVersion++; showSave('有修改，正在保存…');
    clearTimeout(saveTimer);
    // Start media persistence immediately; draft update follows when upload completes.
    const uploadingPerson = window.portraitPeople?.selected, uploadingDraft = draft?.id, uploadingPolicy = personInputPolicy, uploadingVersion = personInputVersion;
    [['video',sourceItem()], ...Object.entries(imageFiles).flatMap(([kind,files])=>files.map(file=>[kind,file]))].filter(([,file])=>file).forEach(([kind,file]) => {
      persistFile(file, kind).then(asset => { if (kind === 'face' && personMode === 'image' && uploadingPolicy === personInputPolicy && uploadingVersion === personInputVersion && imageFiles.face.includes(file) && draft?.id === uploadingDraft && window.portraitPeople?.selected === uploadingPerson) queuePhoto(file, asset, uploadingPerson); }).catch(error => { if (draft?.id === uploadingDraft && personInputPolicy === uploadingPolicy && personInputVersion === uploadingVersion) showSave('素材保存失败：' + error.message, true); });
    });
    saveTimer = setTimeout(() => flushDraft().catch(() => {}), 500);
  }
  async function persistFile(file, kind) {
    if (assetFor(file)) return assetFor(file);
    if (uploadingFiles.has(file)) return uploadingFiles.get(file);
    const promise = (async () => {
      const body = new FormData(); body.append('file', file); body.append('kind', kind);
      const response = await fetch('/api/production/assets', {method:'POST', body});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请重试保存素材');
      assetFiles.set(file, data);
      if (kind === 'video') {
        // Browser metadata can be unavailable for otherwise valid uploaded codecs.
        const metadata = await fetch('/api/production/assets/' + encodeURIComponent(data.id) + '/reference-status', {signal:pageReads.signal});
        if (metadata.ok) {
          const info = await metadata.json();
          if (info.duration > 0) data.duration = info.duration;
        }
        if (!pageInactive && sourceItem() === file) updateButtons();
      }
      return data;
    })();
    uploadingFiles.set(file, promise);
    try { return await promise; } finally { uploadingFiles.delete(file); }
  }
  async function selectPortraitPerson(asset, signal) {
    if (asset.person_id) {
      // Check the lightweight library association before selecting a person.
      // Shared references may have a tenant-local copy ID, so the official ID
      // and media kind identify the source across the copy boundary.
      const readSignal = AbortSignal.any([pageReads.signal,draftReads.signal,...(signal ? [signal] : [])]);
      const data = await window.portraitPeople.request('people/'+encodeURIComponent(asset.person_id)+'/photos', 'GET', undefined, {signal:readSignal});
      if (!(data.items || []).some(photo => photo.status === 'active' && photo.remote_asset_id === asset.portrait.remote_asset_id && (photo.kind || 'face') === asset.kind)) {
        throw new Error('所选素材与人物不匹配，请刷新人物库后重试。');
      }
      if (readSignal.aborted) throw new DOMException('Dialog closed', 'AbortError');
      await window.portraitPeople.selectPerson(asset.person_id, {signal:readSignal});
      return;
    }
    // Only legacy admin cloud imports may lack a tenant-local person ID.
    if (window.currentAccount && !['admin','super_admin'].includes(window.currentAccount.role) && !window.currentAccount.is_admin) {
      throw new Error('请从当前账号的人物库选择素材。');
    }
    await window.portraitPeople.selectGroup(asset.portrait.group_id, asset.person_type || 'LivenessFace');
  }
  window.productionPortraits = {
    get inputPolicy() { return personInputPolicy; },
    get referenceMode() { return personMode; },
    get currentPhoto() { return personMode === 'video' ? personVideo : assetFor(imageFiles.face[0]) || null; },
    async importAsset(asset, {signal} = {}) {
      if (signal?.aborted || pageInactive) throw new DOMException('Dialog closed', 'AbortError');
      if (!sessionReady || restoring) throw new Error('草稿尚未恢复，请稍候再导入。');
      if (busy) throw new Error('编辑器正在处理，请完成当前操作后重试。');
      if (!['face','person_video'].includes(asset?.kind) || asset.portrait?.status !== 'Active' || !asset.portrait.remote_asset_id) {
        throw new Error('官方人物素材状态无效，请刷新列表后重试。');
      }
      lock(true);
      try {
        if (asset.kind === 'person_video') {
          const oldPerson = window.portraitPeople?.selected;
          await selectPortraitPerson(asset, signal);
          if (signal?.aborted || pageInactive) throw new DOMException('Dialog closed', 'AbortError');
          personInputVersion++; personInputPolicy = 'existing_person';
          if (oldPerson !== window.portraitPeople.selected) { imageFiles.face = []; syncImages('face'); }
          personVideo = asset; setPersonMode('video'); changed();
          try { await flushDraft(); } catch (_) { throw new Error('人物视频已加载，但草稿保存失败，请重试保存。'); }
          status.textContent = '人物视频已设为本次身份参考'; return;
        }
        const file = asset;
        if (signal?.aborted) throw new DOMException('Dialog closed', 'AbortError');
        const oldPerson = window.portraitPeople?.selected;
        await selectPortraitPerson(asset, signal);
        if (signal?.aborted || pageInactive) throw new DOMException('Dialog closed', 'AbortError');
        personInputVersion++; personInputPolicy = 'existing_person';
        const remaining = oldPerson === window.portraitPeople?.selected ? imageFiles.face.slice(1).filter(item => assetFor(item)?.sha256 !== asset.sha256) : [];
        if (oldPerson !== window.portraitPeople?.selected) personVideo = null;
        personMode = 'image';
        imageFiles.face = [file, ...remaining];
        setPersonMode('image');
        syncImages('face');
        try { await flushDraft(); }
        catch (_) { throw new Error('官方人物已加载，但草稿保存失败。请关闭弹窗并使用“重试保存”，暂勿刷新页面。'); }
        status.textContent = asset.person_type === 'AIGC' ? '官方虚拟人物已设为主参考图' : '官方已授权人物已设为主参考图';
      } finally { lock(false); }
    },
  };
  // One state query for visible photos; network results are keyed by file AND person.
  const photoRecords = new Map();
  let photoTimer = null, photoPolling = false;
  function photoKey(file, person) { return person + ':' + assetFor(file)?.id; }
  function currentPhotos() {
    const person = window.portraitPeople?.selected;
    return person && personInputPolicy !== 'auto_virtual' && personMode === 'image' ? imageFiles.face.map(file => photoRecords.get(photoKey(file, person))).filter(Boolean) : [];
  }
  function renderPhotoStatus() {
    const label = document.getElementById('person-photo-status');
    if (!label) return;
    const records = currentPhotos();
    const failed = records.find(x => ['failed','uncertain','stopped'].includes(x.status));
    label.dataset.error = String(Boolean(failed));
    label.textContent = personInputPolicy === 'auto_virtual' ? (imageFiles.face.length ? (imageFiles.face.every(file => assetFor(file)) ? '已保存，生成时自动准备人物。' : '正在保存人物参考图…') : '上传人物参考图，生成时自动准备。') : !window.portraitPeople?.selected ? '参考图已恢复，可继续生成。' :
      !imageFiles.face.length ? '上传新参考图，或从人物库选择。' : failed ? failed.message :
      records.length === imageFiles.face.length && records.every(x => x.status === 'active') ? '✓ 照片可用' : '正在校验人物照片，可继续准备素材或提交视频。';
    document.getElementById('person-photo-retry').hidden = !failed;
  }
  async function queuePhoto(file, asset, person = window.portraitPeople?.selected) {
    if (personInputPolicy === 'auto_virtual' || !person || !window.portraitPeople) { renderPhotoStatus(); return; }
    const key = person + ':' + asset.id;
    if (photoRecords.has(key)) { renderPhotoStatus(); schedulePhotoPoll(); return; }
    const pending = {status:'queued',message:'等待校验'};
    photoRecords.set(key,pending); renderPhotoStatus();
    try {
      const job = await window.portraitPeople.request('photos','POST',{person_id:person,asset_id:asset.id});
      photoRecords.set(key,job);
    } catch (error) { photoRecords.set(key,{status:'failed',message:error.message}); }
    renderPhotoStatus(); schedulePhotoPoll();
  }
  function refreshPhotoInputs() {
    const person = window.portraitPeople?.selected;
    if (personMode === 'image') imageFiles.face.forEach(file => {
      const asset = assetFor(file);
      if (asset) queuePhoto(file,asset,person);
    });
    renderPhotoStatus();
  }
  function schedulePhotoPoll() {
    if (pageInactive || photoTimer || photoPolling || !currentPhotos().some(x => x.id && !['active','failed','stopped','removed','restore_held'].includes(x.status))) return;
    photoTimer = setTimeout(pollPhotos, document.hidden ? 15000 : 5000);
  }
  async function pollPhotos() {
    photoTimer = null;
    const jobs = currentPhotos().filter(x => x.id && !['active','failed','stopped','removed','restore_held'].includes(x.status));
    if (!jobs.length || photoPolling) return;
    const version = personInputVersion, targetDraft = draft?.id, targetPerson = window.portraitPeople?.selected, policy = personInputPolicy;
    const current = () => personInputVersion === version && draft?.id === targetDraft && window.portraitPeople?.selected === targetPerson && personInputPolicy === policy;
    photoPolling = true;
    try {
      const data = await window.portraitPeople.request('photos?ids='+encodeURIComponent([...new Set(jobs.map(x=>x.id))].join(',')), 'GET', undefined, {signal:AbortSignal.any([pageReads.signal,draftReads.signal])});
      if (!current() || pageInactive) return;
      const updated = new Map((data.items || []).map(x=>[x.id,x]));
      for (const [key,job] of photoRecords) if (updated.has(job.id)) photoRecords.set(key,updated.get(job.id));
      renderPhotoStatus();
    } catch (error) {
      if (!current() || pageInactive) return;
      const label=document.getElementById('person-photo-status');
      label.textContent='照片状态查询暂时失败，稍后自动恢复；可以继续编辑。';
    } finally { photoPolling=false; schedulePhotoPoll(); }
  }
  document.getElementById('person-photo-retry')?.addEventListener('click',async () => {
    const person=window.portraitPeople?.selected;
    for (const file of [...imageFiles.face]) {
      const key=photoKey(file,person), job=photoRecords.get(key);
      if (!job || !['failed','uncertain','stopped'].includes(job.status)) continue;
      try {
        if (job.id) photoRecords.set(key,await window.portraitPeople.request('photos/'+job.id+'/retry','POST',{}));
        else { photoRecords.delete(key); await queuePhoto(file,assetFor(file),person); }
      } catch (error) { toast(error.message); }
    }
    renderPhotoStatus(); schedulePhotoPoll();
  });
  window.addEventListener('portrait-person-changed',() => {
    if (!sessionReady || restoring) return;
    personInputVersion++; personInputPolicy = window.portraitPeople?.selected ? 'existing_person' : 'legacy_raw';
    if (!window.portraitPeople?.selected) personVideo = null;
    window.portraitPeople?.restore(window.portraitPeople.selected);
    renderPersonVideo(); showFiles(face,'face'); refreshPhotoInputs(); changed();
  });
  window.addEventListener('visibilitychange',() => {
    clearTimeout(photoTimer); photoTimer=null; schedulePhotoPoll();
  });

  function publicModel() {
    if (window.generationOptions) return window.generationOptions.get();
    return Object.fromEntries(publicModelFields.map(name => [name, name === 'generate_audio' ? modelForm.querySelector(`[name="${name}"]`).checked : name === 'duration' ? Number(modelForm.querySelector(`[name="${name}"]`).value) : modelForm.querySelector(`[name="${name}"]`).value.trim()]));
  }
  async function captureDraft() {
    // Read all fields synchronously before awaiting uploads: saves represent one editor version.
    const videoFile = sourceItem(), faceFiles = [...imageFiles.face], clothingFiles = [...imageFiles.clothing];
    const extraFiles = Object.fromEntries(extraKinds.map(kind=>[kind,[...imageFiles[kind]]]));
    const selectedPerson = window.portraitPeople?.selected || null;
    const values = {source_clip:window.generationOptions?.sourceClip?.() || null,target_duration:window.generationOptions?.targetDuration?.() ?? null,person_input_policy:personInputPolicy,person_reference_mode:personMode,person_video_asset_id:personVideo?.id || null,person_id:selectedPerson, name:draftName || draft?.name || '未命名视频', prompt:promptInput.value, mask:maskValues(), model:publicModel()};
    values.prompt_template_id = window.productionPrompt.templateId;
    values.prompt_rule_version = window.productionPrompt.ruleVersion;
    if (!promptEditable) {
      delete values.prompt; delete values.prompt_template_id; delete values.prompt_rule_version;
    }
    for (const kind of extraKinds) values[kind+'_enabled'] = document.getElementById(kind+'-enabled').checked;
    values.scene_description = document.getElementById('scene-description').value;
    values.hairstyle_mask = hairMaskValues();
    if (/^视频-\d{4}-\d{6}$/.test(values.name) && selectedPerson) {
      const person=window.portraitPeople?.items.find(x=>x.id===selectedPerson);
      if(person)values.name=person.name.slice(0,80)+values.name.slice(2);
    }
    const [videoAsset, faces, clothes, extras] = await Promise.all([
      videoFile ? persistFile(videoFile,'video') : null,
      Promise.all(faceFiles.map(file => persistFile(file,'face'))),
      Promise.all(clothingFiles.map(file => persistFile(file,'clothing'))),
      Promise.all(extraKinds.map(async kind=>[kind+'_asset_ids',await Promise.all(extraFiles[kind].map(async file=>(await persistFile(file,kind)).id))])),
    ]);
    return {...values, ...Object.fromEntries(extras), source_asset_id:videoAsset?.id || null, face_asset_ids:faces.map(a=>a.id), clothing_asset_ids:clothes.map(a=>a.id)};
  }
  async function flushDraft() {
    clearTimeout(saveTimer);
    if (!draft) throw new Error('草稿尚未恢复，请稍候');
    if (savePromise) { await savePromise; return flushDraft(); }
    if (savedVersion === dirtyVersion) return draft;
    savePromise = (async () => {
      while (savedVersion !== dirtyVersion) {
        const version = dirtyVersion, id = draft.id, revision = draft.revision, capturedName = draftName;
        showSave('正在保存…');
        const values = await captureDraft();
        const saved = await api('/drafts/' + encodeURIComponent(id), 'PUT', {...values, revision});
        draft = saved; if(draftName===capturedName)draftName=saved.name; drafts.set(saved.id, saved); savedVersion = version; syncTaskName();
        window.productionDraftModel = {...saved.model};
      }
      recoverDraft.hidden = true; showSave('已保存到本机'); return draft;
    })();
    try { return await savePromise; }
    catch (error) {
      recoverDraft.hidden = error.httpStatus !== 409;
      showSave(error.httpStatus === 409 ? '内容已在另一页面修改；点击“保留当前修改”可单独保存此页内容。' : '保存失败：' + error.message, true);
      throw error;
    } finally { savePromise = null; }
  }
  async function restoreDraft(item) {
    draftReads.abort(); draftReads = new AbortController();
    restoring = true; personInputVersion++;
    try {
      const known = new Map((item.assets || []).map(asset => [asset.id,asset]));
      const restore = id => {
        const asset = known.get(id);
        if (!asset) throw new Error('草稿素材缺失，请重试加载。');
        return asset;
      };
      const videoFile = item.source_asset_id ? restore(item.source_asset_id) : null;
      const faces = (item.face_asset_ids || []).map(restore), clothes = (item.clothing_asset_ids || []).map(restore);
      const extras = extraKinds.map(kind => [kind,(item[kind+'_asset_ids'] || []).map(restore)]);
      source.value = ''; sourceAsset = videoFile; sourceUrl.value = '';
      setSourceMode('local'); linkStatus.textContent = '';
      const activePersonIds = item.person_reference_mode === 'video' ? [item.person_video_asset_id] : (item.face_asset_ids || []);
      const hasOfficialBinding = activePersonIds.some(id => known.get(id)?.portrait?.remote_asset_id);
      personInputPolicy = item.person_input_policy || (item.person_id || hasOfficialBinding ? 'existing_person' : 'legacy_raw');
      personMode = item.person_reference_mode || 'image'; personVideo = known.get(item.person_video_asset_id) || null;
      imageFiles.face = faces; imageFiles.clothing = clothes;
      Object.assign(imageFiles,Object.fromEntries(extras));
      for (const kind of extraKinds) document.getElementById(kind+'-enabled').checked = item[kind+'_enabled'] === true;
      document.getElementById('scene-description').value = item.scene_description || '';
      document.getElementById('hairstyle-mask-scale').value = item.hairstyle_mask?.mask_scale ?? 1;
      document.getElementById('hairstyle-mask-threshold').value = item.hairstyle_mask?.threshold ?? 0.2;
      if (draft?.id !== item.id) resetVariation();
      draft = item; draftName=item.name; drafts.set(item.id,item); syncTaskName();
      window.portraitPeople?.restore(item.person_id);
      promptInput.value = item.prompt ?? defaultPrompt;
      window.productionPrompt.select({id:item.prompt_template_id,rule_version:item.prompt_rule_version});
      window.dispatchEvent(new CustomEvent('production-prompt-restored',{detail:item}));
      applyMask({...defaultMask,...item.mask}); savedMask = maskValues();
      window.generationOptions.restore(item.model, item.source_clip, item.target_duration);
      window.productionDraftModel = {...item.model};
      setPersonMode(personMode); showFiles(source,'video'); Object.keys(imageFiles).forEach(syncImages); invalidate();
      dirtyVersion = savedVersion = 0;
      localStorage.setItem(currentKey,item.id);
      ['production-prompt-draft-v1','studio-redaction-settings','active-v1-job'].forEach(key=>localStorage.removeItem(key));
      showSave('已保存到本机');
    } finally { restoring = false; refreshPhotoInputs(); }
    if (sessionReady && promptEditable && item.prompt !== promptInput.value) changed();
  }
  function draftControls(disabled) {
    ['draft-recover'].forEach(id => document.getElementById(id).disabled = disabled || sourceImportBusy);
  }
  async function changeDraft(action) {
    if (sourceImportBusy) { toast('参考视频正在导入，请完成后再切换草稿。'); return; }
    if (busy || !sessionReady) return;
    lock(true); draftControls(true);
    try {
      await flushDraft();
      const item = await action();
      if (pageInactive) return;
      if (item.copied_from && window.delegatedEditor) {
        const next = new URL(window.location.href);
        next.searchParams.delete('delegate_user'); next.searchParams.set('draft', item.id); next.hash = '';
        window.location.assign(next.toString()); return;
      }
      if (item.delegated_user) {
        const next = new URL(window.location.href);
        next.searchParams.set('delegate_user', item.delegated_user.id);
        next.searchParams.set('draft', item.id);
        window.location.assign(next.toString()); return;
      }
      if (item.copied_from) await window.portraitPeople?.refresh();
      await restoreDraft(item);
      document.dispatchEvent(new Event('production-draft-selected'));
    } catch (error) { showSave(error.message, true); }
    finally { draftControls(false); lock(false); }
  }
  const taskNameText=document.getElementById('draft-task-name'),taskNameInput=document.getElementById('draft-name-input'),taskNameEditor=document.getElementById('draft-name-editor');
  function syncTaskName(){taskNameText.textContent=draftName||draft?.name||'视频任务';if(taskNameEditor.hidden)taskNameInput.value=draftName||draft?.name||'';}
  document.getElementById('draft-name-edit').addEventListener('click',()=>{if(!sessionReady||busy)return;taskNameEditor.hidden=false;taskNameInput.value=draftName||draft.name;taskNameInput.focus();taskNameInput.select();});
  function acceptTaskName(){if(taskNameEditor.hidden)return;const name=taskNameInput.value.trim();if(!name||name.length>120)throw new Error('任务名称请输入 1–120 个字符。');draftName=name;taskNameEditor.hidden=true;syncTaskName();changed();}
  document.getElementById('draft-name-save').addEventListener('click',async()=>{try{acceptTaskName();await flushDraft();}catch(error){toast(error.message);}});
  document.getElementById('draft-name-cancel').addEventListener('click',()=>{taskNameEditor.hidden=true;syncTaskName();});
  taskNameInput.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();document.getElementById('draft-name-save').click();}if(event.key==='Escape'){event.preventDefault();document.getElementById('draft-name-cancel').click();}});
  retrySave.addEventListener('click', () => sessionReady ? flushDraft().catch(() => {}) : initialize());
  recoverDraft.addEventListener('click', async () => {
    if (busy || sourceImportBusy) return;
    lock(true); draftControls(true);
    try {
      const values = await captureDraft();
      const item = await api('/drafts','POST',{name:values.name + '（恢复）',person_input_policy:values.person_input_policy});
      const saved = await api('/drafts/'+encodeURIComponent(item.id),'PUT',{...values,name:item.name,revision:item.revision});
      await restoreDraft(saved); recoverDraft.hidden = true;
    } catch (error) { if (!pageInactive) showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  });
  promptInput.addEventListener('input',changed);
  promptInput.addEventListener('blur',() => {
    const input = promptInput, before = input.value;
    syncReferencePrompt();
    if (input.value !== before) changed();
  });
  window.addEventListener('production-prompt-changed',event => { if (event.detail?.applyTemplate) syncReferencePrompt(); changed(); });
  window.addEventListener('model-settings-saved',() => { invalidate(); schedulePromptPreview(); });
  window.addEventListener('beforeunload', event => {
    if (sourceImportBusy || savedVersion !== dirtyVersion) { event.preventDefault(); event.returnValue = ''; }
  });

  async function retryWithoutAudio(ident) {
    if (pendingSubmission) { status.textContent='请先确认上次提交结果，再重试其他任务。'; return; }
    await changeDraft(async () => {
      const result=await api('/runs/'+encodeURIComponent(ident)+'/retry-without-audio','POST',{});
      status.textContent='已打开无声重试任务，可在任务列表查看进度。';
      renderRuns();
      return result.draft;
    });
  }
  const runList=window.createProductionRuns({api,changeDraft,retryWithoutAudio,accessoryLabels,media});
  function renderRuns(){runList.submitted();}
  function refreshRuns(){return runList.refresh();}
  async function initialize() {
    lock(true); draftControls(true);
    try {
      await window.accountReady;
      if (window.currentAccount) {
        currentKey += ':'+window.currentAccount.id; pendingKey += ':'+window.currentAccount.id;
      }
      if (window.delegatedEditor?.userId) {
        currentKey += ':delegate:'+window.delegatedEditor.userId;
        pendingKey += ':delegate:'+window.delegatedEditor.userId;
      }
      try { pendingSubmission = JSON.parse(localStorage.getItem(pendingKey) || 'null'); } catch (_) {}
      const [data] = await Promise.all([api('/drafts'),templatesReady,modelsReady,window.portraitPeople?.ready]);
      (data.items || []).forEach(item=>drafts.set(item.id,item));
      const requested = window.delegatedEditor?.draftId || new URLSearchParams(location.search).get('draft') || localStorage.getItem(currentKey);
      const selected = drafts.get(requested) || data.items?.[0];
      const item = selected ? await api('/drafts/'+encodeURIComponent(selected.id)) : await api('/drafts','POST',{person_input_policy:'auto_virtual'});
      if (pageInactive) return;
      await restoreDraft(item); sessionReady=true;
      if ((item.person_id || null) !== (window.portraitPeople?.selected || null) || (promptEditable && item.prompt !== promptInput.value)) changed();
      if (pendingSubmission) status.textContent='上次提交结果尚未确认，点击确认可安全恢复。';
    } catch (error) { if (!pageInactive) showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  }
  document.getElementById('flow-stage-redaction').addEventListener('toggle', event => {
    if (!event.currentTarget.open) releaseVideo(previewVideo);
  });
  function stopReads() {
    invalidateInspiration();
    pageInactive = true; pageReads.abort(); draftReads.abort(); hairPreviewVersion++;
    clearTimeout(photoTimer); photoTimer = null; clearTimeout(hairPreviewTimer);
    document.querySelectorAll('video').forEach(video => { releaseVideo(video); video.removeAttribute('poster'); });
    document.querySelectorAll('img').forEach(image => image.removeAttribute('src'));
    for (const urls of mediaUrls.values()) urls.forEach(URL.revokeObjectURL);
    mediaUrls.clear(); runList.suspend();
  }
  window.addEventListener('pagehide', stopReads);
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    // Revalidate the account before displaying cached private descriptors.
    window.location.reload();
  });
  initialize(); refreshRuns();

})();
