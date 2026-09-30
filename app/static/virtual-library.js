/* Official AIGC library: creation is distinct from liveness certification. */
(() => {
  const reads = new Set();
  async function request(path, method = 'GET', body) {
    const controller = method === 'GET' ? new AbortController() : null;
    const timeout = controller ? setTimeout(() => controller.abort(), 8000) : null;
    if (controller) reads.add(controller);
    try {
    const response = await fetch('/api/portrait/' + path, {method,
      signal:controller?.signal,
      headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '虚拟库操作未完成');
    return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('人物读取超时，请重试。');
      throw error;
    } finally { clearTimeout(timeout); if (controller) reads.delete(controller); }
  }
  window.addEventListener('pagehide', () => { reads.forEach(controller => controller.abort()); reads.clear(); });
  const admin = document.getElementById('section-people');
  const personal = Boolean(admin?.dataset.personalLibrary);
  function whenPeopleVisible(action) {
    let started = false;
    const start = () => { if (!started) { started = true; action(); } };
    if (personal || (admin && !admin.hidden)) start();
    else window.addEventListener('admin-section-visible', event => { if (event.detail.section === 'people') start(); });
  }
  if (admin && !personal) {
    const card = document.createElement('div'); card.className = 'card';
    card.innerHTML = `<h3>素材库连接</h3>
      <p>真人与虚拟人物共用此连接。项目名称须与官方素材所属项目一致。</p>
      <p data-status role="status">读取配置中…</p>
      <details><summary>人物库连接设置</summary>
      <form data-config><label>项目名称<input name="project_name" maxlength="128" required autocomplete="off"></label>
      <label><input name="use_storage_credentials" type="checkbox">使用对象存储中已保存的 AK/SK</label>
      <label>Access Key<input name="access_key" type="password" autocomplete="new-password" placeholder="留空保留现有值"></label>
      <label>Secret Key<input name="secret_key" type="password" autocomplete="new-password" placeholder="留空保留现有值"></label>
      <button type="submit" class="primary">保存人物库连接</button></form></details>`;
    admin.append(card);
    const status = card.querySelector('[data-status]'), form = card.querySelector('form');
    async function refresh() {
      const [data,config] = await Promise.all([request('virtual/status'),request('config')]);
      status.textContent = `当前项目：${data.project_name} · ${data.ready ? '连接配置已保存' : '尚未配置密钥'}`;
      form.elements.project_name.value = config.project_name;
      form.elements.use_storage_credentials.checked = config.use_storage_credentials;
    }
    form.addEventListener('submit',async event => {
      event.preventDefault(); const button = form.querySelector('button'); button.disabled = true;
      try {
        await request('config','PUT',{project_name:form.elements.project_name.value.trim(),
          use_storage_credentials:form.elements.use_storage_credentials.checked,
          access_key:form.elements.access_key.value.trim(),secret_key:form.elements.secret_key.value.trim()});
        form.elements.access_key.value = ''; form.elements.secret_key.value = ''; await refresh();
      } catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
    });
    whenPeopleVisible(() => refresh().catch(error => { status.textContent = error.message; }));
  }
  if (!admin) return;
  let people = [], libraryType = 'AIGC';
  window.portraitPeople = {
    get items() { return people; }, selected:null,
    async refresh() {
      people = ((await request('people')).items || []).sort((a,b) => Number(b.photo_count > 0 || b.video_count > 0)-Number(a.photo_count > 0 || a.video_count > 0));
      const real = people.filter(p => p.person_type === 'LivenessFace');
      const virtual = people.filter(p => p.person_type === 'AIGC');
      admin.querySelector('[data-library-counts]').textContent = `真人 ${real.length} 位 · 虚拟人物 ${virtual.length} 位 · 可用照片 ${people.reduce((n,p) => n + (p.photo_count || 0),0)} 张 · 可用视频 ${people.reduce((n,p) => n + (p.video_count || 0),0)} 段`;
    }
  };
  const refreshCounts = () => window.portraitPeople.refresh().catch(() => { admin.querySelector('[data-library-counts]').textContent = '人物读取失败，请打开管理重试。'; });
  whenPeopleVisible(refreshCounts);
  window.addEventListener('portrait-verified', refreshCounts);
  const open = document.getElementById('manage-people');
  const dialog = document.createElement('dialog'); dialog.className = 'portrait-dialog virtual-library-dialog';
  dialog.setAttribute('aria-labelledby','virtual-library-title');
  dialog.innerHTML = `<div class="portrait-heading"><h2 id="virtual-library-title">人物素材库</h2><button type="button" data-close aria-label="关闭人物素材库">×</button></div>
    <p class="virtual-intro">添加照片或视频后，回制作页按名字选择。真人视频需添加到已授权的人物。</p>
    <p class="portrait-small">照片支持批量添加：电脑按住 Ctrl 或 Shift 多选，手机可在相册中选择多张。上传后需等待检查通过。</p>
    <p class="portrait-small library-video-requirements">人物视频：MP4 / MOV，2–30 秒，最大 50 MB，24–60 fps，建议 720p 或 1080p。上传后需等待检查通过。</p>
    <div class="person-library-tabs" role="group" aria-label="管理人物类型"><button type="button" data-library-type="AIGC" aria-pressed="true">虚拟人物</button><button type="button" data-library-type="LivenessFace" aria-pressed="false">真人</button></div>
    <div class="virtual-toolbar"><button type="button" class="primary" data-new aria-expanded="false" aria-controls="virtual-create-panel">＋ 新增虚拟人物</button></div>
    <form id="virtual-create-panel" data-create class="virtual-create" hidden><label for="virtual-person-name">新人物叫什么？</label><input id="virtual-person-name" name="name" maxlength="60" required placeholder="例如：短发女模特"><div class="virtual-form-actions"><button class="primary" type="submit">新增</button><button type="button" class="secondary" data-cancel-create>取消</button></div></form>
    <p data-status role="status" aria-live="polite"></p>
    <div data-people class="virtual-people"></div>
    <section data-person-detail class="library-person-detail" aria-labelledby="library-detail-name" hidden>
      <button type="button" class="secondary" data-detail-back>← 返回人物列表</button>
      <div class="library-detail-heading"><div><h3 id="library-detail-name" data-detail-name></h3><span data-detail-type class="library-type-badge"></span></div><div data-detail-actions></div></div>
      <p data-detail-status role="status" aria-live="polite"></p>
      <div data-detail-photos class="library-photo-grid"></div>
      <div data-delete-confirmation class="library-delete-confirmation" role="group" aria-label="删除确认" hidden><p data-delete-warning></p><p data-delete-error role="alert"></p><div class="virtual-form-actions"><button type="button" class="virtual-danger" data-action="confirm-delete">确认删除</button><button type="button" class="secondary" data-action="cancel-delete">取消</button></div></div>
    </section><div data-pending class="portrait-small"></div>
    <details class="virtual-disclosure" data-recycle><summary>已移除人物 <span data-removed-count></span></summary><p>这里只从工作台列表移除，云端素材与历史任务保留。恢复后可继续使用。</p><div data-removed-list class="virtual-people"></div></details>
    <details class="virtual-disclosure virtual-help"><summary>使用帮助与连接信息</summary>
      <p>虚拟人物可在这里新增；真人需先完成官方授权。添加视频后，检查通过即可在制作台切换“人物视频”并选用。</p>
      <p>“照片可用”表示官方状态为 Active；实际生成权限、真人授权范围仍以官方校验为准。</p>
      <p data-project></p>
    </details>`;
  document.body.append(dialog);
  const preview = document.createElement('dialog'); preview.className = 'portrait-dialog library-photo-preview';
  preview.dataset.libraryPreview = ''; preview.setAttribute('aria-label','查看人物照片或视频');
  preview.innerHTML = '<div class="portrait-heading"><h2 data-preview-name></h2><button type="button" data-preview-close aria-label="关闭照片预览">×</button></div><div data-preview-media></div>';
  document.body.append(preview);
  preview.querySelector('[data-preview-close]').addEventListener('click',() => preview.close());
  preview.addEventListener('close',() => { preview.querySelectorAll('video').forEach(video => { video.pause(); video.removeAttribute('src'); video.load(); }); preview.querySelector('[data-preview-media]').replaceChildren(); });
  const status = dialog.querySelector('[data-status]'), createForm = dialog.querySelector('[data-create]');
  let working = false, createRequest = null, pollTimer = null, hasPending = false, watchedPhoto = null;
  const detail = dialog.querySelector('[data-person-detail]');
  const confirmation = detail.querySelector('[data-delete-confirmation]');
  let detailPerson = null, detailVersion = 0, deleteTarget = null, removedLoaded = false, removedItems = [];
  function canManage(item, person = item) {
    if (item.can_manage !== undefined) return item.can_manage === true;
    if (person.can_manage !== undefined) return person.can_manage === true;
    return !window.currentAccount || window.currentAccount.role === 'admin';
  }
  function personType(person) { return person.person_type === 'LivenessFace' ? '真人' : '虚拟人物'; }
  function action(label, name, handler, className = 'secondary') {
    const button = document.createElement('button'); button.type = 'button'; button.className = className;
    button.textContent = label; button.dataset.action = name; button.disabled = working;
    button.addEventListener('click',event => { event.stopPropagation(); handler(event); }); return button;
  }
  function uploadControl(person, kind) {
    const video = kind === 'person_video', label = video ? '视频' : '照片';
    const input = document.createElement('input'); input.type = 'file'; input.hidden = true;
    input.accept = video ? '.mp4,.mov' : '.png,.jpg,.jpeg,.webp';
    input.multiple = !video;
    input.setAttribute('aria-label','给'+person.name+'添加'+label);
    input.dataset[video ? 'videoUpload' : 'photoUpload'] = '';
    input.addEventListener('change',() => {
      const files = Array.from(input.files || []); input.value = ''; if (!files.length) return;
      run(async () => {
        if (!canManage(person)) throw new Error('你没有管理此人物素材的权限。');
        watchedPhoto = null;
        if (!video && files.length > 1) {
          let added = 0, active = 0;
          const failures = [];
          for (const [index,file] of files.entries()) {
            status.textContent = `正在为“${person.name}”上传照片 ${index + 1}/${files.length}：${file.name}`;
            try {
              const job = await uploadFile(person,kind,file,label);
              if (job.status === 'failed') throw new Error(job.message || '照片检查失败，请重试。');
              added++; if (job.status === 'active') active++;
            } catch (error) { failures.push(`${file.name}：${error.message || '上传失败，请重试。'}`); }
          }
          let refreshError = '';
          try { await refresh(); } catch (_) { refreshError = '\n人物列表刷新失败，请重新打开管理查看已提交的照片。'; }
          status.dataset.error = String(failures.length > 0);
          status.textContent = `批量上传完成：已添加 ${added} 张，失败 ${failures.length} 张。`
            + (added ? `其中 ${active} 张已可用，${added - active} 张等待检查。` : '')
            + (failures.length ? '\n以下照片未完成，请根据原因重新添加或在人物详情中重试检查：\n' + failures.join('\n') : '')
            + refreshError;
          return;
        }
        const file = files[0];
        if (video && !/\.(mp4|mov)$/i.test(file.name)) throw new Error('人物视频请使用 MP4 或 MOV 格式。');
        if (video && (!file.size || file.size > 50*1024*1024)) throw new Error('人物视频需大于 0、小于等于 50 MB。');
        status.textContent = '正在为“'+person.name+'”上传'+label+'…';
        const job = await uploadFile(person,kind,file,label);
        watchedPhoto = ['active','failed'].includes(job.status) ? null : {id:job.id,name:person.name,label};
        await refresh();
        if (job.status === 'failed') throw new Error(job.message || label+'检查失败，请重试。');
        status.textContent = job.status === 'active' ? label+'已可用，返回制作页按名称选择。' : job.status === 'uncertain' ? job.message : label+'已添加，正在检查。通过后可在制作页选用。';
      });
    });
    return {input,button:action('添加'+label,video ? 'upload-video' : 'upload',() => input.click())};
  }
  async function uploadFile(person,kind,file,label) {
    const body = new FormData(); body.append('file',file); body.append('kind',kind);
    const response = await fetch('/api/production/assets',{method:'POST',body});
    const asset = await response.json();
    if (!response.ok) throw new Error(typeof asset.detail === 'string' ? asset.detail : label+'上传失败，请重试。');
    let job = await request('photos','POST',{person_id:person.id,asset_id:asset.id});
    if (job.status === 'failed') job = await request('photos/'+job.id+'/retry','POST',{});
    return job;
  }
  function renderPeople() {
    const list = dialog.querySelector('[data-people]'); list.replaceChildren(); hasPending = false;
    const people = window.portraitPeople.items.filter(person => person.person_type === libraryType);
    for (const person of people) {
      const row = document.createElement('div'); row.className = 'virtual-person'; row.dataset.personId = person.id;
      row.tabIndex = 0; row.setAttribute('role','button'); row.setAttribute('aria-label','查看“'+person.name+'”的照片、视频与状态');
      row.addEventListener('click',event => { if (!event.target.closest('button,input,form,[data-editor]')) showDetail(person); });
      row.addEventListener('keydown',event => { if (event.target === row && ['Enter',' '].includes(event.key)) { event.preventDefault(); showDetail(person); } });
      const main = document.createElement('div'); main.className = 'virtual-person-main';
      if (person.thumbnail_url) { const image = document.createElement('img'); image.src = person.thumbnail_url; image.alt = ''; image.loading = 'lazy'; main.append(image); }
      const text = document.createElement('div'); text.className = 'virtual-person-info';
      const name = document.createElement('strong'); name.textContent = person.name;
      const badge = document.createElement('span'); badge.className = 'library-type-badge'; badge.textContent = personType(person)+(person.shared ? ' · 共享' : '');
      const counts = person.photo_counts || {};
      const pending = ['queued','uploading','submitting','processing','uncertain'].reduce((n,key) => n + (counts[key] || 0),0);
      hasPending ||= pending > 0;
      const ready = person.photo_count > 0 || person.video_count > 0;
      const state = document.createElement('small'); state.className = ready ? 'virtual-ready' : '';
      state.textContent = [person.photo_count ? `${person.photo_count} 张照片可用` : '', person.video_count ? `${person.video_count} 段视频可用` : '', !ready && !pending ? '还没有可用素材' : '', pending ? `${pending} 项正在检查` : '', counts.failed ? `${counts.failed} 项检查失败，可重新添加` : ''].filter(Boolean).join(' · ');
      text.append(name,badge,state); main.append(text); row.append(main);
      const actions = document.createElement('div'); actions.className = 'virtual-person-actions';
      const photoUpload = uploadControl(person,'face'), videoUpload = uploadControl(person,'person_video');
      const editor = document.createElement('form'); editor.className = 'virtual-row-editor'; editor.dataset.editor = ''; editor.hidden = true;
      const nameLabel = document.createElement('label'); nameLabel.textContent = '人物名称';
      const nameInput = document.createElement('input'); nameInput.name = 'name'; nameInput.value = person.name; nameInput.required = true; nameInput.maxLength = 60; nameLabel.append(nameInput);
      const save = document.createElement('button'); save.type = 'submit'; save.className = 'primary'; save.textContent = '保存'; save.dataset.action = 'save-name';
      editor.append(nameLabel,save,action('取消','cancel-name',() => { editor.hidden = true; }));
      editor.addEventListener('submit',event => { event.preventDefault(); run(async () => {
        await request('people/'+person.id,'PUT',{name:nameInput.value.trim()}); await refresh(); status.textContent = '人物名称已保存。';
      }); });
      const confirmation = document.createElement('div'); confirmation.className = 'virtual-row-editor'; confirmation.dataset.editor = ''; confirmation.hidden = true;
      const warning = document.createElement('p'); warning.textContent = '从人物库删除“'+person.name+'”及其照片和视频？历史任务和云端原始素材保留，可在下方“已移除人物”中恢复。';
      confirmation.append(warning,action('确认移除','confirm-remove',() => run(async () => {
        await request('people/'+person.id,'DELETE');
        if (window.portraitPeople.selected === person.id) window.portraitPeople.choose?.(null);
        await refresh(); status.textContent = '已从工作台移除，可在“已移除人物”中恢复。';
      }),'virtual-danger'),action('取消','cancel-remove',() => { confirmation.hidden = true; }));
      actions.append(photoUpload.button,videoUpload.button,action('改名','rename',() => { confirmation.hidden = true; editor.hidden = false; nameInput.focus(); nameInput.select(); }),action('移除','remove',() => { editor.hidden = true; confirmation.hidden = false; }));
      if (canManage(person)) row.append(actions,photoUpload.input,videoUpload.input,editor,confirmation);
      list.append(row);
    }
    if (!people.length) { const empty = document.createElement('p'); empty.className = 'virtual-empty'; empty.textContent = '此分类暂无人物，请先新增人物或完成真人授权。'; list.append(empty); }
  }
  function hideDetail() {
    detailVersion++; detailPerson = null; deleteTarget = null; detail.hidden = true; confirmation.hidden = true;
    dialog.querySelector('[data-people]').hidden = false;
    dialog.querySelector('.virtual-toolbar').hidden = false;
  }
  function showDetail(person) {
    detailPerson = person; detail.hidden = false; confirmation.hidden = true; deleteTarget = null;
    dialog.querySelector('[data-people]').hidden = true;
    dialog.querySelector('.virtual-toolbar').hidden = true; showCreate(false);
    detail.querySelector('[data-detail-name]').textContent = person.name;
    detail.querySelector('[data-detail-type]').textContent = personType(person)+(person.shared ? ' · 共享人物库' : '');
    const actions = detail.querySelector('[data-detail-actions]'); actions.replaceChildren();
    const use = document.createElement('a'); use.href = '/'; use.dataset.detailUse = ''; use.className = 'secondary library-use-link'; use.textContent = '前往制作台选用'; actions.append(use);
    if (canManage(person)) {
      const photoUpload = uploadControl(person,'face'), videoUpload = uploadControl(person,'person_video');
      actions.append(photoUpload.button,videoUpload.button,photoUpload.input,videoUpload.input,action('删除整个人物','remove-person',() => askDelete({type:'person',id:person.id,name:person.name})));
    }
    detail.querySelector('[data-detail-back]').focus();
    loadDetail(person);
  }
  function safeMediaUrl(url) {
    if (!url) return null;
    try { const value = new URL(url,location.href); return ['http:','https:'].includes(value.protocol) ? value.href : null; } catch (_) { return null; }
  }
  function isVideo(photo) { return ['video','person_video'].includes(photo.kind); }
  function openPhoto(photo) {
    const url = safeMediaUrl(photo.url); if (!url) return;
    preview.querySelector('[data-preview-name]').textContent = photo.name || '人物照片';
    const video = isVideo(photo) || /\.mp4(?:\?|$)/i.test(url);
    const media = document.createElement(video ? 'video' : 'img');
    if (video) { media.controls = true; media.preload = 'none'; media.playsInline = true; } else media.alt = photo.name || '人物照片';
    media.src = url; preview.querySelector('[data-preview-media]').replaceChildren(media); preview.showModal();
  }
  async function loadDetail(person, background = false) {
    const version = ++detailVersion;
    const note = detail.querySelector('[data-detail-status]'), list = detail.querySelector('[data-detail-photos]');
    if (!background) { list.replaceChildren(); note.textContent = '正在读取照片、视频与状态…'; }
    try {
      const data = await request('people/'+encodeURIComponent(person.id)+'/photos');
      if (version !== detailVersion || detailPerson?.id !== person.id || detail.hidden) return;
      list.replaceChildren(); note.textContent = data.items?.length ? '' : '暂无照片或视频。'; note.dataset.error = 'false';
      for (const photo of data.items || []) {
        const row = document.createElement('article'); row.className = 'library-photo-card'; row.dataset.libraryPhotoId = photo.id;
        const thumb = safeMediaUrl(photo.thumbnail_url);
        if (thumb) { const image = document.createElement('img'); image.src = thumb; image.loading = 'lazy'; image.alt = ''; image.width = 180; image.height = 180; row.append(image); }
        else { const placeholder = document.createElement('div'); placeholder.className = 'library-photo-placeholder'; placeholder.textContent = isVideo(photo) ? '视频预览' : '暂无缩略图'; row.append(placeholder); }
        const name = document.createElement('strong'); name.textContent = photo.name || '人物照片';
        const state = document.createElement('span'); state.className = 'library-photo-state'; state.dataset.state = photo.status;
        state.textContent = photo.status === 'active' && isVideo(photo) ? '视频可用' : ({active:'照片可用',queued:'等待检查',uploading:'正在上传',submitting:'正在提交',processing:'正在检查',uncertain:'等待确认',failed:'检查失败'})[photo.status] || '状态待确认';
        const message = document.createElement('p'); message.className = 'library-photo-message'; message.textContent = photo.message || '';
        const actions = document.createElement('div'); actions.className = 'virtual-form-actions';
        if (safeMediaUrl(photo.url)) actions.append(action(isVideo(photo) ? '打开视频' : '查看原图','open-photo',() => openPhoto(photo)));
        if (canManage(photo,person)) {
          if (photo.status === 'failed') actions.append(action('重试检查','retry-photo',() => run(async () => { await request('photos/'+encodeURIComponent(photo.id)+'/retry','POST',{}); await refresh(); await loadDetail(person); })));
          actions.append(action(isVideo(photo) ? '删除这段视频' : '删除这张照片','remove-photo',() => askDelete({type:'photo',id:photo.id,name:photo.name || '人物素材',personId:person.id,label:isVideo(photo) ? '视频' : '照片'})));
        }
        row.append(name,state,message,actions); list.append(row);
      }
    } catch (error) {
      if (version !== detailVersion || detailPerson?.id !== person.id || detail.hidden) return;
      note.textContent = '人物素材读取失败：'+error.message; note.dataset.error = 'true';
      note.append(action('重新读取','reload-detail',() => loadDetail(person)));
    }
  }
  function askDelete(target) {
    deleteTarget = target; confirmation.hidden = false;
    confirmation.querySelector('[data-delete-warning]').textContent = target.type === 'person'
      ? '从人物库删除“'+target.name+'”及其所有照片和视频？历史任务和云端原始素材保留。'
      : '从“'+detailPerson.name+'”的人物库删除'+(target.label || '素材')+'“'+target.name+'”？历史任务和云端原始素材保留。';
    confirmation.querySelector('[data-delete-error]').textContent = '';
    confirmation.querySelector('[data-action=cancel-delete]').focus(); confirmation.scrollIntoView({block:'nearest'});
  }
  detail.querySelector('[data-detail-back]').addEventListener('click',hideDetail);
  confirmation.querySelector('[data-action=cancel-delete]').addEventListener('click',() => { confirmation.hidden = true; deleteTarget = null; });
  confirmation.querySelector('[data-action=confirm-delete]').addEventListener('click',() => {
    const target = deleteTarget, person = detailPerson; if (!target || !person) return;
    run(async () => {
      try {
        await request((target.type === 'person' ? 'people/' : 'photos/')+encodeURIComponent(target.id),'DELETE');
        confirmation.hidden = true; deleteTarget = null;
        if (target.type === 'person') { hideDetail(); if (window.portraitPeople.selected === target.id) window.portraitPeople.choose?.(null); }
        await refresh();
        if (target.type === 'photo' && detailPerson?.id === person.id) await loadDetail(person);
        status.textContent = target.type === 'person' ? '人物已从人物库删除，历史任务和云端原始素材保留。' : '所选'+(target.label || '素材')+'已从人物库删除。';
      } catch (error) { confirmation.querySelector('[data-delete-error]').textContent = error.message; throw error; }
    });
  });
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!dialog.open || !hasPending) return;
    pollTimer = setTimeout(async () => {
      if (!working && !dialog.querySelector('[data-editor]:not([hidden])') && confirmation.hidden && createForm.hidden) {
        try { await refresh(); } catch (_) { /* Preserve the last list on transient read failures. */ }
      }
      schedulePoll();
    },5000);
  }
  async function refresh() {
    await window.portraitPeople.refresh();
    renderPeople();
    removedLoaded = false;
    if (dialog.querySelector('[data-recycle]').open) loadRemoved();
    loadAuxiliary();
    if (detailPerson && confirmation.hidden) loadDetail(detailPerson,true);
    schedulePoll();
  }
  async function loadAuxiliary() {
    request('virtual/status').then(data => {
      dialog.querySelector('[data-project]').textContent = data.ready ? `当前素材项目：${data.project_name}` : '尚未配置素材库连接，请先在后台配置。';
      dialog.querySelector('[data-pending]').textContent = (data.requests || []).filter(item => item.status !== 'ready').map(item => item.message).join('；');
    }).catch(error => { dialog.querySelector('[data-project]').textContent = '连接信息暂时不可用：'+error.message; });
    if (!watchedPhoto) return;
    const watching = watchedPhoto;
    let photoState;
    try { photoState = await request('photos?ids='+encodeURIComponent(watching.id)); } catch (_) { return; }
    const completedPhoto = photoState?.items?.[0];
    if (watchedPhoto === watching && completedPhoto && ['active','failed'].includes(completedPhoto.status)) {
      status.textContent = completedPhoto.status === 'active' ? '“'+watchedPhoto.name+'”的'+(watchedPhoto.label || '照片')+'已通过检查，可在制作页选用。' : completedPhoto.message;
      status.dataset.error = String(completedPhoto.status === 'failed'); watchedPhoto = null;
    }
  }
  async function loadRemoved() {
    const list = dialog.querySelector('[data-removed-list]');
    if (!removedLoaded) {
      list.textContent = '正在读取已移除人物…';
      try { removedItems = (await request('people?removed=true')).items || []; removedLoaded = true; }
      catch (error) { list.textContent = '已移除人物读取失败：'+error.message; list.append(action('重试','retry-removed',loadRemoved)); return; }
    }
    list.replaceChildren();
    const items = removedItems.filter(person => person.person_type === libraryType);
    dialog.querySelector('[data-removed-count]').textContent = items.length ? `（${items.length}）` : '';
    for (const person of items) {
      const row = document.createElement('div'); row.className = 'virtual-removed-row';
      const label = document.createElement('span'); label.textContent = person.name;
      row.append(label);
      if (canManage(person)) row.append(action('恢复','restore',() => run(async () => { await request('people/'+person.id+'/restore','POST',{}); await refresh(); status.textContent = '人物已恢复，可以继续使用。'; })));
      list.append(row);
    }
    if (!items.length) list.textContent = '没有已移除的人物。';
  }
  dialog.querySelector('[data-recycle]').addEventListener('toggle',() => { if (dialog.querySelector('[data-recycle]').open) loadRemoved(); });
  async function run(operation) {
    if (working) return; working = true;
    dialog.querySelectorAll('button:not([data-close])').forEach(button => { button.disabled = true; });
    status.textContent = '正在处理…'; status.dataset.error = 'false';
    try { await operation(); } catch (error) { status.textContent = error.message; status.dataset.error = 'true'; }
    finally {
      working = false;
      dialog.querySelectorAll('button').forEach(button => { button.disabled = button.dataset.needsPhoto === 'true'; });
      schedulePoll();
    }
  }
  function showCreate(show) {
    createForm.hidden = !show; dialog.querySelector('[data-new]').setAttribute('aria-expanded',String(show));
    if (show) createForm.elements.name.focus();
  }
  open.addEventListener('click',() => { dialog.showModal(); run(async () => { await refresh(); status.textContent = ''; }); });
  dialog.querySelector('[data-close]').addEventListener('click',() => dialog.close());
  dialog.addEventListener('close',() => { clearTimeout(pollTimer); hideDetail(); if (preview.open) preview.close(); });
  dialog.querySelector('[data-new]').addEventListener('click',() => showCreate(createForm.hidden));
  dialog.querySelector('[data-cancel-create]').addEventListener('click',() => showCreate(false));
  createForm.addEventListener('submit',event => {
    event.preventDefault();
    run(async () => {
      const name = createForm.elements.name.value.trim(); if (!name) throw new Error('请填写人物名称。');
      if (!createRequest || createRequest.name !== name) createRequest = {name,request_id:crypto.randomUUID()};
      const data = await request('people','POST',{...createRequest,person_type:'AIGC'});
      await refresh(); status.textContent = data.message;
      if (data.status === 'ready') { createForm.reset(); createRequest = null; showCreate(false); status.textContent = '人物已新增。点击它旁边的“添加照片”即可入库。'; }
    });
  });
  dialog.querySelectorAll('[data-library-type]').forEach(button => button.addEventListener('click',() => {
    libraryType = button.dataset.libraryType; hideDetail(); showCreate(false);
    dialog.querySelector('[data-new]').hidden = libraryType !== 'AIGC';
    dialog.querySelectorAll('[data-library-type]').forEach(tab => tab.setAttribute('aria-pressed',String(tab === button)));
    status.textContent = ''; run(async () => { await refresh(); status.textContent = ''; });
  }));
})();
