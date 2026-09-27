(() => {
  const sourceForm = document.getElementById('studio-job-form');
  const generationForm = document.getElementById('studio-generate-form');
  const source = sourceForm.elements.namedItem('video');
  const sourceUrl = sourceForm.elements.namedItem('video_url');
  const importButton = document.getElementById('confirm-video-url');
  const linkStatus = document.getElementById('video-link-status');
  const face = generationForm.elements.namedItem('face_image');
  const clothing = generationForm.elements.namedItem('clothing_image');
  const mask = document.getElementById('redaction-settings');
  const model = document.getElementById('model-settings');
  const preview = document.getElementById('studio-preview');
  const previewVideo = document.getElementById('studio-defaced-video');
  const previewButton = document.getElementById('studio-preview-submit');
  const previewStatus = document.getElementById('studio-preview-status');
  const generateButton = document.getElementById('studio-generate-submit');
  const status = document.getElementById('production-status');
  const maskFields = [...mask.querySelectorAll('input,select')];
  const assetFiles = new WeakMap();
  const uploadingFiles = new WeakMap();
  let sessionReady = false, restoring = false;
  window.productionSession = true;
  const mediaUrls = new Map();
  const imageFiles = {face: [], clothing: []};
  let busy = false;
  let job = null;
  let preparedSignature = '';
  let savedMask = {};

  function maskValues() {
    return Object.fromEntries(maskFields.map(input => [input.name, input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value]));
  }
  function applyMask(values) {
    maskFields.forEach(input => {
      if (values[input.name] === undefined) return;
      if (input.type === 'checkbox') input.checked = Boolean(values[input.name]);
      else input.value = values[input.name];
    });
  }
  savedMask = maskValues();
  function signature() {
    const file = source.files[0];
    return JSON.stringify([file ? [file.name, file.size, file.lastModified] : null, sourceUrl.value.trim(), maskValues()]);
  }
  function hasSource() { return Boolean(source.files.length || sourceUrl.value.trim() || job?.defaced_url); }
  function updateButtons() {
    importButton.disabled = busy || !sessionReady || !sourceUrl.value.trim();
    previewButton.disabled = busy || !sessionReady || !hasSource();
    generateButton.disabled = busy || !sessionReady || (!pendingSubmission && !(hasSource() && face.files.length && clothing.files.length));
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
    mask.querySelectorAll('[data-redaction-save]').forEach(button => button.disabled = value);
    ['draft-recover'].forEach(id => document.getElementById(id).disabled = value || !sessionReady);
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
      if (kind === 'video') { element.controls = true; element.preload = 'metadata'; return element; }
      element.alt = files[index].name;
      element.decoding = 'async';
      const item = document.createElement('span'); item.className = 'reference-thumb';
      const caption = document.createElement('span'); caption.className = 'reference-caption';
      caption.textContent = files[index].name; caption.title = files[index].name;
      const tag = document.createElement('span'); tag.className = 'reference-index';
      tag.textContent = index === 0 ? '主参考' : String(index + 1);
      const actions = document.createElement('span'); actions.className = 'reference-actions';
      for (const [label, action] of [['替换', () => replaceImage(kind, index)], ['删除', () => {
        imageFiles[kind].splice(index, 1); syncImages(kind);
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
    const input = kind === 'face' ? face : clothing;
    const transfer = new DataTransfer();
    imageFiles[kind].forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    showFiles(input, kind); clearResult(); updateButtons(); changed();
    if (kind === 'face') renderPhotoStatus();
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
      if (!busy && file && validImage(file)) { imageFiles[kind][index] = file; syncImages(kind); }
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
  for (const [input, kind] of [[face, 'face'], [clothing, 'clothing']]) input.addEventListener('change', () => {
    if (busy) return;
    const identity = file => JSON.stringify([file.name, file.size, file.lastModified]);
    const seen = new Set(imageFiles[kind].map(identity));
    for (const file of input.files) {
      if (validImage(file) && !seen.has(identity(file))) { imageFiles[kind].push(file); seen.add(identity(file)); }
    }
    syncImages(kind);
  });
  document.querySelectorAll('[data-add-images]').forEach(button => button.addEventListener('click', () => {
    if (!busy) (button.dataset.addImages === 'face' ? face : clothing).click();
  }));
  maskFields.forEach(input => input.addEventListener('input', invalidate));

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
  mask.querySelectorAll('[data-redaction-save]').forEach(button => button.addEventListener('click', () => {
    if (busy || !sourceForm.reportValidity()) return;
    savedMask = maskValues();
    changed();
    mask.open = false;
  }));
  function cancelMask() {
    if (!busy && JSON.stringify(savedMask) !== JSON.stringify(maskValues())) { applyMask(savedMask); invalidate(); }
    mask.open = false; mask.querySelector('summary').focus();
  }
  mask.querySelectorAll('[data-redaction-cancel]').forEach(button => button.addEventListener('click', cancelMask));
  mask.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); cancelMask(); } });
  [mask, model].forEach(details => details.addEventListener('toggle', () => {
    if (details.open) (details === mask ? model : mask).open = false;
  }));
  const currentKey = 'production-current-draft-v1', pendingKey = 'production-pending-submit-v1';
  const saveStatus = document.getElementById('draft-save-status');
  const retrySave = document.getElementById('draft-save-retry');
  const recoverDraft = document.getElementById('draft-recover');
  const modelForm = document.getElementById('model-settings-form');
  const publicModelFields = ['provider','protocol','mode','base_url','model','duration','fps','resolution','public_base_url'];
  const drafts = new Map(), runs = new Map();
  let draft = null, dirtyVersion = 0, savedVersion = 0, saveTimer, savePromise = null, pendingSubmission = null;
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
    const uploadingPerson = window.portraitPeople?.selected, uploadingDraft = draft?.id;
    [source.files[0], ...imageFiles.face, ...imageFiles.clothing].filter(Boolean).forEach(file => {
      const kind = file === source.files[0] ? 'video' : imageFiles.face.includes(file) ? 'face' : 'clothing';
      persistFile(file, kind).then(asset => { if (kind === 'face' && imageFiles.face.includes(file) && draft?.id === uploadingDraft && window.portraitPeople?.selected === uploadingPerson) queuePhoto(file, asset, uploadingPerson); }).catch(error => showSave('素材保存失败：' + error.message, true));
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
    async importAsset(asset, {signal} = {}) {
      if (!sessionReady || restoring) throw new Error('草稿尚未恢复，请稍候再导入。');
      if (busy) throw new Error('编辑器正在处理，请完成当前操作后重试。');
      if (asset?.kind !== 'face' || asset.portrait?.status !== 'Active' || !asset.portrait.remote_asset_id) {
        throw new Error('官方人物素材状态无效，请刷新列表后重试。');
      }
      lock(true);
      try {
        let file;
        try { file = await fileForAsset(asset, signal); }
        catch (error) {
          if (error.name === 'AbortError') throw error;
          throw new Error('官方图片读取失败，原人物图片已保留。请检查网络后重试。');
        }
        if (signal?.aborted) throw new DOMException('Dialog closed', 'AbortError');
        if (window.portraitPeople) await window.portraitPeople.selectGroup(asset.portrait.group_id);
        imageFiles.face = [file, ...imageFiles.face.slice(1)];
        syncImages('face');
        try { await flushDraft(); }
        catch (_) { throw new Error('官方人物已加载，但草稿保存失败。请关闭弹窗并使用“重试保存”，暂勿刷新页面。'); }
        status.textContent = '官方已授权人物已设为主参考图';
      } finally { lock(false); }
    },
  };
  // One state query for visible photos; network results are keyed by file AND person.
  const photoRecords = new Map();
  let photoTimer = null, photoPolling = false;
  function photoKey(file, person) { return person + ':' + assetFiles.get(file)?.id; }
  function currentPhotos() {
    const person = window.portraitPeople?.selected;
    return person ? imageFiles.face.map(file => photoRecords.get(photoKey(file, person))).filter(Boolean) : [];
  }
  function renderPhotoStatus() {
    const label = document.getElementById('person-photo-status');
    if (!label) return;
    const records = currentPhotos();
    const failed = records.find(x => x.status === 'failed' || x.status === 'uncertain');
    label.dataset.error = String(Boolean(failed));
    label.textContent = !window.portraitPeople?.selected ? '真人照片请先选择或添加已认证人物。' :
      !imageFiles.face.length ? '上传这个人的照片，系统会自动校验。' : failed ? failed.message :
      records.length === imageFiles.face.length && records.every(x => x.status === 'active') ? '✓ 照片可用' : '正在校验人物照片，可继续准备素材或提交视频。';
    document.getElementById('person-photo-retry').hidden = !failed;
  }
  async function queuePhoto(file, asset, person = window.portraitPeople?.selected) {
    if (!person || !window.portraitPeople) { renderPhotoStatus(); return; }
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
    imageFiles.face.forEach(file => {
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
    photoPolling = true;
    try {
      const data = await window.portraitPeople.request('photos?ids='+encodeURIComponent([...new Set(jobs.map(x=>x.id))].join(',')));
      const updated = new Map((data.items || []).map(x=>[x.id,x]));
      for (const [key,job] of photoRecords) if (updated.has(job.id)) photoRecords.set(key,updated.get(job.id));
      renderPhotoStatus();
    } catch (error) {
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
    showFiles(face,'face'); refreshPhotoInputs(); changed();
  });
  window.addEventListener('visibilitychange',() => {
    clearTimeout(photoTimer); photoTimer=null; schedulePhotoPoll();
  });

  function publicModel() {
    return Object.fromEntries(publicModelFields.map(name => [name, ['duration','fps'].includes(name) ? Number(modelForm.elements.namedItem(name).value) : modelForm.elements.namedItem(name).value.trim()]));
  }
  async function captureDraft() {
    // Read all fields synchronously before awaiting uploads: saves represent one editor version.
    const videoFile = source.files[0], faceFiles = [...imageFiles.face], clothingFiles = [...imageFiles.clothing];
    const selectedPerson = window.portraitPeople?.selected || null;
    const values = {person_id:selectedPerson, name:draft?.name || '未命名视频', prompt:generationForm.elements.namedItem('prompt').value, mask:maskValues(), model:publicModel()};
    const [videoAsset, faces, clothes] = await Promise.all([
      videoFile ? persistFile(videoFile,'video') : null,
      Promise.all(faceFiles.map(file => persistFile(file,'face'))),
      Promise.all(clothingFiles.map(file => persistFile(file,'clothing'))),
    ]);
    return {...values, source_asset_id:videoAsset?.id || null, face_asset_ids:faces.map(a=>a.id), clothing_asset_ids:clothes.map(a=>a.id)};
  }
  async function flushDraft() {
    clearTimeout(saveTimer);
    if (!draft) throw new Error('草稿尚未恢复，请稍候');
    if (savePromise) { await savePromise; return flushDraft(); }
    if (savedVersion === dirtyVersion) return draft;
    savePromise = (async () => {
      while (savedVersion !== dirtyVersion) {
        const version = dirtyVersion, id = draft.id, revision = draft.revision;
        showSave('正在保存…');
        const values = await captureDraft();
        const saved = await api('/drafts/' + encodeURIComponent(id), 'PUT', {...values, revision});
        draft = saved; drafts.set(saved.id, saved); savedVersion = version;
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
    restoring = true;
    try {
      const known = new Map((item.assets || []).map(asset => [asset.id,asset]));
      const restore = async id => {
        const asset = known.get(id);
        if (!asset) throw new Error('草稿素材缺失，请重试加载。');
        return fileForAsset(asset);
      };
      const [videoFile, faces, clothes] = await Promise.all([
        item.source_asset_id ? restore(item.source_asset_id) : null,
        Promise.all((item.face_asset_ids || []).map(restore)),
        Promise.all((item.clothing_asset_ids || []).map(restore)),
      ]);
      const transfer = new DataTransfer(); if (videoFile) transfer.items.add(videoFile);
      source.files = transfer.files; sourceUrl.value = '';
      imageFiles.face = faces; imageFiles.clothing = clothes;
      draft = item; drafts.set(item.id,item);
      window.portraitPeople?.restore(item.person_id);
      generationForm.elements.namedItem('prompt').value = item.prompt ?? defaultPrompt;
      applyMask({...defaultMask,...item.mask}); savedMask = maskValues();
      for (const name of publicModelFields) if (item.model?.[name] !== undefined) modelForm.elements.namedItem(name).value = item.model[name];
      window.productionDraftModel = {...item.model};
      modelForm.elements.namedItem('protocol').dispatchEvent(new Event('change', {bubbles:true}));
      showFiles(source,'video'); syncImages('face'); syncImages('clothing'); invalidate();
      dirtyVersion = savedVersion = 0;
      localStorage.setItem(currentKey,item.id);
      ['production-prompt-draft-v1','studio-redaction-settings','active-v1-job'].forEach(key=>localStorage.removeItem(key));
      showSave('已保存到本机');
    } finally { restoring = false; refreshPhotoInputs(); }
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
  retrySave.addEventListener('click', () => sessionReady ? flushDraft().catch(() => {}) : initialize());
  recoverDraft.addEventListener('click', async () => {
    if (busy) return;
    lock(true); draftControls(true);
    try {
      const values = await captureDraft();
      const item = await api('/drafts','POST',{name:values.name + '（恢复）'});
      const saved = await api('/drafts/'+encodeURIComponent(item.id),'PUT',{...values,name:item.name,revision:item.revision});
      await restoreDraft(saved); recoverDraft.hidden = true;
    } catch (error) { showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  });
  generationForm.elements.namedItem('prompt').addEventListener('input',changed);
  window.addEventListener('production-prompt-changed',changed);
  window.addEventListener('model-settings-saved',changed);
  modelForm.addEventListener('input',event => { if (publicModelFields.includes(event.target.name)) changed(); });
  modelForm.addEventListener('change',event => { if (publicModelFields.includes(event.target.name)) changed(); });
  window.addEventListener('beforeunload', event => {
    if (savedVersion !== dirtyVersion) { event.preventDefault(); event.returnValue = ''; }
  });

  const locallyDeletedRuns = new Set();
  const stateNames = {defaced:'预览已完成',queued:'排队中',running:'处理中',succeeded:'已完成',failed:'失败',cancelled:'已取消',needs_attention:'需要处理'};
  const stageNames = {authorizing:'核实人物照片',preprocess:'处理参考视频',upload:'上传素材',submitting:'提交模型',generating:'模型生成',downloading:'下载结果'};
  function node(tag, text, className) {
    const element = document.createElement(tag); if (text) element.textContent = text;
    if (className) element.className = className; return element;
  }
  function renderRuns() {
    const list = document.getElementById('production-run-list');
    // Retain existing video elements and open details across progress polls.
    const retained = new Map([...list.children].map(card => [card.dataset.runId,card]));
    for (const [id,card] of retained) if (!runs.has(id) || locallyDeletedRuns.has(id)) card.remove();
    for (const item of [...runs.values()].sort((a,b) => String(b.created_at).localeCompare(String(a.created_at)))) {
      if (locallyDeletedRuns.has(item.id)) continue;
      const fingerprint = JSON.stringify(item);
      let card = retained.get(item.id);
      if (card?.dataset.version === fingerprint) { list.append(card); continue; }
      const wasOpen = card?.querySelector('details')?.open;
      const next = node('article', '', 'production-run'); next.dataset.runId = item.id; next.dataset.version = fingerprint; next.dataset.state = item.status;
      const heading = node('div','','run-card-heading');
      heading.append(node('h4', item.name || '视频任务'),node('span', (item.legacy ? '历史 · ' : '') + (stateNames[item.status] || item.status),'run-state'));
      const problems = {
        material_rejected:'人物照片未通过检查，请更换照片后重新提交。',
        submission_uncertain:'任务提交结果正在确认，请联系管理员核实，暂勿重复提交。',
        query_unavailable:'暂时无法获取生成进度，可稍后点击“继续查询 / 下载”。',
        download_failed:'视频已生成，但下载尚未完成，可点击“继续查询 / 下载”重试。',
      };
      const body = item.status === 'succeeded' ? '视频已生成，可以播放或下载。'
        : problems[item.error_kind] || (['failed','needs_attention'].includes(item.status)
          ? '本次任务未完成，请检查参考素材后重试；仍有问题可联系管理员。'
          : item.status === 'cancelled' ? '任务已取消。' : stageNames[item.stage] || '等待处理');
      next.append(heading, node('p',body,'run-message'));
      if (['queued','running'].includes(item.status)) {
        const progress = node('progress'); progress.max = 100; progress.value = item.progress || 0;
        progress.setAttribute('aria-label',(stageNames[item.stage] || '任务进度') + ' ' + (item.progress || 0) + '%'); next.append(progress);
      }
      const actions = node('div','','run-actions');
      const button = (action,label) => {
        const b = node('button',label,'secondary'); b.type='button'; b.dataset.runAction=action;
        b.addEventListener('click',async () => {
          if (action === 'details') {
            detail.open = !detail.open;
            return;
          }
          if (action === 'delete') {
            const note = item.status === 'needs_attention' ? '\n此操作不会取消服务商端可能仍在执行的任务。' : '';
            if (!window.confirm('删除这条任务记录？草稿、素材和视频文件会保留。'+note)) return;
            b.disabled = true;
            try {
              await api('/runs/'+encodeURIComponent(item.id),'DELETE');
              locallyDeletedRuns.add(item.id); runs.delete(item.id); renderRuns();
              document.getElementById('runs-status').textContent='任务已删除，草稿和素材已保留。';
            } catch (error) { document.getElementById('runs-status').textContent=error.message; b.disabled=false; }
            return;
          }
          if (action === 'copy') { await changeDraft(() => api('/runs/'+encodeURIComponent(item.id)+'/copy','POST',{})); return; }
          b.disabled = true;
          try { const updated = await api('/runs/'+encodeURIComponent(item.id)+'/'+action,'POST',{}); runs.set(updated.id,updated); renderRuns(); }
          catch (error) { document.getElementById('runs-status').textContent=error.message; b.disabled=false; }
        }); actions.append(b); return b;
      };
      const detailsButton = button('details', wasOpen ? '收起详情' : '查看详情');
      detailsButton.setAttribute('aria-expanded',String(Boolean(wasOpen)));
      button('copy','复制为草稿');
      if (item.can_cancel ?? (item.status==='queued' && !item.legacy && !item.provider_task_id)) button('cancel','取消排队');
      if (item.can_resume ?? (item.status==='needs_attention' && (item.provider_task_id || item.download_url))) button('resume','继续查询 / 下载');
      const deleteButton = button('delete','删除任务');
      deleteButton.classList.add('run-delete');
      deleteButton.disabled = !(item.can_delete ?? !['running','queued'].includes(item.status));
      if (deleteButton.disabled) deleteButton.title='任务正在处理，请等待结束后删除；未开始的任务可先取消排队。';
      if (item.download_url) {
        const link=node('a','下载视频 ↓','secondary');link.href=item.download_url;link.setAttribute('download',''); actions.append(link);
        const video=node('video');video.src=item.download_url;video.controls=true;video.preload='none';video.playsInline=true;next.append(video);
      }
      next.append(actions);
      const detail=node('details');detail.open=Boolean(wasOpen);detail.append(node('summary','任务详情'));
      detail.id = 'run-details-'+item.id;
      detailsButton.setAttribute('aria-controls',detail.id);
      detail.addEventListener('toggle',() => {
        detailsButton.textContent = detail.open ? '收起详情' : '查看详情';
        detailsButton.setAttribute('aria-expanded',String(detail.open));
      });
      const created = new Date(item.created_at);
      if (!Number.isNaN(created.getTime())) {
        detail.append(node('p','创建于 '+created.toLocaleString('zh-CN',{hour12:false}),'run-created'));
      }
      const materials = node('div','','run-detail-assets');
      const assets = item.snapshot?.assets || [];
      for (const [kind,label] of [['video','参考视频'],['face','人物参考图'],['clothing','衣服参考图']]) {
        const figure=node('figure'); figure.dataset.sourceKind=kind;
        figure.append(node('figcaption',label));
        const sources=assets.filter(asset=>asset.kind===kind);
        if (!sources.length) figure.append(node('p','这条历史任务未保留原始素材','run-source-empty'));
        for (const asset of sources) {
          const media=node(kind==='video' ? 'video' : 'img'); media.dataset.sourceUrl=asset.url;
          if (kind==='video') { media.controls=true;media.preload='none';media.playsInline=true; }
          else { media.alt=label;media.loading='lazy';media.decoding='async'; }
          media.addEventListener('error',()=>{
            media.hidden=true;
            figure.append(node('p','素材暂时无法加载，请稍后刷新重试。','run-source-empty'));
          },{once:true});
          figure.append(media);
        }
        materials.append(figure);
      }
      detail.append(materials);
      const loadSources=()=>{
        if (!detail.open) return;
        materials.querySelectorAll('[data-source-url]').forEach(media=>{
          media.src=media.dataset.sourceUrl; delete media.dataset.sourceUrl;
        });
      };
      detail.addEventListener('toggle',loadSources); loadSources();
      if (item.defaced_url) {
        const link=node('a','查看打码视频','secondary');link.href=item.defaced_url;link.target='_blank';link.rel='noopener';detail.append(link);
      }
      next.append(detail);
      if (card) card.replaceWith(next); list.append(next);
    }
    document.getElementById('runs-status').textContent = runs.size ? '任务独立执行，刷新页面后仍可查看进度和结果。' : '还没有生成任务。提交后会在这里显示进度。';
  }
  async function refreshRuns() {
    try { const data=await api('/runs'); runs.clear(); (data.items || []).forEach(item=>runs.set(item.id,item)); renderRuns(); }
    catch (error) { document.getElementById('runs-status').textContent='任务读取失败：'+error.message; }
  }
  document.getElementById('runs-refresh').addEventListener('click',refreshRuns);
  async function initialize() {
    lock(true); draftControls(true);
    try {
      const [data] = await Promise.all([api('/drafts'),templatesReady,modelsReady,window.portraitPeople?.ready]);
      (data.items || []).forEach(item=>drafts.set(item.id,item));
      const requested = localStorage.getItem(currentKey);
      const selected = drafts.get(requested) || data.items?.[0];
      const item = selected ? await api('/drafts/'+encodeURIComponent(selected.id)) : await api('/drafts','POST',{});
      await restoreDraft(item); sessionReady=true;
      if ((item.person_id || null) !== (window.portraitPeople?.selected || null)) changed();
      if (pendingSubmission) status.textContent='上次提交结果尚未确认，点击确认可安全恢复。';
    } catch (error) { showSave('恢复失败：'+error.message,true); }
    finally { draftControls(false); lock(false); }
  }
  initialize(); refreshRuns(); setInterval(() => { if (!document.hidden) refreshRuns(); },5000);

})();
