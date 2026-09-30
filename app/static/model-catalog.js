(() => {
  const form = document.getElementById('model-catalog-form');
  if (!form) return;
  const list = document.getElementById('model-catalog-list');
  const status = document.getElementById('model-catalog-status');
  let rows = [];
  let loaded = false, loading = false;
  let saving = false;
  const saveButton = document.getElementById('save-model-catalog');
  saveButton.disabled = true;
  function report(message, error = false) { status.textContent = message; status.dataset.error = String(error); }
  async function request(method = 'GET', body) {
    const controller = new AbortController();
    const timer = method === 'GET' ? setTimeout(() => controller.abort(), 8000) : null;
    try {
    const response = await fetch('/api/model-catalog', {method,cache:'no-store',signal:method === 'GET' ? controller.signal : undefined,headers:body ? {'Content-Type':'application/json'} : {},body:body ? JSON.stringify(body) : undefined});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '模型目录保存失败');
    return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('读取模型目录超时，请重试。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function render(data) {
    rows = data.items; list.replaceChildren();
    for (const item of rows) {
      const card = document.createElement('div'); card.className = 'catalog-model'; card.dataset.catalogId = item.id;
      const title = document.createElement('strong'); title.textContent = item.label + (data.default_model === item.id ? ' · 当前默认' : '');
      const id = document.createElement('code'); id.textContent = item.id;
      card.append(title, id);
      for (const [key,label,type] of [['label','显示名称','text'],['order','排序','number'],['verified','已完成此账号的实际生成验收','checkbox'],['enabled','在制作页开放','checkbox']]) {
        const field = document.createElement('label'); field.className = type === 'checkbox' ? 'catalog-check' : '';
        const input = document.createElement('input'); input.type = type; input.dataset.field = key;
        if (type === 'checkbox') input.checked = item[key]; else input.value = item[key];
        if (type === 'number') { input.min = 0; input.max = 100; }
        if (type === 'text') { input.required = true; input.maxLength = 60; }
        field.append(input, document.createTextNode(label)); card.append(field);
      }
      const note = document.createElement('p'); note.className = 'hint';
      note.textContent = `${item.resolutions.join(' / ')} · ${item.follow_source ? '编辑时跟随原视频' : '生成 4–'+item.max_duration+' 秒'} · 参考视频合计 ≤ ${item.max_video_seconds} 秒`;
      card.append(note); list.append(card);
    }
  }
  async function load() {
    if (loaded || loading || saving) return;
    loading = true;
    try { render(await request()); loaded = true; report('新增模型需先完成账号验收，再开放给制作页。'); }
    catch (error) {
      report(error.message, true);
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重试加载';
      retry.addEventListener('click', load); status.append(document.createTextNode(' '), retry);
    } finally { loading = false; saveButton.disabled = !loaded; }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!loaded || loading || saving) return;
    const items = [...list.children].map(card => ({...rows.find(row => row.id === card.dataset.catalogId),
      ...Object.fromEntries([...card.querySelectorAll('[data-field]')].map(input => [input.dataset.field,input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value]))}));
    saving = true; saveButton.disabled = true;
    list.querySelectorAll('input').forEach(input => input.disabled = true);
    try { render(await request('PUT', {items})); report('模型目录已保存。制作页刷新后读取最新列表。'); }
    catch (error) { report(error.message, true); }
    finally { saving = false; saveButton.disabled = false; list.querySelectorAll('input').forEach(input => input.disabled = false); }
  });
  window.addEventListener('model-settings-saved', () => { loaded = false; void load(); });
  window.addEventListener('admin-section-visible', event => { if (event.detail.section === 'model') void load(); });
  if (location.pathname !== '/admin/settings') load();
})();
