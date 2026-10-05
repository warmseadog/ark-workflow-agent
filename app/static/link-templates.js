(() => {
  const form = document.getElementById('studio-generate-form');
  if (!form) return;
  const byId = id => document.getElementById(id);
  const prompt = byId('generation-prompt'), name = byId('prompt-template-name');
  const list = byId('prompt-template-list'), templateStatus = byId('prompt-template-status');
  const source = document.querySelector('[name="video_url"]');
  const key = byId('tikhub-api-key'), keyStatus = byId('link-settings-status');
  let configured = false, inspectVersion = 0, timer;
  const busy = () => form.getAttribute('aria-busy') === 'true';
  function message(element, text, error = false) { if (element) { element.textContent=text; element.dataset.error=String(error); } }
  async function request(url, method='GET', body) {
    const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json();
    if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'请求失败，请重试。');
    return data;
  }
  if (prompt && name) {
  let items = [], selected = null, saving = false, loaded = false;
  const draftKey = 'production-prompt-draft-v1';
  let baseline = {name:name.value, content:prompt.value};
  const reusablePrompt = () => window.productionPrompt?.strip(prompt.value) ?? prompt.value;
  const dirty = () => name.value !== baseline.name || reusablePrompt() !== baseline.content;
  const busy = () => form.getAttribute('aria-busy') === 'true';
  function message(element, text, error = false) {
    element.textContent = text; element.dataset.error = String(error);
  }
  async function request(url, method = 'GET', body) {
    const response = await fetch(url, {method, headers: body ? {'Content-Type':'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('服务响应异常，请稍后重试。'); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '保存失败，请检查名称和内容长度后重试。');
    return data;
  }
  function stash(sync = false) {
    if (window.productionSession) { window.dispatchEvent(new CustomEvent('production-prompt-changed', {detail:{applyTemplate:sync === true}})); return; }
    try { localStorage.setItem(draftKey, JSON.stringify({selected, name:name.value, content:prompt.value})); }
    catch (_) { message(templateStatus, '浏览器无法保留草稿；点击保存仍可将模板存到本机服务端。'); }
  }
  function controls() {
    document.querySelectorAll('[data-template-action]').forEach(button => {
      button.disabled = busy() || saving || !loaded || (['template-save', 'template-delete', 'template-delete-accept'].includes(button.id) && (!selected || items.find(item=>item.id===selected)?.read_only));
    });
    list.querySelectorAll('button').forEach(button => button.disabled = busy() || saving);
    name.disabled = prompt.disabled = busy() || saving;
  }
  function renderList() {
    list.replaceChildren();
    if (!items.length) {
      const text = document.createElement('span'); text.className = 'field-hint';
      text.textContent = '还没有模板，编辑提示词后可以保存第一份。'; list.append(text);
    }
    items.forEach(item => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = item.name;
      button.dataset.promptTemplate = item.id;
      button.setAttribute('aria-pressed', String(selected === item.id));
      button.addEventListener('click', () => {
        if (busy() || saving) return;
        if (dirty() && !confirm('当前提示词有未保存的修改，确认切换模板？')) return;
        selected = item.id; name.value = item.name; prompt.value = item.content;
        window.productionPrompt?.select(item);
        baseline = {name:item.name, content:item.content};
        byId('template-delete-confirm').hidden = true;
        stash(true); renderList(); message(templateStatus, item.read_only ? '已应用系统模板；修改后可另存为个人模板。' : '已应用模板，可编辑名称和提示词后保存修改。');
      });
      list.append(button);
    });
    controls();
  }
  name.addEventListener('input', stash);
  prompt.addEventListener('input', stash);
  new MutationObserver(() => {
    controls();
    if (busy()) { clearTimeout(timer); ++inspectVersion; }
  }).observe(form, {attributes:true, attributeFilter:['aria-busy']});
  byId('template-new').addEventListener('click', () => {
    if (busy() || saving) return;
    selected = null; name.value = ''; byId('template-delete-confirm').hidden = true;
    window.productionPrompt?.select({rule_version:window.productionPrompt.ruleVersion});
    stash(true); renderList(); name.focus();
    message(templateStatus, '输入模板名称，编辑下方提示词，再点击“另存为新模板”。');
  });
  async function save(asNew) {
    if (busy() || saving || !loaded) return;
    if (!name.value.trim() || !reusablePrompt().trim()) { message(templateStatus, '请填写模板名称和提示词。', true); return; }
    if (name.value.trim().length > 60 || reusablePrompt().trim().length > 10000) { message(templateStatus, '名称最多 60 字，提示词最多 10000 字。', true); return; }
    saving = true; controls();
    try {
      const url = asNew ? '/api/prompt-templates' : '/api/prompt-templates/' + encodeURIComponent(selected);
      const item = await request(url, asNew ? 'POST' : 'PUT', {name:name.value.trim(), content:reusablePrompt().trim(),rule_version:window.productionPrompt?.ruleVersion || 'legacy-v1'});
      if (asNew) items.push(item); else items = items.map(old => old.id === item.id ? item : old);
      selected = item.id; name.value = item.name; prompt.value = item.content;
      window.productionPrompt?.select(item);
      baseline = {name:item.name, content:item.content};
      stash(true); renderList(); message(templateStatus, '已保存到本机，刷新页面或重启服务后仍可使用。');
    } catch (error) { message(templateStatus, error.message, true); }
    finally { saving = false; controls(); }
  }
  byId('template-save').addEventListener('click', () => save(false));
  byId('template-copy').addEventListener('click', () => save(true));
  byId('template-delete').addEventListener('click', () => { byId('template-delete-confirm').hidden = false; });
  byId('template-delete-cancel').addEventListener('click', () => { byId('template-delete-confirm').hidden = true; });
  byId('template-delete-accept').addEventListener('click', async () => {
    if (busy() || saving || !selected) return;
    saving = true; controls();
    try {
      await request('/api/prompt-templates/' + encodeURIComponent(selected), 'DELETE');
      items = items.filter(item => item.id !== selected); selected = null; name.value = '';
      window.productionPrompt?.select({rule_version:window.productionPrompt.ruleVersion});
      byId('template-delete-confirm').hidden = true;
      stash(true); renderList(); message(templateStatus, '模板已删除；下方当前提示词仍可继续编辑或使用。');
    } catch (error) { message(templateStatus, error.message, true); }
    finally { saving = false; controls(); }
  });
  request('/api/prompt-templates').then(data => {
    items = data.items; loaded = true;
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
      if (!window.productionSession && draft && typeof draft.content === 'string' && typeof draft.name === 'string') {
        prompt.value = draft.content; name.value = draft.name;
        selected = items.some(item => item.id === draft.selected) ? draft.selected : null;
        const saved = items.find(item => item.id === selected);
        if (saved) baseline = {name:saved.name, content:saved.content};
      }
    } catch (_) {}
    renderList(); message(templateStatus, '选择模板可应用；也可以编辑下方提示词并另存为新模板。');
  }).catch(error => message(templateStatus, '模板读取失败：' + error.message + ' 刷新页面可重试。', true)).finally(() => window.dispatchEvent(new Event('production-templates-ready')));
  controls();
  window.addEventListener('production-prompt-restored', event => {
    const item = items.find(item => item.id === event.detail.prompt_template_id);
    selected = item?.id || null; name.value = item?.name || '';
    baseline = {name:name.value,content:item?.content ?? reusablePrompt()};
    byId('template-delete-confirm').hidden = true;
    renderList();
  });
  form.addEventListener('submit', event => {
    if (saving) { event.preventDefault(); event.stopImmediatePropagation(); message(templateStatus, '模板正在保存，请稍候再生成。'); }
  }, true);

  } else { queueMicrotask(() => window.dispatchEvent(new Event('production-templates-ready'))); }

  function keyState(data) {
    configured = data.has_api_key;
    byId('link-key-state').textContent = configured ? '已配置' : '待配置';
    key.placeholder = configured ? '已保存，留空保留；输入新密钥可替换' : '输入 TikHub API Key';
  }
  request('/api/link-settings').then(keyState).catch(error => message(keyStatus, error.message, true));
  async function saveKey(clear = false) {
    if (busy()) return;
    const save = byId('save-tikhub-key'), remove = byId('clear-tikhub-key');
    if (clear && !confirm('确认清除本机保存的 TikHub 密钥？')) return;
    save.disabled = remove.disabled = true;
    try {
      keyState(await request('/api/link-settings', 'PUT', {api_key:key.value, clear_api_key:clear}));
      key.value = ''; message(keyStatus, clear ? '密钥已清除。' : '配置已保存。解析视频时会验证密钥是否有效。');
      if (source.value.trim()) inspect();
    } catch (error) { message(keyStatus, error.message, true); }
    finally { save.disabled = remove.disabled = false; }
  }
  byId('test-tikhub-key')?.addEventListener('click', async () => {
    if (busy()) return;
    const button = byId('test-tikhub-key');
    const save = byId('save-tikhub-key'), remove = byId('clear-tikhub-key');
    const payload = {api_key:key.value};
    button.disabled = save.disabled = remove.disabled = true;
    message(keyStatus, '正在验证 TikHub 账户…');
    try {
      const data = await request('/api/link-settings/test', 'POST', payload);
      message(keyStatus, data.message, !data.ok);
    } catch(error) { message(keyStatus, error.message, true); }
    finally { button.disabled = save.disabled = remove.disabled = false; }
  });
  window.addEventListener('focus', () => {
    request('/api/link-settings').then(keyState).catch(() => {});
  });
  byId('save-tikhub-key').addEventListener('click', () => saveKey(false));
  byId('clear-tikhub-key').addEventListener('click', () => saveKey(true));
  key.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); saveKey(false); } });
  async function inspect() {
    if (busy()) return;
    const version = ++inspectVersion, text = source.value.trim(), status = byId('video-link-status');
    if (!text) { message(status, ''); return; }
    message(status, '正在识别链接…');
    try {
      const data = await request('/api/video-link/inspect', 'POST', {text});
      if (busy() || version !== inspectVersion || source.value.trim() !== text) return;
      keyState({has_api_key:data.configured});
      message(status, data.platform
        ? '已识别' + data.label + '链接。' + (configured ? '点击“导入视频”即可加载。' : '链接解析服务尚未配置，请联系管理员。')
        : '已识别其他链接，点击“导入视频”尝试导入。');
    } catch (error) {
      if (!busy() && version === inspectVersion && source.value.trim() === text) message(status, error.message, true);
    }
  }
  source.addEventListener('input', () => { clearTimeout(timer); ++inspectVersion; timer = setTimeout(inspect, 450); });
})();
