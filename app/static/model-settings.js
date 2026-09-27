(() => {
  const details = document.getElementById('model-settings');
  const form = document.getElementById('model-settings-form');
  const fields = document.getElementById('model-settings-fields');
  const errorNode = document.getElementById('model-settings-error');
  const status = document.getElementById('model-settings-status');
  const toggle = document.getElementById('model-settings-toggle');
  const keyButton = document.getElementById('toggle-model-key');
  const testButton = document.getElementById('test-model-connection');
  const connectionResult = document.getElementById('model-connection-result');
  const control = name => form.elements.namedItem(name);
  let saved = null;
  let presets = {};
  let saving = false;
  let testing = false;

  function showError(message) {
    errorNode.textContent = message;
    errorNode.hidden = !message;
  }
  function clearConnectionResult() {
    connectionResult.hidden = true;
    connectionResult.textContent = '';
    delete connectionResult.dataset.state;
  }
  function showConnectionResult(message, state) {
    connectionResult.textContent = message;
    connectionResult.dataset.state = state;
    connectionResult.hidden = false;
    connectionResult.scrollIntoView({block: 'nearest'});
  }
  function currentPayload() {
    const payload = {};
    for (const name of ['provider', 'protocol', 'mode', 'base_url', 'model', 'resolution', 'api_key', 'public_base_url']) payload[name] = control(name).value.trim();
    payload.duration = Number(control('duration').value);
    payload.fps = Number(control('fps').value);
    payload.clear_api_key = control('clear_api_key').checked;
    return payload;
  }
  function showKeyHint() {
    const retained = saved?.has_api_key && control('base_url').value.replace(/\/+$/, '') === saved.base_url && control('protocol').value === saved.protocol;
    document.getElementById('model-key-status').textContent = retained ? '已保存' : '尚未保存';
    control('api_key').placeholder = retained ? '留空保留已保存的 Key；输入可替换' : '填写此接口的 API Key';
  }
  function updateProtocol() {
    const automatic = control('protocol').value !== 'adapter';
    document.getElementById('model-public-base-field').hidden = control('protocol').value !== 'ark';
    control('public_base_url').disabled = control('protocol').value !== 'ark';
    control('fps').disabled = automatic;
    if (automatic) control('fps').value = '0';
    document.getElementById('model-fps-note').textContent = automatic ? '预设接口不传自定义帧率，实际输出以模型为准。' : '仅在自定义适配层支持 fps 参数时生效。';
    showKeyHint();
  }
  function updateSuggestions() {
    const options = document.getElementById('model-options');
    options.replaceChildren(...(presets[control('provider').value]?.models || []).map(value => {
      const option = document.createElement('option'); option.value = value; return option;
    }));
  }
  function fill(config) {
    for (const name of ['provider', 'protocol', 'mode', 'base_url', 'model', 'duration', 'fps', 'resolution']) control(name).value = config[name];
    control('public_base_url').value = config.public_base_url || '';
    control('api_key').value = '';
    control('api_key').type = 'password';
    control('clear_api_key').checked = false;
    keyButton.textContent = '显示';
    keyButton.setAttribute('aria-pressed', 'false');
    keyButton.setAttribute('aria-label', '显示 API Key');
    updateProtocol(); updateSuggestions();
    status.textContent = config.status === 'demo' ? '当前为本地演示，不调用视频模型。' : config.status === 'incomplete' ? '配置待补全：请填写接口地址、Key 和模型 ID。' : '配置已保存 · 可测试连接';
    const badge = document.getElementById('model-service-status');
    badge.textContent = config.status === 'demo' ? '演示模式' : config.status === 'incomplete' ? '模型待配置' : '配置已保存';
    const sourceHint = document.getElementById('model-video-source-hint');
    if (sourceHint) sourceHint.textContent = config.generation_message || (config.protocol === 'ark' ? (config.video_source === 'tos' ? '参考视频通过 TOS 上传，无需工作台公网地址。' : '参考视频通过工作台公网地址读取。') : '参考视频使用当前服务商的上传方式。');
    showError('');
    clearConnectionResult();
  }
  async function request(options, path = '/api/model-settings') {
    const response = await fetch(path, {cache: 'no-store', ...options});
    const payload = await response.json().catch(() => { throw new Error('服务响应异常，请稍后重试。'); });
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '配置请求失败，请重试。');
    return payload;
  }
  async function load() {
    try {
      const payload = await request();
      saved = payload.config; presets = payload.presets;
      fill(saved); fields.disabled = false;
    } catch (error) { showError(error.message); }
    finally { window.dispatchEvent(new Event('model-settings-loaded')); }
  }
  function close() {
    if (saving || testing) return;
    if (saved) fill(window.productionDraftModel || saved);
    if (location.pathname !== '/admin/settings') details.open = false;
    toggle.focus();
  }
  control('provider').addEventListener('change', () => {
    const preset = presets[control('provider').value];
    control('base_url').value = preset.base_url;
    control('model').value = preset.model;
    control('protocol').value = preset.protocol;
    control('mode').value = 'http';
    control('api_key').value = '';
    control('clear_api_key').checked = false;
    control('resolution').value = '720p';
    control('fps').value = '0';
    updateProtocol(); updateSuggestions();
    status.textContent = '预设已填入，可继续编辑后保存。';
  });
  control('protocol').addEventListener('change', updateProtocol);
  control('api_key').addEventListener('input', () => {
    if (control('api_key').value.trim()) control('mode').value = 'http';
  });
  control('base_url').addEventListener('input', showKeyHint);
  function markChanged() { status.textContent = '有未保存的修改'; showError(''); clearConnectionResult(); }
  form.addEventListener('input', markChanged);
  form.addEventListener('change', markChanged);
  form.addEventListener('invalid', event => {
    const advanced = event.target.closest('.model-advanced');
    if (advanced) advanced.open = true;
  }, true);
  keyButton.addEventListener('click', () => {
    const visible = control('api_key').type === 'password';
    control('api_key').type = visible ? 'text' : 'password';
    keyButton.textContent = visible ? '隐藏' : '显示';
    keyButton.setAttribute('aria-pressed', String(visible));
    keyButton.setAttribute('aria-label', visible ? '隐藏 API Key' : '显示 API Key');
  });
  document.getElementById('close-model-settings').addEventListener('click', close);
  document.getElementById('cancel-model-settings').addEventListener('click', close);
  details.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); close(); } });
  details.addEventListener('toggle', () => {
    toggle.setAttribute('aria-expanded', String(details.open));
    if (details.open && !saved) load();
    if (!details.open && saved && !saving && !testing) fill(window.productionDraftModel || saved);
  });
  testButton.addEventListener('click', async () => {
    if (saving || testing || !form.reportValidity()) return;
    const payload = currentPayload();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    testing = true; fields.disabled = true; showError('');
    testButton.textContent = '测试中…';
    testButton.setAttribute('aria-busy', 'true');
    showConnectionResult('正在测试当前配置，不会生成视频。', 'pending');
    try {
      const response = await request({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: controller.signal}, '/api/model-settings/test');
      const latency = Number.isFinite(response.latency_ms) ? `（${Math.round(response.latency_ms)} ms）` : '';
      showConnectionResult(`${response.message || (response.ok ? '连接测试通过。' : '连接测试失败。')}${latency}`, response.ok ? 'success' : 'error');
    } catch (error) {
      const message = error.name === 'AbortError' ? '连接测试超时，请检查接口地址或稍后重试。' : error instanceof TypeError ? '连接测试失败，请检查网络或接口地址后重试。' : error.message;
      showConnectionResult(message, 'error');
    } finally {
      clearTimeout(timeout);
      testing = false; fields.disabled = false; updateProtocol();
      testButton.textContent = '测试连接';
      testButton.removeAttribute('aria-busy');
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (saving || testing) return;
    const payload = currentPayload();
    saving = true; fields.disabled = true; showError(''); clearConnectionResult();
    document.getElementById('save-model-settings').textContent = '正在保存…';
    try {
      const response = await request({method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
      saved = response.config; fill(saved);
      window.dispatchEvent(new Event('model-settings-saved'));
      if (typeof toast === 'function') toast(saved.status === 'incomplete' ? '配置已保存，请补齐 Key 和模型信息' : '配置已保存到本机');
    } catch (error) { showError(error.message); }
    finally {
      saving = false; fields.disabled = false; updateProtocol();
      document.getElementById('save-model-settings').textContent = '保存配置';
    }
  });
  load();
})();
