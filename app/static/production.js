(() => {
  const sourceForm = document.getElementById('studio-job-form');
  const generationForm = document.getElementById('studio-generate-form');
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
  const status = document.getElementById('production-status');
  const assetFiles = new WeakMap();
  const uploadingFiles = new WeakMap();
  let hairPreviewVersion = 0, hairPreviewTimer = null;
  let sessionReady = false, restoring = false;
  window.productionSession = true;
  const mediaUrls = new Map();
  const accessoryLabels = {bag:'包包',hat:'帽子',watch:'手表',shoes:'鞋子',necklace:'项链',glasses:'眼镜'};
  const accessoryRules = {bag:'参考包型、颜色、材质及背带，自然手持或背戴',hat:'参考帽型、颜色与佩戴方式',watch:'参考表盘、表带与颜色，佩戴于手腕',shoes:'参考鞋型、颜色与材质，保持足部结构自然',necklace:'参考链条、吊坠及材质，佩戴于颈部',glasses:'参考镜框、镜片与颜色，保持眼部和面部特征'};
  const extraKinds = ['hairstyle','scene',...Object.keys(accessoryLabels)];
  const imageFiles = Object.fromEntries(['face','clothing',...extraKinds].map(kind=>[kind,[]]));
  const imageInputs = Object.fromEntries(Object.keys(imageFiles).map(kind=>[kind,generationForm.elements.namedItem(kind+'_image')]));
  let personMode = 'image', personVideo = null, personInputPolicy = 'auto_virtual', personInputVersion = 0;
  const personVideoPreview = document.getElementById('person-video-preview');
  let busy = false;
  let job = null;
  let preparedSignature = '';
  let savedMask = {blur_style:'mosaic',mask_mode:'face',mask_scale:1.4,threshold:0.2,keep_audio:true,robust_tracking:false};

  function renderPersonVideo() {
    const automatic = personInputPolicy === 'auto_virtual';
    personVideoPreview.hidden = !personVideo;
    if (personVideo) {
      if (personVideoPreview.getAttribute('src') !== personVideo.url) personVideoPreview.src = personVideo.url;
    } else { personVideoPreview.pause(); personVideoPreview.removeAttribute('src'); personVideoPreview.load(); }
    document.getElementById('person-video-upload').hidden = Boolean(personVideo);
    document.getElementById('person-video-name').textContent = personVideo ? '已选人物视频' : '未设置';
    document.getElementById('person-video-replace').hidden = !personVideo;
    document.getElementById('person-video-remove').hidden = !personVideo;
    document.getElementById('person-video-status').textContent = automatic ? (personVideo ? '已保存，生成时自动准备人物。' : '上传人物视频，生成时自动准备。') : personVideo ? '人物视频可用' : '上传视频或从人物库选择。';
    updateButtons();
  }
  function setPersonMode(mode) {
    if (personMode !== mode) personInputVersion++;
    personMode = mode;
    document.getElementById('person-image-panel').hidden = mode !== 'image';
    document.getElementById('person-video-panel').hidden = mode !== 'video';
    if (mode !== 'video') personVideoPreview.pause();
    document.querySelectorAll('[data-person-media]').forEach(button => { button.hidden = button.dataset.personMedia === mode; });
    window.portraitPeople?.restore(window.portraitPeople.selected);
    renderPersonVideo(); syncReferencePrompt(); updateButtons();
  }
  document.querySelectorAll('[data-person-media]').forEach(button => button.addEventListener('click',() => {
    if (busy || !sessionReady) return;
    setPersonMode(button.dataset.personMedia); syncImages('face'); clearResult(); refreshPhotoInputs(); changed();
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
    const current = () => version === personInputVersion && personMode === 'video' && personInputPolicy === 'auto_virtual' && draft?.id === targetDraft && window.portraitPeople?.selected === targetPerson;
    document.getElementById('person-video-status').textContent = '正在保存人物视频…';
    try {
      const asset = await persistFile(file,'person_video');
      if (!current()) return;
      personVideo = asset; renderPersonVideo(); clearResult(); changed();
    } catch (error) {
      if (current()) document.getElementById('person-video-status').textContent = '视频保存失败：' + error.message;
    }
  });
  personVideoPreview.addEventListener('loadedmetadata',updateButtons);
  personVideoPreview.addEventListener('error',() => {
    if (personVideo) document.getElementById('person-video-status').textContent = '浏览器无法预览此视频，请尝试 H.264 编码的 MP4；生成前仍会检查素材。';
  });

  function maskValues() {
    return {...savedMask};
  }
  function applyMask(values) {
    savedMask = {...values};
  }
  savedMask = maskValues();
  function signature() {
    const file = source.files[0];
    return JSON.stringify([file ? [file.name, file.size, file.lastModified] : null, sourceUrl.value.trim(), maskValues()]);
  }
  function hasSource() { return Boolean(source.files.length || sourceUrl.value.trim() || job?.defaced_url); }
  function referenceTotal() { return (personMode === 'video' ? 0 : imageFiles.face.length) + imageFiles.clothing.length + extraKinds.reduce((n,k)=>n+(document.getElementById(k+'-enabled').checked ? imageFiles[k].length : 0),0); }
  function modelLimits() { return window.generationOptions?.limits() || {max_images:9,max_video_seconds:15}; }
  function referenceOverLimit() { const max = modelLimits().max_images; return max != null && referenceTotal()>max; }
  function updateButtons() {
    const total = referenceTotal(), over = referenceOverLimit();
    const sourceSeconds = document.querySelector('#video-reference-preview video')?.duration;
    const personSeconds = personVideoPreview.duration;
    const seconds = sourceSeconds + personSeconds, maxSeconds = modelLimits().max_video_seconds;
    const durationOver = (personMode === 'video' && personVideo && Number.isFinite(seconds) && seconds > maxSeconds) || (modelLimits().follow_source && Number.isFinite(sourceSeconds) && (sourceSeconds < 2 || sourceSeconds > maxSeconds));
    const durationHint = document.getElementById('person-video-duration');
    durationHint.textContent = Number.isFinite(seconds) && personVideo ? `动作 ${sourceSeconds.toFixed(1)} 秒 + 人物 ${personSeconds.toFixed(1)} 秒 = ${seconds.toFixed(1)} / ${maxSeconds} 秒` + (durationOver ? '，请缩短其中一段。' : '') : `动作视频 + 人物视频，合计不超过 ${maxSeconds} 秒。`;
    durationHint.dataset.error = String(Boolean(durationOver));
    const sourceHint = document.getElementById('source-model-hint');
    sourceHint.hidden = !modelLimits().follow_source;
    sourceHint.textContent = Number.isFinite(sourceSeconds) && (sourceSeconds < 2 || sourceSeconds > maxSeconds) ? `当前模型需要 2–${maxSeconds} 秒的动作视频，请更换或裁剪。` : `视频编辑将保留原视频时长，参考视频合计不超过 ${maxSeconds} 秒。`;
    face.required = personMode === 'image';
    const counter = document.getElementById('reference-count');
    counter.textContent = `本次参考图 ${total}${modelLimits().max_images == null ? '' : ' / '+modelLimits().max_images} 张` + (over ? ' · 请关闭部分可选项后生成' : '');
    window.generationOptions?.refreshSource();
    counter.classList.toggle('over-limit',over);
    importButton.disabled = busy || !sessionReady || !sourceUrl.value.trim();
    previewButton.disabled = busy || !sessionReady || !hasSource();
    generateButton.disabled = busy || !sessionReady || (!pendingSubmission && (window.generationOptions?.available() === false || referenceOverLimit() || durationOver || !(hasSource() && (personMode === 'video' ? personVideo && (personInputPolicy === 'auto_virtual' || window.portraitPeople?.selected) : face.files.length) && clothing.files.length)));
    generateButton.formNoValidate = Boolean(pendingSubmission);
    for (const id of ['replace-source-video', 'remove-source-video']) document.getElementById(id).hidden = !hasSource();
    if (!busy) generateButton.textContent = pendingSubmission ? '确认上次提交结果' : '生成视频 →';
    if (!busy && ['选择三类素材后即可生成', '素材已就绪，可以生成'].includes(status.textContent)) {
      status.textContent = generateButton.disabled ? '选择三类素材后即可生成' : '素材已就绪，可以生成';
    }
  }
  function lock(value) {
    busy = value;
    window.portraitPeople?.lock(value);
    generationForm.setAttribute('aria-busy', String(value));
    [...sourceForm.elements, ...generationForm.querySelectorAll('input,textarea,[data-prompt-template],[data-template-action],[data-asset-edit]'), document.getElementById('toggle-video-url')].forEach(input => input.disabled = value);
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
    video.pause(); video.removeAttribute('src'); video.hidden = true;
    document.getElementById('studio-download').hidden = true;
  }
  function invalidate() {
    job = null; preparedSignature = ''; preview.hidden = true;
    previewVideo.pause(); previewVideo.removeAttribute('src');
    previewStatus.textContent = '素材或设置已变化，点击重新预览';
    status.textContent = '选择三类素材后即可生成';
    clearResult(); updateButtons(); changed();
  }
  function showFiles(input, kind) {
    (mediaUrls.get(kind) || []).forEach(URL.revokeObjectURL);
    const files = [...input.files];
    const urls = files.map(file => URL.createObjectURL(file));
    mediaUrls.set(kind, urls);
    const target = document.getElementById(`${kind}-reference-preview`);
    target.classList.toggle('single-image', files.length === 1);
    target.replaceChildren(...urls.map((url, index) => {
      const element = document.createElement(kind === 'video' ? 'video' : 'img');
      element.src = url;
      if (kind === 'video') { element.controls = true; element.preload = 'metadata'; element.addEventListener('loadedmetadata',updateButtons); return element; }
      element.alt = files[index].name;
      element.decoding = 'async';
      const item = document.createElement('span'); item.className = 'reference-thumb';
      const caption = document.createElement('span'); caption.className = 'reference-caption';
      caption.textContent = files[index].name; caption.title = files[index].name;
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
      item.append(element, tag, caption, actions);
      const portrait = !window.portraitPeople?.selected ? assetFiles.get(files[index])?.portrait : null;
      if (kind === 'face' && portrait?.status === 'Active' && portrait.remote_asset_id) {
        const badge = document.createElement('span'); badge.className = 'portrait-badge';
        badge.textContent = '官方已授权'; badge.title = '此图片来自官方授权素材；生成时将重新校验授权状态。'; item.append(badge);
      }
      return item;
    }));
    document.getElementById(`${kind}-picker`).classList.toggle('has-media', urls.length > 0);
    const name = document.getElementById(kind === 'video' ? 'source-file-name' : `${kind}-file-name`);
    name.textContent = files.length ? kind === 'video' ? files[0].name : `已选 ${files.length} 张 · 第一张为主参考` : kind === 'video' ? '建议 5–15 秒' : '可选择一张或多张图片';
  }
  function syncImages(kind) {
    const input = imageInputs[kind];
    const transfer = new DataTransfer();
    imageFiles[kind].forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    showFiles(input, kind); clearResult(); updateButtons(); changed();
    if (kind === 'face') renderPhotoStatus();
    if (kind === 'hairstyle') scheduleHairPreview();
    refreshExtraState();
  }
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
    if (source.files.length) { sourceUrl.value = ''; document.getElementById('video-url-entry').hidden = true; document.getElementById('toggle-video-url').setAttribute('aria-expanded', 'false'); }
    showFiles(source, 'video'); invalidate();
  });
  sourceUrl.addEventListener('input', () => {
    if (sourceUrl.value.trim()) { source.value = ''; showFiles(source, 'video'); }
    if (sourceUrl.value.trim()) document.getElementById('source-file-name').textContent = '已填写视频链接';
    invalidate();
  });
  document.getElementById('replace-source-video').addEventListener('click', () => { if (!busy) source.click(); });
  document.getElementById('remove-source-video').addEventListener('click', () => {
    if (busy) return;
    source.value = ''; sourceUrl.value = ''; showFiles(source, 'video');
    document.getElementById('video-url-entry').hidden = true;
    document.getElementById('toggle-video-url').setAttribute('aria-expanded', 'false'); invalidate();
  });
  document.getElementById('toggle-video-url').addEventListener('click', event => {
    const entry = document.getElementById('video-url-entry'); entry.hidden = !entry.hidden;
    event.currentTarget.setAttribute('aria-expanded', String(!entry.hidden));
    if (!entry.hidden) sourceUrl.focus();
  });
  importButton.addEventListener('click', async () => {
    if (busy || !sourceUrl.value.trim()) return;
    const text = sourceUrl.value.trim();
    lock(true);
    importButton.textContent = '正在加载视频…';
    linkStatus.dataset.error = 'false';
    linkStatus.textContent = '正在解析链接并下载视频，请稍候…';
    document.getElementById('video-picker').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/production/assets/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : '视频导入失败，请稍后重试。');
      }
      linkStatus.textContent = '正在加载视频到页面…';
      const asset = await response.json();
      const file = await fileForAsset(asset);
      const filename = asset.name;
      const transfer = new DataTransfer(); transfer.items.add(file);
      source.files = transfer.files;
      sourceUrl.value = '';
      // Reuse the upload path without another paid link resolution.
      showFiles(source, 'video'); invalidate();
      document.getElementById('source-file-name').textContent = '链接视频 · ' + filename;
      linkStatus.textContent = '视频已加载，可在上方播放，也可继续打码或生成。';
      const video = document.querySelector('#video-reference-preview video');
      video.playsInline = true;
      video.addEventListener('error', () => {
        if (!video.isConnected) return;
        linkStatus.dataset.error = 'true';
        linkStatus.textContent = '视频已导入，但浏览器无法播放此格式，请更换视频或尝试打码预览。';
      }, {once: true});
    } catch (error) {
      linkStatus.dataset.error = 'true';
      linkStatus.textContent = error.message || '网络异常，视频导入失败，请重试。';
    } finally {
      document.getElementById('video-picker').removeAttribute('aria-busy');
      importButton.textContent = '确认并加载视频';
      lock(false);
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
    const input = generationForm.elements.namedItem('prompt');
    const rules = [
      '严格参考：参考素材在各自负责范围内优先于文字描述，文字仅补充未指定细节。',
      '@Video1 动作主参考：严格遵循动作顺序、关键姿态、移动方向、运镜、构图和节奏，不自行增加动作或镜头；不采用其中的人脸和服装，不生成打码痕迹。'
    ];
    const identity = personMode === 'video' ? '@Video2' : '@Image1';
    const clothing = personMode === 'video' ? '@Image1' : '@Image2';
    if (personMode === 'video' || imageFiles.face.length) rules.push(`${identity} 人物主参考：锁定脸型、五官比例、肤色与人物身份，全片一致；不混入其他素材人物，不自行美化或重塑五官。`);
    if (imageFiles.clothing.length) rules.push(`${clothing} 服装主参考：严格还原款式、剪裁、版型、颜色、材质、纹理、图案及可见细节，不改款、不换色、不增加装饰。`);
    let supplement = (personMode === 'video' ? 0 : Math.min(1,imageFiles.face.length)) + Math.min(1,imageFiles.clothing.length);
    if (personMode !== 'video') for (const _ of imageFiles.face.slice(1)) rules.push(`@Image${++supplement} 仅补充人物角度和细节，冲突时以人物主参考为准。`);
    for (const _ of imageFiles.clothing.slice(1)) rules.push(`@Image${++supplement} 仅补充服装角度和细节，冲突时以服装主参考为准。`);
    let index = (personMode === 'video' ? 0 : imageFiles.face.length) + imageFiles.clothing.length;
    if (personMode === 'video') rules.push('人物身份以 @Video2 为准；动作和镜头以 @Video1 为准；衣服主参考为 @Image1。不采用视频2的动作、服装、背景、声音或台词。');
    const hair = imageFiles.hairstyle.length > 0 && document.getElementById('hairstyle-enabled').checked;
    const description = document.getElementById('scene-description').value.trim();
    const sceneEnabled = document.getElementById('scene-enabled').checked;
    const scene = imageFiles.scene.length > 0 && sceneEnabled;
    if (hair) rules.push(`@Image${++index} 发型参考：采用图中的发长、轮廓、刘海、卷曲程度和发色；仅参考头发，不采用该图的人脸、身份、服装或背景。人物身份以 ${personMode === 'video' ? '@Video2' : '@Image1'} 为准，发型以本图为准。`);
    else rules.push(personMode === 'video' ? '发型沿用人物参考视频 @Video2，不使用独立发型图。' : '发型沿用主人物参考图，不使用独立发型图。');
    if (scene) {
      rules.push(`@Image${++index} 场景参考：使用图中的空间、布景、光线和环境替换原视频背景；不引入图中的人物，保留 @Video1 的主体动作、运镜与节奏。`);
      if (description) rules.push('场景补充：'+description);
    } else if (sceneEnabled && description) rules.push('按文字描述替换场景：'+description+'；保留原视频的动作、镜头与节奏。');
    else rules.push('保留原视频场景，不替换背景。');
    for (const [kind,label] of Object.entries(accessoryLabels)) {
      if (imageFiles[kind].length && document.getElementById(kind+'-enabled').checked) rules.push(`@Image${++index} ${label}参考：${accessoryRules[kind]}；仅采用对应配饰，不引入图中人物、服装或背景。`);
    }
    rules.push('独立发型、场景或配饰在各自范围内优先，严格保持对应参考的可见细节，不混用其他内容。禁止凭空增加人物、配饰、文字、水印或特效；全片保持身份、穿着和细节连续一致。');
    const base = stripReferenceRules(input.value)
      .replace(/@(?:Image1|Video2)人物参考(?:图|视频)/g, identity + (personMode === 'video' ? '人物参考视频' : '人物参考图'))
      .replace(/@(?:Image1|Video2)(?=的人物身份|的脸|为主人物身份)/g, identity)
      .replace(/@Image[12](?=衣服参考图|服装参考图|的服装|服装|为主服装)/g, clothing);
    input.value = base + '\n\n【素材联动】\n' + rules.join('\n') + '\n【联动结束】';
    input.rows = 9;
    const hint = document.getElementById('prompt-reference-status');
    if (hint) hint.textContent = '已启用严格参考';
  }
  window.productionPrompt = {strip:stripReferenceRules, sync:syncReferencePrompt};
  function refreshExtraState() {
    for (const kind of extraKinds) {
      const enabled = document.getElementById(kind+'-enabled');
      const present = imageFiles[kind].length > 0 || (kind==='scene' && document.getElementById('scene-description').value.trim());
      document.getElementById(kind+'-reference-state').textContent = !present ? '可选' : enabled.checked ? '已设置' : '未启用';
      if (accessoryLabels[kind]) {
        document.getElementById(kind+'-references').hidden = !imageFiles[kind].length;
        document.getElementById(kind+'-add').classList.toggle('has-reference',Boolean(imageFiles[kind].length));
      }
      if (!imageFiles[kind].length) document.getElementById(kind+'-file-name').textContent = '未设置';
      else document.getElementById(kind+'-file-name').textContent = imageFiles[kind][0].name;
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
    if (panel.hidden) return;
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
    const response = await fetch(url, options);
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
      if (previewVideo.getAttribute('src') !== current.defaced_url) previewVideo.src = current.defaced_url;
    }
    if (current.status === 'succeeded' && current.download_url) {
      document.getElementById('generation-result').hidden = false;
      const text = current.provider === 'mock' ? '本地演示完成（打码视频）' : '生成完成';
      document.getElementById('generation-message').textContent = text;
      const video = document.getElementById('studio-final-video'); video.src = current.download_url; video.hidden = false;
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
      current = await jsonRequest(`/api/jobs/${encodeURIComponent(current.id)}`);
    }
  }
  async function prepareVideo(body, fingerprint, isGeneration) {
    if (job?.defaced_url && preparedSignature === fingerprint && (job.status === 'defaced' || (!isGeneration && job.status === 'succeeded'))) {
      render(job, false); previewStatus.textContent = '预览已就绪'; return job;
    }
    if (!source.files.length && !sourceUrl.value.trim()) throw new Error('请重新选择参考视频。');
    previewStatus.textContent = '正在处理预览…';
    if (isGeneration) { generateButton.textContent = '正在准备视频…'; status.textContent = '正在准备视频'; }
    const created = await jsonRequest('/api/jobs', {method: 'POST', body});
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
    if (busy || !sourceForm.reportValidity()) return;
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
    if (busy || !sessionReady || (!pendingSubmission && !sourceForm.reportValidity())) return;
    clearResult(); lock(true); generateButton.textContent = '正在提交…';
    status.textContent = '正在保存草稿并加入任务队列';
    try {
      if (!pendingSubmission) {
        acceptTaskName();
        const saved = await flushDraft();
        pendingSubmission = {draft_id: saved.id, revision: saved.revision, idempotency_key: crypto.randomUUID()};
        // Persist before the request: a lost response must retry the identical submission.
        localStorage.setItem(pendingKey, JSON.stringify(pendingSubmission));
      }
      const submitted = await api('/runs', 'POST', pendingSubmission);
      pendingSubmission = null; localStorage.removeItem(pendingKey);
      runs.set(submitted.id, submitted); renderRuns();
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
  const currentKey = 'production-current-draft-v1', pendingKey = 'production-pending-submit-v1';
  const saveStatus = document.getElementById('draft-save-status');
  const retrySave = document.getElementById('draft-save-retry');
  const recoverDraft = document.getElementById('draft-recover');
  const modelForm = document.getElementById('model-settings-form');
  const publicModelFields = ['model','duration','resolution'];
  const drafts = new Map(), runs = new Map();
  let draft = null, draftName = null, dirtyVersion = 0, savedVersion = 0, saveTimer, savePromise = null, pendingSubmission = null;
  try { pendingSubmission = JSON.parse(localStorage.getItem(pendingKey) || 'null'); } catch (_) {}
  const templatesReady = new Promise(resolve => window.addEventListener('production-templates-ready', resolve, {once:true}));
  const modelsReady = new Promise(resolve => window.addEventListener('model-settings-loaded', resolve, {once:true}));
  const defaultMask = maskValues(), defaultPrompt = generationForm.elements.namedItem('prompt').value;

  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/production' + path, {
      method, cache: 'no-store', headers: body ? {'Content-Type':'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
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
  }
  function changed() {
    if (!sessionReady || restoring) return;
    queueMicrotask(() => { window.productionDraftModel = publicModel(); });
    dirtyVersion++; showSave('有修改，正在保存…');
    clearTimeout(saveTimer);
    // Start media persistence immediately; draft update follows when upload completes.
    const uploadingPerson = window.portraitPeople?.selected, uploadingDraft = draft?.id, uploadingPolicy = personInputPolicy, uploadingVersion = personInputVersion;
    [['video',source.files[0]], ...Object.entries(imageFiles).flatMap(([kind,files])=>files.map(file=>[kind,file]))].filter(([,file])=>file).forEach(([kind,file]) => {
      persistFile(file, kind).then(asset => { if (kind === 'face' && personMode === 'image' && uploadingPolicy === personInputPolicy && uploadingVersion === personInputVersion && imageFiles.face.includes(file) && draft?.id === uploadingDraft && window.portraitPeople?.selected === uploadingPerson) queuePhoto(file, asset, uploadingPerson); }).catch(error => { if (draft?.id === uploadingDraft && personInputPolicy === uploadingPolicy && personInputVersion === uploadingVersion) showSave('素材保存失败：' + error.message, true); });
    });
    saveTimer = setTimeout(() => flushDraft().catch(() => {}), 500);
  }
  async function persistFile(file, kind) {
    if (assetFiles.has(file)) return assetFiles.get(file);
    if (uploadingFiles.has(file)) return uploadingFiles.get(file);
    const promise = (async () => {
      const body = new FormData(); body.append('file', file); body.append('kind', kind);
      const response = await fetch('/api/production/assets', {method:'POST', body});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请重试保存素材');
      assetFiles.set(file, data); return data;
    })();
    uploadingFiles.set(file, promise);
    try { return await promise; } finally { uploadingFiles.delete(file); }
  }
  async function fileForAsset(asset, signal) {
    const response = await fetch(asset.url, {signal});
    if (!response.ok) throw new Error('素材读取失败：' + asset.name);
    const file = new File([await response.blob()], asset.name, {type:asset.mime || (asset.kind === 'video' ? 'video/mp4' : 'image/png')});
    assetFiles.set(file, asset); return file;
  }
  window.productionPortraits = {
    get inputPolicy() { return personInputPolicy; },
    get referenceMode() { return personMode; },
    get currentPhoto() { return personMode === 'video' ? personVideo : assetFiles.get(imageFiles.face[0]) || null; },
    async importAsset(asset, {signal} = {}) {
      if (!sessionReady || restoring) throw new Error('草稿尚未恢复，请稍候再导入。');
      if (busy) throw new Error('编辑器正在处理，请完成当前操作后重试。');
      if (!['face','person_video'].includes(asset?.kind) || asset.portrait?.status !== 'Active' || !asset.portrait.remote_asset_id) {
        throw new Error('官方人物素材状态无效，请刷新列表后重试。');
      }
      lock(true);
      try {
        if (asset.kind === 'person_video') {
          const oldPerson = window.portraitPeople?.selected;
          await window.portraitPeople.selectGroup(asset.portrait.group_id,asset.person_type || 'LivenessFace');
          personInputVersion++; personInputPolicy = 'existing_person';
          if (oldPerson !== window.portraitPeople.selected) { imageFiles.face = []; syncImages('face'); }
          personVideo = asset; setPersonMode('video'); changed();
          try { await flushDraft(); } catch (_) { throw new Error('人物视频已加载，但草稿保存失败，请重试保存。'); }
          status.textContent = '人物视频已设为本次身份参考'; return;
        }
        let file;
        try { file = await fileForAsset(asset, signal); }
        catch (error) {
          if (error.name === 'AbortError') throw error;
          throw new Error('官方图片读取失败，原人物图片已保留。请检查网络后重试。');
        }
        if (signal?.aborted) throw new DOMException('Dialog closed', 'AbortError');
        const oldPerson = window.portraitPeople?.selected;
        if (window.portraitPeople) await window.portraitPeople.selectGroup(asset.portrait.group_id, asset.person_type || 'LivenessFace');
        personInputVersion++; personInputPolicy = 'existing_person';
        const remaining = oldPerson === window.portraitPeople?.selected ? imageFiles.face.slice(1).filter(item => assetFiles.get(item)?.sha256 !== asset.sha256) : [];
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
  function photoKey(file, person) { return person + ':' + assetFiles.get(file)?.id; }
  function currentPhotos() {
    const person = window.portraitPeople?.selected;
    return person && personInputPolicy !== 'auto_virtual' && personMode === 'image' ? imageFiles.face.map(file => photoRecords.get(photoKey(file, person))).filter(Boolean) : [];
  }
  function renderPhotoStatus() {
    const label = document.getElementById('person-photo-status');
    if (!label) return;
    const records = currentPhotos();
    const failed = records.find(x => x.status === 'failed' || x.status === 'uncertain');
    label.dataset.error = String(Boolean(failed));
    label.textContent = personInputPolicy === 'auto_virtual' ? (imageFiles.face.length ? (imageFiles.face.every(file => assetFiles.has(file)) ? '已保存，生成时自动准备人物。' : '正在保存人物参考图…') : '上传人物参考图，生成时自动准备。') : !window.portraitPeople?.selected ? '参考图已恢复，可继续生成。' :
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
      const asset = assetFiles.get(file);
      if (asset) queuePhoto(file,asset,person);
    });
    renderPhotoStatus();
  }
  function schedulePhotoPoll() {
    if (photoTimer || photoPolling || !currentPhotos().some(x => x.id && !['active','failed'].includes(x.status))) return;
    photoTimer = setTimeout(pollPhotos, document.hidden ? 15000 : 5000);
  }
  async function pollPhotos() {
    photoTimer = null;
    const jobs = currentPhotos().filter(x => x.id && !['active','failed'].includes(x.status));
    if (!jobs.length || photoPolling) return;
    const version = personInputVersion, targetDraft = draft?.id, targetPerson = window.portraitPeople?.selected, policy = personInputPolicy;
    const current = () => personInputVersion === version && draft?.id === targetDraft && window.portraitPeople?.selected === targetPerson && personInputPolicy === policy;
    photoPolling = true;
    try {
      const data = await window.portraitPeople.request('photos?ids='+encodeURIComponent([...new Set(jobs.map(x=>x.id))].join(',')));
      if (!current()) return;
      const updated = new Map((data.items || []).map(x=>[x.id,x]));
      for (const [key,job] of photoRecords) if (updated.has(job.id)) photoRecords.set(key,updated.get(job.id));
      renderPhotoStatus();
    } catch (error) {
      if (!current()) return;
      const label=document.getElementById('person-photo-status');
      label.textContent='照片状态查询暂时失败，稍后自动恢复；可以继续编辑。';
    } finally { photoPolling=false; schedulePhotoPoll(); }
  }
  document.getElementById('person-photo-retry')?.addEventListener('click',async () => {
    const person=window.portraitPeople?.selected;
    for (const file of [...imageFiles.face]) {
      const key=photoKey(file,person), job=photoRecords.get(key);
      if (!job || !['failed','uncertain'].includes(job.status)) continue;
      try {
        if (job.id) photoRecords.set(key,await window.portraitPeople.request('photos/'+job.id+'/retry','POST',{}));
        else { photoRecords.delete(key); await queuePhoto(file,assetFiles.get(file),person); }
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
    return Object.fromEntries(publicModelFields.map(name => [name, name === 'duration' ? Number(modelForm.querySelector(`[name="${name}"]`).value) : modelForm.querySelector(`[name="${name}"]`).value.trim()]));
  }
  async function captureDraft() {
    // Read all fields synchronously before awaiting uploads: saves represent one editor version.
    const videoFile = source.files[0], faceFiles = [...imageFiles.face], clothingFiles = [...imageFiles.clothing];
    const extraFiles = Object.fromEntries(extraKinds.map(kind=>[kind,[...imageFiles[kind]]]));
    const selectedPerson = window.portraitPeople?.selected || null;
    const values = {person_input_policy:personInputPolicy,person_reference_mode:personMode,person_video_asset_id:personVideo?.id || null,person_id:selectedPerson, name:draftName || draft?.name || '未命名视频', prompt:generationForm.elements.namedItem('prompt').value, mask:maskValues(), model:publicModel()};
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
    restoring = true; personInputVersion++;
    try {
      const known = new Map((item.assets || []).map(asset => [asset.id,asset]));
      const restore = async id => {
        const asset = known.get(id);
        if (!asset) throw new Error('草稿素材缺失，请重试加载。');
        return fileForAsset(asset);
      };
      const [videoFile, faces, clothes, extras] = await Promise.all([
        item.source_asset_id ? restore(item.source_asset_id) : null,
        Promise.all((item.face_asset_ids || []).map(restore)),
        Promise.all((item.clothing_asset_ids || []).map(restore)),
        Promise.all(extraKinds.map(async kind=>[kind,await Promise.all((item[kind+'_asset_ids'] || []).map(restore))])),
      ]);
      const transfer = new DataTransfer(); if (videoFile) transfer.items.add(videoFile);
      source.files = transfer.files; sourceUrl.value = '';
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
      document.getElementById('hairstyle-references').open = false;
      document.getElementById('accessory-references').open = Object.keys(accessoryLabels).some(k=>imageFiles[k].length);
      draft = item; draftName=item.name; drafts.set(item.id,item); syncTaskName();
      window.portraitPeople?.restore(item.person_id);
      generationForm.elements.namedItem('prompt').value = item.prompt ?? defaultPrompt;
      applyMask({...defaultMask,...item.mask}); savedMask = maskValues();
      window.generationOptions.restore(item.model);
      window.productionDraftModel = {...item.model};
      setPersonMode(personMode); showFiles(source,'video'); Object.keys(imageFiles).forEach(syncImages); invalidate();
      dirtyVersion = savedVersion = 0;
      localStorage.setItem(currentKey,item.id);
      ['production-prompt-draft-v1','studio-redaction-settings','active-v1-job'].forEach(key=>localStorage.removeItem(key));
      showSave('已保存到本机');
    } finally { restoring = false; refreshPhotoInputs(); }
    if (sessionReady && item.prompt !== generationForm.elements.namedItem('prompt').value) changed();
  }
  function draftControls(disabled) {
    ['draft-recover'].forEach(id => document.getElementById(id).disabled = disabled);
  }
  async function changeDraft(action) {
    if (busy || !sessionReady) return;
    lock(true); draftControls(true);
    try {
      await flushDraft();
      const item = await action();
      await restoreDraft(item);
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
    if (busy) return;
    lock(true); draftControls(true);
    try {
      const values = await captureDraft();
      const item = await api('/drafts','POST',{name:values.name + '（恢复）',person_input_policy:values.person_input_policy});
      const saved = await api('/drafts/'+encodeURIComponent(item.id),'PUT',{...values,name:item.name,revision:item.revision});
      await restoreDraft(saved); recoverDraft.hidden = true;
    } catch (error) { showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  });
  generationForm.elements.namedItem('prompt').addEventListener('input',changed);
  generationForm.elements.namedItem('prompt').addEventListener('blur',() => {
    const input = generationForm.elements.namedItem('prompt'), before = input.value;
    syncReferencePrompt();
    if (input.value !== before) changed();
  });
  window.addEventListener('production-prompt-changed',event => { if (event.detail?.applyTemplate) syncReferencePrompt(); changed(); });
  window.addEventListener('model-settings-saved',() => { updateButtons(); changed(); });
  window.addEventListener('beforeunload', event => {
    if (savedVersion !== dirtyVersion) { event.preventDefault(); event.returnValue = ''; }
  });

  const runList=window.createProductionRuns({api,changeDraft,accessoryLabels});
  function renderRuns(){runList.submitted();}
  function refreshRuns(){return runList.refresh();}
  async function initialize() {
    lock(true); draftControls(true);
    try {
      const [data] = await Promise.all([api('/drafts'),templatesReady,modelsReady,window.portraitPeople?.ready]);
      (data.items || []).forEach(item=>drafts.set(item.id,item));
      const requested = localStorage.getItem(currentKey);
      const selected = drafts.get(requested) || data.items?.[0];
      const item = selected ? await api('/drafts/'+encodeURIComponent(selected.id)) : await api('/drafts','POST',{person_input_policy:'auto_virtual'});
      await restoreDraft(item); sessionReady=true;
      if (/^未命名视频(?: 副本)*$/.test(draft.name)) {
        const stamp=new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(/\D/g,'').slice(4);
        draftName='视频-'+stamp.slice(0,4)+'-'+stamp.slice(4);syncTaskName();changed();
      }
      if ((item.person_id || null) !== (window.portraitPeople?.selected || null) || item.prompt !== generationForm.elements.namedItem('prompt').value) changed();
      if (pendingSubmission) status.textContent='上次提交结果尚未确认，点击确认可安全恢复。';
    } catch (error) { showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  }
  initialize(); refreshRuns();

})();
