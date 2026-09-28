/* Official AIGC library: creation is distinct from liveness certification. */
(() => {
  async function request(path, method = 'GET', body) {
    const response = await fetch('/api/portrait/' + path, {method,
      headers: body === undefined ? {} : {'Content-Type':'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '虚拟库操作未完成');
    return data;
  }
  const admin = document.getElementById('section-people');
  const personal = Boolean(admin?.dataset.personalLibrary);
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
    refresh().catch(error => { status.textContent = error.message; });
  }
  if (!admin) return;
  let people = [], libraryType = 'AIGC';
  window.portraitPeople = {
    get items() { return people; }, selected:null,
    async refresh() {
      people = ((await request('people')).items || []).sort((a,b) => Number(b.photo_count > 0)-Number(a.photo_count > 0));
      const real = people.filter(p => p.person_type === 'LivenessFace');
      const virtual = people.filter(p => p.person_type === 'AIGC');
      admin.querySelector('[data-library-counts]').textContent = `真人 ${real.length} 位 · 虚拟人物 ${virtual.length} 位 · 可用照片 ${people.reduce((n,p) => n + p.photo_count,0)} 张`;
    }
  };
  window.portraitPeople.refresh().catch(() => { admin.querySelector('[data-library-counts]').textContent = '人物读取失败，请打开管理重试。'; });
  window.addEventListener('portrait-verified',() => refresh().catch(error => { status.textContent = error.message; }));
  const open = document.getElementById('manage-people');
  const dialog = document.createElement('dialog'); dialog.className = 'portrait-dialog virtual-library-dialog';
  dialog.setAttribute('aria-labelledby','virtual-library-title');
  dialog.innerHTML = `<div class="portrait-heading"><h2 id="virtual-library-title">人物与照片</h2><button type="button" data-close aria-label="关闭人物与照片">×</button></div>
    <p class="virtual-intro">导入或添加照片后，回制作页按名字选择。移除只影响本工作台。</p>
    <div class="person-library-tabs" role="group" aria-label="管理人物类型"><button type="button" data-library-type="AIGC" aria-pressed="true">虚拟人物</button><button type="button" data-library-type="LivenessFace" aria-pressed="false">真人</button></div>
    <div class="virtual-toolbar"><button type="button" class="primary" data-new aria-expanded="false" aria-controls="virtual-create-panel">＋ 新增虚拟人物</button><button type="button" class="secondary" data-show-import>从云端导入</button></div>
    <form id="virtual-create-panel" data-create class="virtual-create" hidden><label for="virtual-person-name">新人物叫什么？</label><input id="virtual-person-name" name="name" maxlength="60" required placeholder="例如：短发女模特"><div class="virtual-form-actions"><button class="primary" type="submit">新增</button><button type="button" class="secondary" data-cancel-create>取消</button></div></form>
    <p data-status role="status" aria-live="polite"></p>
    <div data-people class="virtual-people"></div><div data-pending class="portrait-small"></div>
    <details class="virtual-disclosure" data-more><summary>从云端导入已有素材</summary>
      <p>按照片编号逐张导入，不需要读取整个云端列表。类型必须与上方所选分类一致。</p>
      <button type="button" class="secondary" data-assets>查看云端可用照片</button><div data-asset-list class="virtual-people"></div>
      <form data-import class="virtual-create"><label for="virtual-asset-id">照片编号（Asset ID）</label><input id="virtual-asset-id" name="asset" required maxlength="120" placeholder="asset-…"><label for="import-person-name">人物名称（可选）</label><input id="import-person-name" name="name" maxlength="60" placeholder="例如：yoyo-真人头像"><button type="submit" class="secondary">核实并导入</button></form>
      <details class="virtual-disclosure"><summary>只找回人物名单</summary><p>读取当前账号、项目已有的素材组。不会上传或导入照片，名单也可能含商品素材。</p><button type="button" class="secondary" data-sync>读取云端人物列表</button></details>
    </details>
    <details class="virtual-disclosure" data-recycle><summary>已移除人物 <span data-removed-count></span></summary><p>这里只从工作台列表移除，云端素材与历史任务保留。恢复后可继续使用。</p><div data-removed-list class="virtual-people"></div></details>
    <details class="virtual-disclosure virtual-help"><summary>使用帮助与连接信息</summary>
      <p>虚拟人物可在这里新增；真人需先完成官方授权，或导入已有授权照片。</p>
      <p>Asset ID 是单张云端照片的编号，仅按编号导入时需要。人物组编号由系统处理，日常无需填写。</p>
      <p>“照片可用”表示官方状态为 Active；实际生成权限、真人授权范围仍以官方校验为准。</p>
      <p data-project></p>
    </details>`;
  document.body.append(dialog);
  if (personal) {
    dialog.querySelector('[data-show-import]').hidden=true;
    dialog.querySelector('[data-more]').hidden=true;
  }
  const status = dialog.querySelector('[data-status]'), createForm = dialog.querySelector('[data-create]');
  let working = false, createRequest = null, pollTimer = null, hasPending = false, watchedPhoto = null;
  function action(label, name, handler, className = 'secondary') {
    const button = document.createElement('button'); button.type = 'button'; button.className = className;
    button.textContent = label; button.dataset.action = name; button.disabled = working;
    button.addEventListener('click',handler); return button;
  }
  function renderPeople() {
    const list = dialog.querySelector('[data-people]'); list.replaceChildren(); hasPending = false;
    const people = window.portraitPeople.items.filter(person => person.person_type === libraryType);
    for (const person of people) {
      const row = document.createElement('div'); row.className = 'virtual-person'; row.dataset.personId = person.id;
      const main = document.createElement('div'); main.className = 'virtual-person-main';
      if (person.thumbnail_url) { const image = document.createElement('img'); image.src = person.thumbnail_url; image.alt = ''; image.loading = 'lazy'; main.append(image); }
      const text = document.createElement('div'); text.className = 'virtual-person-info';
      const name = document.createElement('strong'); name.textContent = person.name;
      const counts = person.photo_counts || {};
      const pending = ['queued','uploading','submitting','processing','uncertain'].reduce((n,key) => n + (counts[key] || 0),0);
      hasPending ||= pending > 0;
      const state = document.createElement('small'); state.className = person.photo_count ? 'virtual-ready' : '';
      state.textContent = [person.photo_count ? `${person.photo_count} 张照片可用` : pending ? '' : '还没有可用照片', pending ? `${pending} 张正在检查` : '', counts.failed ? `${counts.failed} 张检查失败，可重新添加` : ''].filter(Boolean).join(' · ');
      text.append(name,state); main.append(text); row.append(main);
      const actions = document.createElement('div'); actions.className = 'virtual-person-actions';
      const upload = document.createElement('input'); upload.type = 'file'; upload.accept = '.png,.jpg,.jpeg,.webp'; upload.hidden = true;
      upload.setAttribute('aria-label','给'+person.name+'添加照片'); upload.dataset.photoUpload = '';
      upload.addEventListener('change',() => {
        const file = upload.files[0]; if (!file) return;
        run(async () => {
          status.textContent = '正在为“'+person.name+'”上传照片…';
          const body = new FormData(); body.append('file',file); body.append('kind','face');
          const response = await fetch('/api/production/assets',{method:'POST',body});
          const asset = await response.json();
          if (!response.ok) throw new Error(typeof asset.detail === 'string' ? asset.detail : '照片上传失败，请重试。');
          let job = await request('photos','POST',{person_id:person.id,asset_id:asset.id});
          if (job.status === 'failed') job = await request('photos/'+job.id+'/retry','POST',{});
          watchedPhoto = ['active','failed'].includes(job.status) ? null : {id:job.id,name:person.name};
          await refresh();
          status.textContent = job.status === 'active' ? '这张照片已可用，返回制作页按名称选择。' : job.status === 'uncertain' ? job.message : '照片已添加，正在检查。通过后可在制作页选用。';
        }); upload.value = '';
      });
      const addPhoto = action('添加照片','upload',() => upload.click());
      const editor = document.createElement('form'); editor.className = 'virtual-row-editor'; editor.dataset.editor = ''; editor.hidden = true;
      const nameLabel = document.createElement('label'); nameLabel.textContent = '人物名称';
      const nameInput = document.createElement('input'); nameInput.name = 'name'; nameInput.value = person.name; nameInput.required = true; nameInput.maxLength = 60; nameLabel.append(nameInput);
      const save = document.createElement('button'); save.type = 'submit'; save.className = 'primary'; save.textContent = '保存'; save.dataset.action = 'save-name';
      editor.append(nameLabel,save,action('取消','cancel-name',() => { editor.hidden = true; }));
      editor.addEventListener('submit',event => { event.preventDefault(); run(async () => {
        await request('people/'+person.id,'PUT',{name:nameInput.value.trim()}); await refresh(); status.textContent = '人物名称已保存。';
      }); });
      const confirmation = document.createElement('div'); confirmation.className = 'virtual-row-editor'; confirmation.dataset.editor = ''; confirmation.hidden = true;
      const warning = document.createElement('p'); warning.textContent = '从工作台移除“'+person.name+'”？云端素材与历史任务会保留，可在下方“已移除人物”中恢复。';
      confirmation.append(warning,action('确认移除','confirm-remove',() => run(async () => {
        await request('people/'+person.id,'DELETE');
        if (window.portraitPeople.selected === person.id) window.portraitPeople.choose(null);
        await refresh(); status.textContent = '已从工作台移除，可在“已移除人物”中恢复。';
      }),'virtual-danger'),action('取消','cancel-remove',() => { confirmation.hidden = true; }));
      actions.append(addPhoto,action('改名','rename',() => { confirmation.hidden = true; editor.hidden = false; nameInput.focus(); nameInput.select(); }),action('移除','remove',() => { editor.hidden = true; confirmation.hidden = false; }));
      row.append(actions,upload,editor,confirmation); list.append(row);
    }
    if (!people.length) { const empty = document.createElement('p'); empty.className = 'virtual-empty'; empty.textContent = '此分类暂无人物。已有官方照片请点击“从云端导入”。'; list.append(empty); }
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!dialog.open || !hasPending) return;
    pollTimer = setTimeout(async () => {
      if (!working && !dialog.querySelector('[data-editor]:not([hidden])') && createForm.hidden) {
        try { await refresh(); } catch (_) { /* Preserve the last list on transient read failures. */ }
      }
      schedulePoll();
    },5000);
  }
  async function refresh() {
    const [,data,removed,photoState] = await Promise.all([window.portraitPeople.refresh(),request('virtual/status'),request('people?removed=true'),watchedPhoto ? request('photos?ids='+encodeURIComponent(watchedPhoto.id)) : Promise.resolve(null)]);
    renderPeople();
    const completedPhoto = photoState?.items?.[0];
    if (watchedPhoto && completedPhoto && ['active','failed'].includes(completedPhoto.status)) {
      status.textContent = completedPhoto.status === 'active' ? '“'+watchedPhoto.name+'”的照片已通过检查，可在制作页选用。' : completedPhoto.message;
      status.dataset.error = String(completedPhoto.status === 'failed'); watchedPhoto = null;
    }
    dialog.querySelector('[data-project]').textContent = data.ready ? `当前素材项目：${data.project_name}` : '尚未配置素材库连接，请先在后台配置。';
    dialog.querySelector('[data-pending]').textContent = data.requests.filter(item => item.status !== 'ready').map(item => item.message).join('；');
    const list = dialog.querySelector('[data-removed-list]'); list.replaceChildren();
    const items = removed.items.filter(person => person.person_type === libraryType);
    dialog.querySelector('[data-removed-count]').textContent = items.length ? `（${items.length}）` : '';
    for (const person of items) {
      const row = document.createElement('div'); row.className = 'virtual-removed-row';
      const label = document.createElement('span'); label.textContent = person.name;
      row.append(label,action('恢复','restore',() => run(async () => { await request('people/'+person.id+'/restore','POST',{}); await refresh(); status.textContent = '人物已恢复，可以继续使用。'; })));
      list.append(row);
    }
    if (!items.length) list.textContent = '没有已移除的人物。';
    schedulePoll();
  }
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
  dialog.addEventListener('close',() => clearTimeout(pollTimer));
  dialog.querySelector('[data-new]').addEventListener('click',() => showCreate(createForm.hidden));
  dialog.querySelector('[data-cancel-create]').addEventListener('click',() => showCreate(false));
  dialog.querySelector('[data-show-import]').addEventListener('click',() => { const details = dialog.querySelector('[data-more]'); details.open = true; details.querySelector('summary').focus(); details.scrollIntoView({block:'nearest'}); });
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
  dialog.querySelector('[data-sync]').addEventListener('click',() => run(async () => {
    status.textContent = '正在读取云端人物列表…';
    await request('people/sync','POST',{person_type:libraryType}); await refresh(); status.textContent = '人物列表已更新。已有照片请从云端导入；新照片请点击人物旁的“添加照片”。已移除人物需手动恢复。';
  }));
  async function importAsset(id, name = '') {
    const asset = await request('import','POST',{remote_asset_id:id,person_type:libraryType});
    const person = await request('people/resolve','POST',{group_id:asset.portrait.group_id,person_type:libraryType});
    if (name) await request('people/'+person.id,'PUT',{name});
    await request('people/'+person.id+'/restore','POST',{});
    await refresh(); status.textContent = '照片已导入。返回制作页，按人物名称选择即可使用。';
  }
  dialog.querySelectorAll('[data-library-type]').forEach(button => button.addEventListener('click',() => {
    libraryType = button.dataset.libraryType; showCreate(false);
    dialog.querySelector('[data-new]').hidden = libraryType !== 'AIGC';
    dialog.querySelectorAll('[data-library-type]').forEach(tab => tab.setAttribute('aria-pressed',String(tab === button)));
    dialog.querySelector('[data-asset-list]').replaceChildren(); status.textContent = '';
    dialog.querySelector('[data-import]').reset(); run(async () => { await refresh(); status.textContent = ''; });
  }));
  dialog.querySelector('[data-import]').addEventListener('submit',event => {
    event.preventDefault(); const form = event.currentTarget;
    run(() => importAsset(form.elements.asset.value.trim(),form.elements.name.value.trim()));
  });
  dialog.querySelector('[data-assets]').addEventListener('click',() => run(async () => {
    status.textContent = '正在读取云端可用照片…';
    const data = await request('assets?person_type='+libraryType); const list = dialog.querySelector('[data-asset-list]'); list.replaceChildren();
    for (const asset of data.items) {
      const button = action(`${asset.name || asset.id} · 导入`,'import-asset',() => run(() => importAsset(asset.id)),'person-option'); list.append(button);
    }
    status.textContent = data.items.length ? '选择照片后会核实并保存到人物库。' : '没有找到可用照片，请检查账号、素材项目或照片状态。';
  }));
})();
