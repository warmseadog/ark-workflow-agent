(() => {
  'use strict';
  const form = document.getElementById('continuation-form');
  if (!form) return;
  const fields = form.querySelector('fieldset');
  const status = document.getElementById('continuation-status');
  let baseline = {};
  function note(message, error = false) {
    status.textContent = message;
    status.dataset.error = String(error);
    status.hidden = !message;
  }
  async function request(method = 'GET', values) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/continuation-settings', {
        method, cache: 'no-store', signal: controller.signal,
        headers: values ? {'Content-Type': 'application/json'} : {},
        body: values ? JSON.stringify(values) : undefined,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '续写配置无效，请检查填写内容。');
      return data.config;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('读取或保存续写配置超时，请重试。');
      if (error instanceof SyntaxError || error instanceof TypeError) throw new Error('无法读取服务响应，请刷新后重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function fill(config) {
    baseline = {...config};
    for (const name of ['model', 'base_url', 'timeout_seconds', 'skill']) form.elements[name].value = config[name];
    form.elements.enabled.checked = config.enabled;
    form.elements.api_key.value = '';
    form.elements.clear_api_key.checked = false;
    document.getElementById('continuation-key-status').textContent = config.has_api_key ? '已保存' : '未配置';
    document.getElementById('continuation-skill-version').textContent = config.skill_version.slice(0, 12);
    document.getElementById('continuation-skill-version').title = config.skill_version;
  }
  window.ContinuationSettings = {
    async load() {
      fill(await request());
      fields.disabled = false;
    },
  };
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled || !form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    values.enabled = form.elements.enabled.checked;
    values.clear_api_key = form.elements.clear_api_key.checked;
    values.timeout_seconds = Number(values.timeout_seconds);
    for (const key of Object.keys(values)) if (values[key] === baseline[key]) delete values[key];
    fields.disabled = true;
    note('正在保存续写配置…');
    try {
      const config = await request('PUT', values);
      fill(config);
      note('续写配置已保存，用于新任务。' + config.problem);
    } catch (error) { note(error.message, true); }
    finally { fields.disabled = false; }
  });
})();
