(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const tos = byId('tos-form'), tik = byId('tikhub-form'), prompts = byId('prompt-form');
  const redaction = byId('redaction-form');
  const service = byId('redaction-service-form');
  let overview = null, templates = [], promptBaseline = '', templateId = '';
  const sectionLoads = new Map();
  function note(id, text, error = false) {
    const node = byId(id); node.textContent = text; node.dataset.error = String(error); node.hidden = !text;
  }
  async function request(url, method = 'GET', body) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), method === 'GET' ? 8000 : 90000);
    try {
      const response = await fetch(url, {method, cache:'no-store', signal:controller.signal,
        headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '配置请求失败，请检查填写内容。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请检查网络或服务状态后重试。');
      if (error instanceof SyntaxError || error instanceof TypeError) throw new Error('无法读取服务响应，请刷新页面重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function selectSection() {
    let section = location.hash.slice(1) || 'overview';
    if (!byId('section-' + section)) section = 'overview';
    document.querySelectorAll('.section').forEach(node => node.hidden = node.id !== 'section-' + section);
    document.querySelectorAll('[data-section]').forEach(node => {
      node.classList.toggle('active', node.dataset.section === section);
      if (node.dataset.section === section) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
    });
    if (section === 'model') byId('model-settings').open = true;
    const embedded = section === 'users' || section === 'my-people';
    byId('admin-section-name').textContent = byId('section-' + section).querySelector('h2')?.textContent || (section === 'users' ? '用户与任务' : '我的人物');
    document.querySelector('.content').classList.toggle('has-embedded-section', embedded);
    document.querySelectorAll('[data-admin-frame]').forEach(frame => {
      const visible = frame.closest('.section').id === 'section-' + section;
      const syncVisibility = () => {
        try {
          frame.contentWindow.adminEmbedVisible = visible;
          frame.contentWindow.dispatchEvent(new Event('admin-embed-visibility'));
        } catch (_) { /* Login redirects can leave the embedded document. */ }
      };
      if (visible && !frame.hasAttribute('src')) {
        frame.addEventListener('load', () => {
          try {
            if (frame.contentWindow.location.pathname === '/login') {
              window.location.assign(frame.contentWindow.location.href); return;
            }
            frame.contentWindow.adminEmbedVisible = !frame.closest('.section').hidden;
            frame.contentWindow.dispatchEvent(new Event('admin-embed-visibility'));
          } catch (_) { /* Keep the shell usable if a frame cannot be inspected. */ }
        });
        frame.src = frame.dataset.src;
      } else if (frame.hasAttribute('src')) syncVisibility();
    });
    void ensureSection(section);
    window.dispatchEvent(new CustomEvent('admin-section-visible', {detail:{section}}));
  }
  window.addEventListener('hashchange', selectSection);
  function info(id, rows) {
    const node = byId(id); node.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = label; dd.textContent = String(value ?? '—'); row.append(dt, dd); node.append(row);
    }
  }
  function fillRedaction(config) {
    for (const [name, value] of Object.entries(config)) {
      const input = redaction.elements.namedItem(name);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = value;
      else input.value = value ?? '';
    }
    redactionLimits();
  }
  function serviceMode() {
    const external = service.elements.mode.value === 'http';
    byId('redaction-api-fields').hidden = !external;
    service.elements.endpoint.required = external;
    service.elements.endpoint.disabled = !external;
  }
  function fillService(config) {
    for (const name of ['mode','endpoint','timeout_seconds']) service.elements[name].value = config[name];
    service.elements.api_key.value = '';
    service.elements.clear_api_key.checked = false;
    byId('redaction-key-status').textContent = config.has_api_key ? '已保存' : '未配置';
    serviceMode();
  }
  service.elements.mode.addEventListener('change', serviceMode);
  service.addEventListener('submit', event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(service));
    payload.timeout_seconds = Number(payload.timeout_seconds);
    payload.clear_api_key = service.elements.clear_api_key.checked;
    operate(service, 'redaction-service-status', '正在保存…', async () => {
      const {config} = await request('/api/redaction-service', 'PUT', payload);
      fillService(config);
      note('redaction-service-status', config.mode === 'local' ? '已使用本地打码。' : '已使用外部 API，新的打码处理将调用此接口。');
    });
  });
  function redactionLimits() {
    const hair = redaction.elements.mask_mode.value !== 'face';
    for (const option of redaction.elements.blur_style.options) option.disabled = hair && option.value !== 'mosaic';
    if (hair) redaction.elements.blur_style.value = 'mosaic';
  }
  redaction.elements.mask_mode.addEventListener('change', redactionLimits);
  redaction.addEventListener('submit', event => {
    event.preventDefault();
    const values = {};
    redaction.querySelectorAll('input,select').forEach(input => {
      values[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.name === 'detection_size' ? (input.value ? Number(input.value) : null) : input.value;
    });
    operate(redaction, 'redaction-status', '正在保存…', async () => {
      const data = await request('/api/redaction-settings', 'PUT', values);
      fillRedaction(data.config);
      note('redaction-status', '打码设置已保存，新建草稿自动使用；已有草稿和任务保持原参数。');
      await refreshOverview();
    });
  });
  async function refreshOverview() {
    try {
      overview = await request('/api/admin/overview');
      const {model,storage,tikhub,system,redaction} = overview;
      byId('overview-model').textContent = model.mode === 'mock' ? '本地演示模式' : (model.has_api_key ? '模型已配置' : '待填写 API Key');
      byId('overview-model-detail').textContent = model.model;
      byId('overview-tikhub').textContent = tikhub.has_api_key ? '密钥已保存' : '等待配置';
      byId('overview-storage').textContent = storage.ready ? '已启用 TOS' : storage.enabled ? '配置待补全' : '尚未启用';
      byId('overview-storage-detail').textContent = storage.bucket || '上传打码视频，生成临时访问链接';
      note('overview-attention', model.generation_message || (model.mode === 'mock' ? '当前为演示模式，可在视频模型中切换真实生成。' : ''));
      info('system-info', [['访问范围',system.access],['存储目录',system.storage_dir],['上传大小上限',system.max_upload_mb + ' MB'],['模型轮询间隔',system.seedance_poll_seconds + ' 秒'],['通用下载 Cookie',system.cookie_configured ? '已配置' : '未配置'],['配置生效','服务配置保存后用于新任务']]);
      note('page-error','');
      return true;
    } catch (error) { note('page-error',error.message,true); return false; }
  }
  byId('refresh-overview').addEventListener('click',refreshOverview);
  window.addEventListener('model-settings-saved',refreshOverview);
  function fillTos(config) {
    for (const name of ['region','endpoint','bucket','prefix','expires_seconds']) {
      const input = tos.elements.namedItem(name);
      if (name === 'expires_seconds' && ![...input.options].some(o => o.value === String(config[name]))) {
        const option = document.createElement('option'); option.value = config[name]; option.textContent = config[name] + ' 秒（已保存）'; input.append(option);
      }
      input.value = config[name];
    }
    tos.elements.enabled.checked = config.enabled;
    tos.elements.clear_credentials.checked = false;
    for (const [name, id] of [['access_key','tos-ak-status'],['secret_key','tos-sk-status']]) {
      const input = tos.elements.namedItem(name); input.value = '';
      input.placeholder = config['has_' + name] ? '已保存，留空保留；输入可替换' : '请输入 ' + (name === 'access_key' ? 'Access Key' : 'Secret Key');
      byId(id).textContent = config['has_' + name] ? '已保存' : '未配置';
    }
  }
  function tosPayload() {
    const data = Object.fromEntries(new FormData(tos));
    data.enabled = tos.elements.enabled.checked;
    data.clear_credentials = tos.elements.clear_credentials.checked;
    data.expires_seconds = Number(data.expires_seconds);
    return data;
  }
  tos.elements.region.addEventListener('change', () => {
    tos.elements.endpoint.value = 'https://tos-' + tos.elements.region.value.trim() + '.volces.com';
    note('tos-status','地域已修改，请核对 Endpoint 并填写对应的 AK/SK，保存后生效。');
  });
  async function operate(form, statusId, message, action) {
    const fields = form.querySelector('fieldset');
    if (fields.disabled) return;
    fields.disabled = true; note(statusId,message);
    try { await action(); } catch(error) { note(statusId,error.message,true); }
    finally { fields.disabled = false; }
  }
  tos.addEventListener('submit', event => {
    event.preventDefault();
    const payload = tosPayload();
    if (payload.clear_credentials && !confirm('确认清除 TOS 的 AK 和 SK？新的 TOS 上传任务将不可用。')) return;
    operate(tos,'tos-status','正在保存…',async () => {
      const data = await request('/api/storage-settings','PUT',payload); fillTos(data.config);
      note('tos-status',data.config.ready ? 'TOS 配置已保存并启用，将用于新的方舟生成任务。' : '配置已保存。' + data.config.message);
      await refreshOverview();
    });
  });
  byId('test-tos').addEventListener('click', () => {
    if (!tos.reportValidity()) return; const payload = tosPayload();
    operate(tos,'tos-status','正在验证 Bucket 访问权限…',async () => {
      const data = await request('/api/storage-settings/test','POST',payload); note('tos-status',data.message,!data.ok);
    });
  });
  function tikPayload() { return {api_key:tik.elements.api_key.value.trim(),clear_api_key:tik.elements.clear_api_key.checked}; }
  function fillTik(data) {
    byId('tikhub-key-status').textContent = data.has_api_key ? '已保存' : '未配置';
    tik.elements.api_key.placeholder = data.has_api_key ? '已保存，留空保留；输入新密钥可替换' : '填写 TikHub API Key';
    tik.elements.api_key.value = ''; tik.elements.clear_api_key.checked = false;
  }
  tik.addEventListener('submit',event => {
    event.preventDefault(); const payload = tikPayload();
    if (payload.clear_api_key && !confirm('确认清除 TikHub 密钥？')) return;
    operate(tik,'tikhub-status','正在保存…',async () => {
      fillTik(await request('/api/link-settings','PUT',payload));
      note('tikhub-status', payload.clear_api_key ? '密钥已清除。' : 'TikHub 配置已保存，制作页将直接使用此密钥。'); await refreshOverview();
    });
  });
  byId('test-tikhub').addEventListener('click', () => {
    const payload = tikPayload();
    operate(tik,'tikhub-status','正在验证 TikHub 账户…',async () => {
      const data = await request('/api/link-settings/test','POST',payload); note('tikhub-status',data.message,!data.ok);
    });
  });
  const promptValue = () => JSON.stringify([prompts.elements.namedItem('name').value,prompts.elements.content.value]);
  function fillPrompt(id) {
    const selected = templates.find(item => item.id === id);
    templateId = selected?.id || ''; byId('admin-template-select').value = templateId;
    prompts.elements.namedItem('name').value = selected?.name || ''; prompts.elements.content.value = selected?.content || '';
    byId('delete-template').disabled = !selected; promptBaseline = promptValue();
  }
  async function loadTemplates(selected = '') {
    templates = (await request('/api/admin/prompt-templates')).items;
    const select = byId('admin-template-select'); select.replaceChildren();
    const blank = document.createElement('option'); blank.value=''; blank.textContent='新建模板'; select.append(blank);
    templates.forEach(item => { const option = document.createElement('option'); option.value=item.id; option.textContent=item.name; select.append(option); });
    fillPrompt(selected);
  }
  byId('admin-template-select').addEventListener('change',event => {
    if (promptValue() !== promptBaseline && !confirm('有未保存的模板修改，确认切换？')) { event.target.value=templateId; return; }
    fillPrompt(event.target.value); note('prompt-status','');
  });
  byId('new-template').addEventListener('click',() => {
    if (promptValue() !== promptBaseline && !confirm('有未保存的模板修改，确认新建？')) return;
    fillPrompt(''); prompts.elements.namedItem('name').focus();
  });
  prompts.addEventListener('submit',event => {
    event.preventDefault(); const payload={name:prompts.elements.namedItem('name').value,content:prompts.elements.content.value};
    operate(prompts,'prompt-status','正在保存模板…',async () => {
      const data = await request('/api/admin/prompt-templates' + (templateId ? '/' + encodeURIComponent(templateId) : ''),templateId ? 'PUT' : 'POST',payload);
      await loadTemplates(data.id); note('prompt-status','模板已保存，制作页刷新后可选用。');
    });
  });
  byId('delete-template').addEventListener('click',() => {
    if (!templateId || !confirm('确认删除此提示词模板？')) return;
    operate(prompts,'prompt-status','正在删除…',async () => { await request('/api/admin/prompt-templates/' + encodeURIComponent(templateId),'DELETE'); await loadTemplates(); note('prompt-status','模板已删除。'); });
  });
  const sectionRequests = {
    continuation: ['continuation-status', async () => window.ContinuationSettings.load()],
    overview: ['page-error', refreshOverview],
    system: ['page-error', async () => overview ? true : refreshOverview()],
    redaction: ['redaction-status', async () => {
      const serviceData = await request('/api/redaction-service'); fillService(serviceData.config);
      service.querySelector('fieldset').disabled = false;
      const data = await request('/api/redaction-settings'); fillRedaction(data.config);
      redaction.querySelector('fieldset').disabled = false;
    }],
    storage: ['tos-status', async () => { fillTos((await request('/api/storage-settings')).config); tos.querySelector('fieldset').disabled = false; }],
    tikhub: ['tikhub-status', async () => { fillTik(await request('/api/link-settings')); tik.querySelector('fieldset').disabled = false; }],
    prompts: ['prompt-status', async () => { await loadTemplates(); prompts.querySelector('fieldset').disabled = false; }],
  };
  async function ensureSection(section) {
    if (!sectionRequests[section] || sectionLoads.has(section)) return;
    const [statusId, load] = sectionRequests[section];
    sectionLoads.set(section, true);
    note(statusId, '正在读取…');
    try {
      if (await load() === false) throw new Error(byId(statusId).textContent || '配置暂时无法读取。');
      note(statusId, '');
    } catch (error) {
      sectionLoads.delete(section); note(statusId, error.message, true);
      const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重试加载';
      retry.addEventListener('click', () => void ensureSection(section));
      byId(statusId).append(document.createTextNode(' '), retry);
    }
  }
  selectSection();
})();
