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
  const admin = document.getElementById('section-overview');
  if (admin) {
    const card = document.createElement('div'); card.className = 'card';
    card.innerHTML = `<h3>官方人物库 · AIGC 虚拟人物</h3>
      <p>与真人库共用当前项目 AK/SK。虚拟人物无需本人活体认证；上传照片仍需通过官方检查。</p>
      <p data-status role="status">读取配置中…</p><p data-counts></p><p data-history></p><p data-failures></p><button type="button" class="secondary" data-test>只读检查 AIGC 权限</button>
      <details><summary>人物库连接设置</summary>
      <form data-config><label>项目名称<input name="project_name" maxlength="128" required autocomplete="off"></label>
      <label><input name="use_storage_credentials" type="checkbox">明确使用已保存的 TOS AK/SK</label>
      <label>Access Key<input name="access_key" type="password" autocomplete="new-password" placeholder="留空保留现有值"></label>
      <label>Secret Key<input name="secret_key" type="password" autocomplete="new-password" placeholder="留空保留现有值"></label>
      <button type="submit" class="primary">保存人物库连接</button></form></details>`;
    admin.append(card);
    const status = card.querySelector('[data-status]'), form = card.querySelector('form');
    async function refresh() {
      const [data,config] = await Promise.all([request('virtual/status'),request('config')]);
      status.textContent = `项目 ${data.project_name} · AK/SK ${data.ready ? '已配置' : '未配置'} · TOS ${data.tos_ready ? '已配置' : '未配置'}。权限尚需只读检查；创建、生成权限以官方实际响应为准。`;
      const counts = data.photo_counts || {};
      card.querySelector('[data-counts]').textContent = `本地 AIGC 素材组 ${data.people_count} · 可用照片 ${counts.active || 0} · 处理中 ${(counts.queued || 0) + (counts.uploading || 0) + (counts.submitting || 0) + (counts.processing || 0)} · 失败 ${counts.failed || 0} · 结果待确认 ${counts.uncertain || 0}`;
      const last = data.last_success;
      card.querySelector('[data-history]').textContent = last ? `最近成功：${new Date(last.time).toLocaleString()} · ${last.model || '未记录模型'}${last.mode === 'mock' ? '（模拟任务）' : ''}` : '尚无虚拟人物生成成功记录。';
      card.querySelector('[data-failures]').textContent = (data.recent_failures || []).map(item => item.name + '：' + item.message).join('；');
      form.elements.project_name.value = config.project_name;
      form.elements.use_storage_credentials.checked = config.use_storage_credentials;
    }
    card.querySelector('[data-test]').addEventListener('click',async event => {
      const button = event.currentTarget; button.disabled = true;
      try { status.textContent = '正在只读查询官方 AIGC 素材组…'; status.textContent = (await request('virtual/test','POST',{})).message; }
      catch (error) { status.textContent = error.message; } finally { button.disabled = false; }
    });
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
  const picker = document.getElementById('person-picker');
  if (!picker || !window.portraitPeople) return;
  const open = document.createElement('button'); open.type = 'button'; open.className = 'secondary virtual-library-open'; open.textContent = '管理虚拟人物库';
  picker.insertAdjacentElement('afterend',open);
  const dialog = document.createElement('dialog'); dialog.className = 'portrait-dialog virtual-library-dialog';
  dialog.setAttribute('aria-labelledby','virtual-library-title');
  dialog.innerHTML = `<div class="portrait-heading"><div><p class="eyebrow">OFFICIAL AIGC LIBRARY</p><h2 id="virtual-library-title">虚拟人物库</h2></div><button type="button" data-close aria-label="关闭虚拟人物库">×</button></div>
    <p>管理当前账号与项目的官方 AIGC 素材组（也可能包含商品等非人物图片）。创建人物后，选中它并在制作台上传照片；照片通过官方检查后才能使用。</p>
    <p class="portrait-small">“照片可用”和“已成功生成”分别记录。官方公用素材仅能在当前凭据可读取并核实类型、项目与状态时导入。</p>
    <p data-status role="status" aria-live="polite"></p>
    <section class="portrait-section"><h3>私有虚拟人物</h3>
      <form data-create class="virtual-create"><label for="virtual-person-name">人物名称</label><input id="virtual-person-name" name="name" maxlength="60" required placeholder="例如：短发女模特"><button class="primary" type="submit">创建官方虚拟人物</button></form>
      <div class="portrait-actions"><button type="button" class="secondary" data-sync>同步官方 AIGC 素材组</button><button type="button" class="secondary" data-check>只读检查权限</button></div>
      <div data-people class="virtual-people"></div><div data-pending class="portrait-small"></div>
    </section>
    <section class="portrait-section"><h3>导入已有官方照片</h3>
      <p>填写官方 Asset ID，或读取当前项目可用照片。导入前会重新核实 AIGC 类型、账号可访问性、项目和 Active 状态。</p>
      <form data-import class="virtual-create"><label for="virtual-asset-id">官方 Asset ID</label><input id="virtual-asset-id" name="asset" required maxlength="120" placeholder="asset-…"><button type="submit" class="secondary">核实并导入</button></form>
      <button type="button" class="secondary" data-assets>读取官方可用照片</button><div data-asset-list class="virtual-people"></div>
    </section>`;
  document.body.append(dialog);
  const status = dialog.querySelector('[data-status]'), createForm = dialog.querySelector('[data-create]');
  let working = false, createRequest = null;
  function renderPeople() {
    const list = dialog.querySelector('[data-people]'); list.replaceChildren();
    const people = window.portraitPeople.items.filter(person => person.person_type === 'AIGC');
    for (const person of people) {
      const row = document.createElement('div'); row.className = 'virtual-person';
      const text = document.createElement('span');
      text.textContent = `${person.name} · ${person.photo_count ? person.photo_count + ' 张可用照片' : '待上传照片'} · ${person.generated_count ? '已成功生成 ' + person.generated_count + ' 次' : '尚无生成成功记录'}`;
      const select = document.createElement('button'); select.type = 'button'; select.className = 'secondary'; select.textContent = '选择';
      select.addEventListener('click',() => { window.portraitPeople.choose(person.id); dialog.close(); });
      if (person.thumbnail_url) { const image = document.createElement('img'); image.src = person.thumbnail_url; image.alt = person.name; image.loading = 'lazy'; row.append(image); }
      row.append(text,select); list.append(row);
    }
    if (!people.length) list.textContent = '当前项目尚无虚拟人物。可以创建或同步官方已有素材组。';
  }
  async function refresh() {
    await window.portraitPeople.refresh(); renderPeople();
    const data = await request('virtual/status');
    const pending = data.requests.filter(item => item.status !== 'ready');
    dialog.querySelector('[data-pending]').textContent = pending.map(item => item.message).join('；');
  }
  async function run(operation) {
    if (working) return; working = true;
    dialog.querySelectorAll('button:not([data-close])').forEach(button => { button.disabled = true; });
    status.textContent = '正在处理…';
    try { await operation(); } catch (error) { status.textContent = error.message; }
    finally { working = false; dialog.querySelectorAll('button').forEach(button => { button.disabled = false; }); }
  }
  open.addEventListener('click',() => { dialog.showModal(); run(async () => { await refresh(); status.textContent = '选择虚拟人物后，在制作台添加照片；人物备注可在选择器中修改。'; }); });
  dialog.querySelector('[data-close]').addEventListener('click',() => dialog.close());
  createForm.addEventListener('submit',event => {
    event.preventDefault();
    run(async () => {
      const name = createForm.elements.name.value.trim();
      if (!name) throw new Error('请填写虚拟人物名称。');
      if (!createRequest || createRequest.name !== name) createRequest = {name,request_id:crypto.randomUUID()};
      const data = await request('people','POST',{...createRequest,person_type:'AIGC'});
      await refresh(); status.textContent = data.message;
      if (data.status === 'ready') { createForm.reset(); createRequest = null; }
    });
  });
  dialog.querySelector('[data-sync]').addEventListener('click',() => run(async () => {
    await request('people/sync','POST',{person_type:'AIGC'}); await refresh(); status.textContent = '官方 AIGC 素材组已同步；素材组也可能包含商品等非人物图片，请按用途选择。创建结果待确认的记录不会自动重复提交。';
  }));
  dialog.querySelector('[data-check]').addEventListener('click',() => run(async () => { status.textContent = (await request('virtual/test','POST',{})).message; }));
  async function importAsset(id) {
    const asset = await request('import','POST',{remote_asset_id:id,person_type:'AIGC'});
    await window.productionPortraits.importAsset(asset); await refresh(); status.textContent = '已核实官方虚拟照片并设为主参考图。'; dialog.close();
  }
  dialog.querySelector('[data-import]').addEventListener('submit',event => { event.preventDefault(); run(() => importAsset(event.currentTarget.elements.asset.value.trim())); });
  dialog.querySelector('[data-assets]').addEventListener('click',() => run(async () => {
    const data = await request('assets?person_type=AIGC'); const list = dialog.querySelector('[data-asset-list]'); list.replaceChildren();
    for (const asset of data.items) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'person-option';
      button.textContent = `${asset.name || asset.id} · 官方可用`; button.addEventListener('click',() => run(() => importAsset(asset.id))); list.append(button);
    }
    status.textContent = data.items.length ? '选择照片后会再次核实并导入。' : '当前项目无可读取的官方 AIGC 可用照片；未返回素材不代表已开通公用库权限。';
  }));
})();
