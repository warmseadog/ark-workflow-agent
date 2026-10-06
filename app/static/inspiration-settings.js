(() => {
  'use strict';
  const form = document.getElementById('inspiration-settings-form');
  if (!form) return;
  const fields = form.querySelector('fieldset'), status = document.getElementById('inspiration-settings-status');
  const retry = document.getElementById('inspiration-settings-reload');
  function note(text, error = false) {
    status.textContent = text; status.hidden = !text; status.dataset.error = String(error);
  }
  function mode() {
    const inherited = form.elements.inherit_provider.checked;
    form.querySelector('[data-inspiration-provider]').hidden = inherited;
    for (const name of ['base_url', 'api_key', 'clear_api_key']) form.elements[name].disabled = inherited;
    form.elements.model.required = !inherited;
    form.elements.base_url.required = !inherited;
  }
  function fill(config) {
    for (const name of ['model', 'base_url', 'timeout_seconds', 'max_tokens']) form.elements[name].value = config[name];
    for (const name of ['enabled', 'inherit_provider']) form.elements[name].checked = config[name];
    form.elements.api_key.value = ''; form.elements.clear_api_key.checked = false; mode();
    document.getElementById('inspiration-config-info').textContent =
      `实际模型：${config.effective_model || '未配置'}；密钥：${config.has_api_key ? '已配置' : '未配置'}；Skill 版本：${config.skill_version.slice(0, 12)}。${config.problem}`;
  }
  async function request(method = 'GET', values) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/inspiration-settings', {method, cache: 'no-store', signal: controller.signal,
        headers: values ? {'Content-Type': 'application/json'} : {}, body: values ? JSON.stringify(values) : undefined});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '辅助配置请求失败');
      return data.config;
    } finally { clearTimeout(timer); }
  }
  async function load() {
    fields.disabled = true; retry.hidden = true;
    try { fill(await request()); fields.disabled = false; note(''); }
    catch (error) { note('无法加载灵感辅助配置，请重试。', true); retry.hidden = false; }
  }
  form.elements.inherit_provider.addEventListener('change', mode);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled || !form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    values.enabled = form.elements.enabled.checked; values.inherit_provider = form.elements.inherit_provider.checked;
    if (!values.inherit_provider) values.clear_api_key = form.elements.clear_api_key.checked;
    values.timeout_seconds = Number(values.timeout_seconds); values.max_tokens = Number(values.max_tokens);
    fields.disabled = true; note('正在保存…');
    try { fill(await request('PUT', values)); note('灵感辅助配置已保存。'); }
    catch (error) { note(error.name === 'AbortError' ? '保存超时，请重新加载核对结果。' : error.message, true); }
    finally { fields.disabled = false; }
  });
  retry.addEventListener('click', load);
  window.InspirationSettings = {load};
})();
